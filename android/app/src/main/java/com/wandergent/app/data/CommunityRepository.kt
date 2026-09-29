package com.wandergent.app.data

import java.io.IOException
import retrofit2.HttpException

/**
 * One community request, already turned into something a screen can render.
 *
 * A value rather than an exception: every caller is a ViewModel handling a tap, and a
 * feed that cannot load has to say *why* rather than look empty.
 */
sealed interface CommunityOutcome<out T> {
    data class Success<T>(val value: T) : CommunityOutcome<T>

    data class Failure(val message: String) : CommunityOutcome<Nothing>
}

/**
 * What of the original request, if anything, travels with a shared trip.
 *
 * **Defaults to nothing, and that default is the point.** The free-text request is where
 * the private half of a trip ends up -- health, money, who is coming. The itinerary
 * already says what the trip is; the sentence that produced it can cost its author more
 * than it is worth to a reader.
 */
internal fun sharedRequest(request: String, include: Boolean): String =
    if (include) request.trim() else ""

class CommunityRepository(private val api: CommunityApi = Network.communityApi) {

    // No viewer id: who is asking comes from the token the interceptor attaches, so a
    // signed-out reader gets a feed with `savedByViewer` null rather than a wrong answer.
    suspend fun feed(
        limit: Int = 30,
        cursor: String? = null,
        destination: String = "",
    ): CommunityOutcome<FeedPage> = call { api.feed(limit, cursor, destination) }

    suspend fun plan(id: String): CommunityOutcome<SharedPlanDetail> =
        call { api.plan(id) }

    suspend fun publish(request: PublishRequest): CommunityOutcome<SharedPlanDetail> =
        call { api.publish(request) }

    suspend fun setSaved(id: String, saved: Boolean): CommunityOutcome<SaveResponse> =
        call { api.setSaved(id, SaveRequest(saved)) }

    suspend fun withdraw(id: String): CommunityOutcome<Unit> =
        call { api.withdraw(id) }

    private suspend fun <T> call(block: suspend () -> T): CommunityOutcome<T> =
        try {
            CommunityOutcome.Success(block())
        } catch (e: HttpException) {
            val detail = ApiJson.parseErrorDetail(e.response()?.errorBody()?.string())
            CommunityOutcome.Failure(
                when (e.code()) {
                    429 -> tooManyRequests(detail, e.retryAfterSeconds())
                    // The server returns this for "gone" and "not yours" alike, so say
                    // the part that is true in both cases.
                    404 -> detail ?: "That plan is no longer shared."
                    422 -> detail ?: "The server rejected that."
                    else -> "Request failed (HTTP ${e.code()})${detail?.let { ": $it" } ?: ""}"
                }
            )
        } catch (e: IOException) {
            CommunityOutcome.Failure(
                "Cannot reach the backend at ${ServerConfig.baseUrl} (${e.javaClass.simpleName})"
            )
        }
}
