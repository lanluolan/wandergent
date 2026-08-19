package com.wandergent.app.data

import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST

/**
 * Accounts and sessions.
 *
 * The token these return is the only thing that establishes identity anywhere in this
 * app. It travels in the `Authorization` header, attached by [Network]'s interceptor --
 * never in a query string, which would put it in the server's access log.
 */
interface AuthApi {

    @POST("auth/register")
    suspend fun register(@Body credentials: CredentialsDto): SessionDto

    @POST("auth/login")
    suspend fun login(@Body credentials: CredentialsDto): SessionDto

    /** Ends the session server-side, so a token copied off the device stops working. */
    @POST("auth/logout")
    suspend fun logout()

    /** Whether a stored token is still live, and who it belongs to. */
    @GET("auth/me")
    suspend fun me(): AccountDto

    /**
     * Ask for a reset code.
     *
     * Always 204, whether or not the address belongs to an account -- so the client
     * cannot report "no such address" either, and must not pretend to.
     */
    @POST("auth/reset/request")
    suspend fun requestReset(@Body request: ResetRequestDto)

    /** Set a new password from a code. 400 if the code is wrong or expired. */
    @POST("auth/reset/confirm")
    suspend fun confirmReset(@Body request: ResetConfirmDto)

    /** Set or change this account's address. The new one always starts unproven. */
    @POST("auth/email")
    suspend fun changeEmail(@Body request: EmailChangeDto): AccountDto

    /** Send another confirmation code. Requires a session, so it cannot enumerate. */
    @POST("auth/verify/request")
    suspend fun resendVerification()

    /** Prove the address. 400 if the code is wrong, expired, or not this account's. */
    @POST("auth/verify")
    suspend fun confirmVerification(@Body request: VerifyConfirmDto)
}
