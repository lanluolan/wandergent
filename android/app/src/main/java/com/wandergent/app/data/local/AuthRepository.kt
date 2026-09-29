package com.wandergent.app.data.local

import com.wandergent.app.data.AccountDto
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
import kotlinx.coroutines.ExperimentalCoroutinesApi
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
 * The server checks the password (PBKDF2) and issues a bearer token; that token is the
 * only thing that establishes who is asking. Nothing in this app claims an identity -- the
 * request bodies have no field for one. The local [UserEntity] row survives only as a
 * **storage partition** and holds no credentials.
 *
 * **Offline**: signing in needs the network, staying signed in does not. The token and the
 * local id live in [SessionStore], so an already signed-in traveller keeps their library.
 */
@OptIn(ExperimentalCoroutinesApi::class)
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
     * End the session on the server as well as here. The server call is best-effort -- the
     * local token is discarded either way, and refusing to sign out because the network is
     * down would be the worse failure.
     */
    suspend fun logout() {
        runCatching { api.logout() }
        forget()
    }

    /**
     * Drop the session here without telling the server, for when the server already told
     * *us* -- `/auth/logout` with a rejected token only earns a second rejection.
     */
    suspend fun forget() {
        Network.authToken = null
        sessionStore.signOut()
    }

    /**
     * Ask for a reset code.
     *
     * **Reports success even for an address with no account**: the server answers
     * identically either way on purpose, and reporting "no such address" here would hand
     * back exactly what it withheld.
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
     * Run a call that changes the account, then re-read it from the server rather than
     * predicting the outcome.
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
     * Without this an expired token keeps being sent until something fails, and the
     * failure lands on whatever the traveller happened to tap.
     */
    suspend fun restore() {
        val token = sessionStore.token()
        if (token == null) {
            // Signed in locally with no token: a session from before accounts moved
            // server-side, which would present as signed in while every write 401s.
            // Clearing it keeps the local row, so signing in with the same username
            // re-attaches the library rather than starting an empty one.
            if (sessionStore.currentUserId.first() != null) sessionStore.signOut()
            return
        }
        Network.authToken = token
        try {
            // The answer is *used*, not just awaited: discarded, the cached email and
            // verified flag go stale as soon as the account changes on another device.
            cache(api.me())
        } catch (e: HttpException) {
            if (e.code() == 401) {
                Network.authToken = null
                sessionStore.signOut()
            }
        } catch (_: IOException) {
            // Offline. Keep the session -- it may still be valid, and a dead token gets
            // cleared on the next 401 anyway.
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

    /** Write the server's answer into the local row, so nothing here has to guess. */
    private suspend fun cache(account: AccountDto) {
        val local = userDao.findByUsername(account.username) ?: return
        userDao.linkToAccount(
            local.id,
            account.id,
            account.displayName,
            account.email,
            account.emailVerified,
        )
    }

    /**
     * Bind a server session to a local row, reusing one that matches the username.
     *
     * Reuse rather than always-insert is what keeps a returning user's saved trips: the
     * library is keyed on the local row id, so a second row would present an empty library
     * to someone who has plans saved. It also adopts rows written before accounts moved
     * server-side, which have no `serverAccountId` at all.
     */
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
