package com.wandergent.app.ui.auth

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.wandergent.app.data.Network
import com.wandergent.app.data.local.AuthRepository
import com.wandergent.app.data.local.AuthResult
import com.wandergent.app.data.local.SessionStore
import com.wandergent.app.data.local.UserEntity
import com.wandergent.app.data.local.WandergentDatabase
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

sealed interface SessionState {
    /** Still reading persisted state; the UI must not decide where to route yet. */
    data object Loading : SessionState

    data object SignedOut : SessionState

    data class SignedIn(val user: UserEntity) : SessionState
}

class AuthViewModel(application: Application) : AndroidViewModel(application) {

    private val repository = AuthRepository(
        userDao = WandergentDatabase.get(application).userDao(),
        sessionStore = SessionStore(application),
    )

    init {
        // Re-arm the interceptor from the persisted token and check the server still
        // honours it. Without this a revoked or expired session keeps being sent until
        // something fails, and the failure lands on whatever the traveller happened to
        // tap rather than at launch where it can be handled.
        viewModelScope.launch { repository.restore() }

        // A session the server rejects mid-use ends here rather than surfacing as a
        // stream of confusing failures on unrelated screens.
        Network.onUnauthorized = {
            viewModelScope.launch { repository.forget() }
        }
    }

    val session: StateFlow<SessionState> = repository.currentUser
        .map { user -> if (user == null) SessionState.SignedOut else SessionState.SignedIn(user) }
        .stateIn(viewModelScope, SharingStarted.Eagerly, SessionState.Loading)

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()

    private val _busy = MutableStateFlow(false)
    val busy: StateFlow<Boolean> = _busy.asStateFlow()

    fun login(username: String, password: String) = submit { repository.login(username, password) }

    fun register(username: String, password: String, displayName: String, email: String) =
        submit { repository.register(username, password, displayName, email) }

    /**
     * Ask for a reset code, and report only that the request went through.
     *
     * Deliberately *not* "we sent you a code": the server answers the same whether or not
     * the address has an account, and saying more here would leak what it withheld.
     */
    fun requestReset(email: String) = submit(
        onSuccess = { _notice.value = "If that address has an account, a code is on its way." },
    ) { repository.requestReset(email) }

    fun confirmReset(code: String, password: String) = submit(
        onSuccess = { _notice.value = "Password changed. Sign in with the new one." },
    ) { repository.confirmReset(code, password) }

    /** A one-shot message for the reset screens; sign-in reports through [session]. */
    private val _notice = MutableStateFlow<String?>(null)
    val notice: StateFlow<String?> = _notice.asStateFlow()

    fun clearNotice() {
        _notice.value = null
    }

    fun verifyEmail(code: String) = submit(
        onSuccess = { _notice.value = "Email confirmed. You can reset your password with it now." },
    ) { repository.verifyEmail(code) }

    fun resendVerification() = submit(
        onSuccess = { _notice.value = "Another code is on its way." },
    ) { repository.resendVerification() }

    fun changeEmail(email: String) = submit(
        onSuccess = {
            _notice.value =
                if (email.isBlank()) {
                    "Address removed. This account can no longer be recovered."
                } else {
                    "Address saved. Check it for a confirmation code."
                }
        },
    ) { repository.changeEmail(email) }

    fun logout() {
        viewModelScope.launch { repository.logout() }
    }

    fun clearError() {
        _error.value = null
    }

    /**
     * Runs an auth call and funnels the outcome into the shared busy/error state.
     * Success needs no handling here: the session flow changes, and navigation follows
     * from that rather than from a one-shot event.
     */
    private fun submit(onSuccess: () -> Unit = {}, block: suspend () -> AuthResult) {
        if (_busy.value) return
        _busy.value = true
        _error.value = null
        viewModelScope.launch {
            when (val result = block()) {
                is AuthResult.Success -> onSuccess()
                is AuthResult.Failure -> _error.value = result.message
            }
            _busy.value = false
        }
    }
}
