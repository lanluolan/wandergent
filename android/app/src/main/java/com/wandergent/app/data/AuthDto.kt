package com.wandergent.app.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** Wire types for the auth endpoints, mirroring `app/auth/store.py`. */

@Serializable
data class CredentialsDto(
    val username: String,
    val password: String,
    /** Only read on registration; the server ignores it on login. */
    @SerialName("display_name") val displayName: String = "",
    /**
     * Registration-only and optional -- requiring it would lock out accounts that already
     * exist without one. With no address a forgotten password ends the account, which the
     * sign-up screen says rather than letting someone find out later.
     */
    val email: String = "",
)

@Serializable
data class AccountDto(
    val id: String,
    val username: String,
    @SerialName("display_name") val displayName: String,
    @SerialName("created_at") val createdAt: String,
    val email: String = "",
    /**
     * Whether the address is proven. **Password reset only works for a proven address** --
     * otherwise registering with a stranger's address would be a takeover route.
     */
    @SerialName("email_verified") val emailVerified: Boolean = false,
)

/**
 * A live session.
 *
 * [token] is shown exactly once -- the server keeps only its hash -- so it has to be
 * persisted the moment it arrives or the account is unreachable until the next login.
 */
@Serializable
data class SessionDto(
    val token: String,
    @SerialName("expires_at") val expiresAt: String,
    val account: AccountDto,
)

@Serializable
data class ResetRequestDto(val email: String)

@Serializable
data class ResetConfirmDto(val code: String, val password: String)

@Serializable
data class EmailChangeDto(val email: String)

@Serializable
data class VerifyConfirmDto(val code: String)
