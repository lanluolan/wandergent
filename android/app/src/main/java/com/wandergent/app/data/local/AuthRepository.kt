package com.wandergent.app.data.local

import com.wandergent.app.data.AuthApi
import com.wandergent.app.data.CredentialsDto
import com.wandergent.app.data.EmailChangeDto
import com.wandergent.app.data.VerifyConfirmDto
import com.wandergent.app.data.ResetConfirmDto
import com.wandergent.app.data.ResetRequestDto
import com.wandergent.app.data.ApiJson
import com.wandergent.app.data.Network
import com.wandergent.app.data.retryAfterSeconds
import com.wandergent.app.data.tooManyRequests
import com.wandergent.app.data.SessionDto
import java.io.IOException
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flowOf
import retrofit2.HttpException

sealed interface AuthResult {
    data class Success(val user: UserEntity) : AuthResult

    data class Failure(val message: String) : AuthResult
}

/**
 * Accounts, verified by the server.
 *
 * **This used to be a local placeholder** -- a SHA-256 hash in Room, and every request
 * carrying whichever `user_id` the app felt like sending. That was fine while the only
 * client was this phone over `adb reverse`, and worthless the moment two people can reach
 * the same server: anyone could post as anyone.
 *
 * Now the server verifies the password (PBKDF2) and issues a bearer token, and that token
 * is the only thing that establishes who is asking. Nothing in this app claims an identity
 * any more -- the request bodies no longer even have a field for one.
 *
 * The local [UserEntity] row survives as a **storage partition**: the library, the
 * transcript and the currency setting are keyed on its `Long` id. It holds no credentials.
 *
 * **Offline behaviour**: signing in needs the network, staying signed in does not. The
 * token and the local id live in [SessionStore], so a traveller who is already signed in
 * keeps their library on a plane.
 */
class AuthRepository(
    private val userDao: UserDao,
    private val sessionStore: SessionStore,
    private val api: AuthApi = Network.authApi,
) {

    /** The signed-in user, or null. Re-reads if the account row changes. */
    val currentUser: Flow<UserEntity?> =
        sessionStore.currentUserId.flatMapLatest { userId ->
            if (userId == null) flowOf(null) else userDao.observeById(userId)
        }

    suspend fun register(
        username: String,
        password: String,
        displayName: String,
        email: String,
    ): AuthResult {
        val name = username.trim()
        // Checked here as well as on the server so the common typo does not cost a round
        // trip. The server's copy is the one that matters -- this one is a courtesy.
        when {
            name.length < 3 -> return AuthResult.Failure("Username must be at least 3 characters")
            password.length < 8 -> return AuthResult.Failure("Password must be at least 8 characters")
        }
        return attempt {
            api.register(CredentialsDto(name, password, displayName.trim(), email.trim()))
        }
    }

    suspend fun login(username: String, password: String): AuthResult =
        attempt { api.login(CredentialsDto(username.trim(), password)) }

    /**
     * End the session on the server as well as here.
     *
     * The server call is best-effort: if it fails the local token is still discarded, and
     * a token nobody holds is a token nobody uses. Refusing to sign out because the
     * network is down would be the worse failure.
     */
    suspend fun logout() {
        runCatching { api.logout() }
        forget()
    }

    /**
     * Drop the session here without telling the server.
     *
     * For the case where the server already told *us*: calling `/auth/logout` with a
     * token it has just rejected would only earn a second rejection.
     */
    suspend fun forget() {
        Network.authToken = null
        sessionStore.signOut()
    }

    /**
     * Ask for a reset code.
     *
     * **Reports success even for an address with no account**, because the server answers
     * identically either way and it does so on purpose: telling them apart would make this
     * a way to ask whether someone has an account here. A client that "helpfully" said
     * "no such address" would hand back exactly what the server withheld.
     */
    suspend fun requestReset(email: String): AuthResult =
        try {
            api.requestReset(ResetRequestDto(email.trim()))
            AuthResult.Success(
                UserEntity(username = "", displayName = "", createdAt = 0),
            )
        } catch (e: HttpException) {
            AuthResult.Failure(describe(e))
        } catch (e: IOException) {
            AuthResult.Failure(unreachable(e))
        }

    /** Set a new password from a code. */
    suspend fun confirmReset(code: String, password: String): AuthResult =
        try {
            api.confirmReset(ResetConfirmDto(code.trim().uppercase(), password))
            AuthResult.Success(
                UserEntity(username = "", displayName = "", createdAt = 0),
            )
        } catch (e: HttpException) {
            AuthResult.Failure(
                if (e.code() == 400) {
                    "That code is not valid, or it has expired. Ask for a new one."
                } else {
                    describe(e)
                }
            )
        } catch (e: IOException) {
            AuthResult.Failure(unreachable(e))
        }

    /** Prove the address with a code from the email. */
    suspend fun verifyEmail(code: String): AuthResult = act { api.confirmVerification(VerifyConfirmDto(code.trim().uppercase())) }

    /** Send another confirmation code to the address already on the account. */
    suspend fun resendVerification(): AuthResult = act { api.resendVerification() }

    /** Point the account at a new address. It always starts unproven. */
    suspend fun changeEmail(email: String): AuthResult = act { api.changeEmail(EmailChangeDto(email.trim())) }

    /**
     * Run a call that changes the account, then refresh the cached row from the server.
     *
     * Refreshing rather than guessing: the flag the profile draws is the server's answer,
     * and a client that predicted it would show "verified" for a code the server refused.
     */
    private suspend fun act(call: suspend () -> Unit): AuthResult =
        try {
            call()
            cache(api.me())
            AuthResult.Success(UserEntity(username = "", displayName = "", createdAt = 0))
        } catch (e: HttpException) {
            AuthResult.Failure(
                if (e.code() == 400) {
                    "That code is not valid, or it has expired. Ask for a new one."
                } else {
                    describe(e)
                }
            )
        } catch (e: IOException) {
            AuthResult.Failure(unreachable(e))
        }

    /**
     * Re-attach a stored session on launch, or discard it if the server disowns it.
     *
     * Called once at startup. Without this a token that expired or was revoked keeps
     * being sent until something happens to fail, and the failure lands on whatever the
     * traveller happened to tap.
     */
    suspend fun restore() {
        val token = sessionStore.token()
        if (token == null) {
            // Signed in locally with no token: a session from before accounts moved
            // server-side. It would otherwise present as signed in while every write
            // 401s, which is a worse state than being asked to sign in again. Clearing
            // the session keeps the local row, so signing in with the same username
            // re-attaches the library rather than starting an empty one.
            if (sessionStore.currentUserId.first() != null) sessionStore.signOut()
            return
        }
        Network.authToken = token
        try {
            // The answer is *used*, not just awaited. It was discarded here, so the cached
            // email and verified flag went stale the moment the account changed anywhere
            // else -- and the profile would then say "Confirmed" about an address the
            // server considers unproven, telling someone they are recoverable when they
            // are not.
            cache(api.me())
        } catch (e: HttpException) {
            if (e.code() == 401) {
                Network.authToken = null
                sessionStore.signOut()
            }
        } catch (_: IOException) {
            // Offline. Keep the session: it may well still be valid, and signing someone
            // out because their train went into a tunnel loses their library for no
            // reason. An actually-dead token gets cleared on the next 401.
        }
    }

    /** FastAPI's `{"detail": ...}` body, if the response carried one. */
    private fun detail(e: HttpException): String? =
        ApiJson.parseErrorDetail(e.response()?.errorBody()?.string())

    private suspend fun attempt(call: suspend () -> SessionDto): AuthResult =
        try {
            adopt(call())
        } catch (e: HttpException) {
            AuthResult.Failure(describe(e))
        } catch (e: IOException) {
            AuthResult.Failure(unreachable(e))
        }

    private fun describe(e: HttpException): String =
        when (e.code()) {
            429 -> tooManyRequests(detail(e), e.retryAfterSeconds())
            401 -> "Wrong username or password"
            409 -> "That username is taken"
            422 -> detail(e) ?: "Check what you entered and try again"
            else -> "Request failed (HTTP ${e.code()})"
        }

    private fun unreachable(e: IOException): String =
        "Cannot reach the server. Signing in needs a connection; " +
            "an existing session does not. (${e.javaClass.simpleName})"

    /**
     * Bind a server session to a local row, reusing one that matches the username.
     *
     * Reuse rather than always-insert is what keeps a returning user's saved trips: the
     * library is keyed on the local row id, so a second row would silently present an
     * empty library to someone who has plans saved. It also adopts rows written before
     * accounts moved server-side, which have no `serverAccountId` at all.
     */
    /** Write the server's answer into the local row, so the profile is never guessing. */
    private suspend fun cache(account: com.wandergent.app.data.AccountDto) {
        val local = userDao.findByUsername(account.username) ?: return
        userDao.linkToAccount(
            local.id,
            account.id,
            account.displayName,
            account.email,
            account.emailVerified,
        )
    }

    private suspend fun adopt(session: SessionDto): AuthResult {
        Network.authToken = session.token
        val existing = userDao.findByUsername(session.account.username)
        val id = if (existing != null) {
            userDao.linkToAccount(
                existing.id,
                session.account.id,
                session.account.displayName,
                session.account.email,
                session.account.emailVerified,
            )
            existing.id
        } else {
            userDao.insert(
                UserEntity(
                    username = session.account.username,
                    displayName = session.account.displayName,
                    serverAccountId = session.account.id,
                    email = session.account.email,
                    emailVerified = session.account.emailVerified,
                    createdAt = System.currentTimeMillis(),
                )
            )
        }
        sessionStore.signIn(id, session.token)
        return AuthResult.Success(
            UserEntity(
                id = id,
                username = session.account.username,
                displayName = session.account.displayName,
                serverAccountId = session.account.id,
                email = session.account.email,
                emailVerified = session.account.emailVerified,
                createdAt = System.currentTimeMillis(),
            )
        )
    }
}
