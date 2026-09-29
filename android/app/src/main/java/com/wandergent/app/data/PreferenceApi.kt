package com.wandergent.app.data

import java.io.IOException
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.SerializationException
import retrofit2.HttpException
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.Header
import retrofit2.http.Path

@Serializable
data class SavedPreference(
    val id: String,
    val text: String,
    @SerialName("created_at") val createdAt: String,
)

interface PreferenceApi {
    @GET("preferences")
    suspend fun list(@Header("Authorization") authorization: String): List<SavedPreference>

    @DELETE("preferences/{id}")
    suspend fun delete(
        @Path("id") id: String,
        @Header("Authorization") authorization: String,
    )
}

sealed interface PreferenceOutcome<out T> {
    data class Success<T>(val value: T) : PreferenceOutcome<T>
    data class Failure(val message: String) : PreferenceOutcome<Nothing>
}

class PreferenceRepository(
    private val api: PreferenceApi = Network.preferenceApi,
    // Bind the screen to its session: a delayed delete must never use a new account's token.
    private val token: String? = Network.authToken,
) {
    suspend fun list(): PreferenceOutcome<List<SavedPreference>> = call { api.list(it) }

    suspend fun delete(id: String): PreferenceOutcome<Unit> = call { api.delete(id, it) }

    private suspend fun <T> call(block: suspend (String) -> T): PreferenceOutcome<T> {
        if (token.isNullOrBlank()) return PreferenceOutcome.Failure("Sign in to manage your preferences.")
        return try {
            PreferenceOutcome.Success(block("Bearer $token"))
        } catch (e: HttpException) {
            PreferenceOutcome.Failure(
                if (e.code() == 401) "Your session has expired. Please sign in again."
                else "Could not update your saved preferences. Please try again."
            )
        } catch (e: IOException) {
            PreferenceOutcome.Failure("Could not connect. Check your connection and try again.")
        } catch (e: SerializationException) {
            PreferenceOutcome.Failure("Could not read your saved preferences. Please try again.")
        }
    }
}
