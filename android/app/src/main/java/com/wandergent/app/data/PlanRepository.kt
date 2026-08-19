package com.wandergent.app.data

import com.wandergent.app.BuildConfig
import java.io.IOException
import kotlinx.coroutines.flow.Flow
import retrofit2.HttpException

/**
 * Result of one planning attempt, already translated into something the UI can show.
 *
 * `retryable` follows the status-code split the backend deliberately makes: a
 * misconfigured server (500) is not worth retrying, whereas a slow or unavailable
 * model (504 / 502) is.
 */
sealed interface PlanOutcome {
    data class Success(val response: PlanResponse) : PlanOutcome

    data class Failure(val message: String, val retryable: Boolean) : PlanOutcome
}

class PlanRepository(
    private val api: PlanApi = Network.planApi,
    private val streamClient: PlanStreamClient = PlanStreamClient(),
) {

    /** Live progress for one planning run. The primary path. */
    fun stream(
        message: String,
        currency: String = "",
        previous: Itinerary? = null,
    ): Flow<PlanEventDto> = streamClient.stream(message, currency, previous)

    /**
     * Non-streaming fallback. Used when the stream dies before producing anything --
     * some proxies buffer or break `text/event-stream`, and a plan that arrives late
     * beats no plan at all.
     */
    suspend fun plan(
        message: String,
        currency: String = "",
        previous: Itinerary? = null,
    ): PlanOutcome =
        try {
            PlanOutcome.Success(api.plan(PlanRequest(message, currency, previous)))
        } catch (e: HttpException) {
            val detail = ApiJson.parseErrorDetail(e.response()?.errorBody()?.string())
            describeFailure(e.code(), detail, e.retryAfterSeconds())
        } catch (e: IOException) {
            // Covers connection refused, DNS, and read timeouts.
            describeConnectionFailure(e)
        }
}

/**
 * "Too many requests", with the wait in it.
 *
 * The server sends `Retry-After` precisely so a client does not have to guess or poll.
 * A message without a number in it produces a person tapping the button every two
 * seconds, which is the behaviour the limit exists to stop.
 */
internal fun tooManyRequests(detail: String?, retryAfterSeconds: Int?): String {
    val wait = when {
        retryAfterSeconds == null -> "Try again shortly."
        retryAfterSeconds < 90 -> "Try again in ${retryAfterSeconds}s."
        else -> "Try again in about ${(retryAfterSeconds + 59) / 60} min."
    }
    // The server's detail has no trailing full stop, so it ran straight into ours:
    // "the limit is 10 per 60 min Try again in about 59 min."
    val reason = (detail ?: "Too many requests").trimEnd('.', ' ')
    return "$reason. $wait"
}

/** The `Retry-After` header as seconds, or null if it was absent or not a number. */
internal fun HttpException.retryAfterSeconds(): Int? =
    response()?.headers()?.get("Retry-After")?.trim()?.toIntOrNull()

/** Maps the backend's status-code split onto a user-facing message. */
internal fun describeFailure(
    code: Int?,
    detail: String?,
    retryAfterSeconds: Int? = null,
): PlanOutcome.Failure =
    when (code) {
        // Retryable, but not immediately -- which is the whole point of the number.
        429 -> PlanOutcome.Failure(tooManyRequests(detail, retryAfterSeconds), retryable = true)
        422 -> PlanOutcome.Failure(detail ?: "That request was rejected. Try rephrasing it.", retryable = false)
        500 -> PlanOutcome.Failure("The backend is misconfigured: ${detail ?: "no reason given"}", retryable = false)
        504 -> PlanOutcome.Failure("The model timed out. Worth retrying.", retryable = true)
        502 -> PlanOutcome.Failure("The model is unavailable: ${detail ?: "no reason given"}", retryable = true)
        null -> PlanOutcome.Failure(detail ?: "The connection dropped. Worth retrying.", retryable = true)
        else -> PlanOutcome.Failure(
            "Request failed (HTTP $code)${detail?.let { ": $it" } ?: ""}",
            retryable = true,
        )
    }

internal fun describeConnectionFailure(e: Throwable): PlanOutcome.Failure =
    PlanOutcome.Failure(
        "Cannot reach the backend at ${BuildConfig.BASE_URL}\n" +
            "Check the backend is running and the adb reverse tunnel is still up.(${e.javaClass.simpleName})",
        retryable = true,
    )
