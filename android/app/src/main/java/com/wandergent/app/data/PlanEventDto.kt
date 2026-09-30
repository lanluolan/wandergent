package com.wandergent.app.data

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/**
 * One event from `POST /plan/stream`, mirroring `app/agent/events.py`.
 *
 * Flat rather than a sealed hierarchy, matching the backend: `type` says which fields are
 * meaningful and everything else is nullable. Unknown types are ignored by the consumer,
 * so a newer backend can add event kinds without breaking this client.
 */
@Serializable
data class PlanEventDto(
    val type: String,
    val message: String? = null,
    val name: String? = null,
    val arguments: JsonObject? = null,
    val ok: Boolean? = null,
    /** Failure kind for `tool_result`; the sentence for it is written on this side. */
    val code: String? = null,
    /** What older backends sent instead: their own developer sentence. */
    val error: String? = null,
    val violations: List<Violation>? = null,
    val result: PlanResponse? = null,
) {
    companion object {
        const val STAGE = "stage"
        const val TOOL_CALL = "tool_call"
        const val TOOL_RESULT = "tool_result"
        const val COMPOSING = "composing"
        const val VALIDATION = "validation"
        const val RESULT = "result"
        const val ERROR = "error"
    }
}

/**
 * What a run warning should say to the person reading the plan.
 *
 * The three budget codes collapse into one sentence: which ceiling the run hit matters to
 * whoever tunes the tool loop, not to someone deciding whether to check a closing time.
 * They stay separate on the wire; only this rendering flattens them.
 *
 * Unknown codes fall through to `detail` -- developer wording, but better than a blank
 * line where a caveat belongs.
 */
fun warningText(warning: RunWarning): String = when (warning.code) {
    "tool_calls_dropped", "tool_calls_spent", "tool_rounds_spent" ->
        "Built on less research than usual: the planner hit its lookup limit before it " +
            "had checked everything. Worth confirming opening times and prices yourself."
    else -> warning.detail
}

/**
 * What a tool call is doing, in the traveller's words. Shared by the live progress line
 * and the finished plan's tool list, so the two cannot drift.
 */
fun toolLabel(name: String, subject: String? = null): String = when (name) {
    "get_weather_forecast" ->
        subject?.let { "Checking the forecast for $it" } ?: "Checking the forecast"
    "remember_preference" -> "Remembering your preference"
    "search_places" -> "Finding places to go"
    "get_travel_time" -> "Checking travel times"
    else -> name
}

/**
 * What a failed tool call should say to the person reading the plan.
 *
 * Same split as [warningText]: the backend's `error` was written for the model and quoted
 * upstream URLs at the traveller, so it now sends a code and the sentence is written here.
 * `legacy` is that old field, kept so trips saved before the change still say something.
 */
fun toolFailureText(code: String?, legacy: String? = null): String = when (code) {
    "not_configured" -> "not set up on this server"
    "timed_out" -> "took too long"
    "rate_limited" -> "is temporarily rate limited"
    "unavailable" -> "the service was unreachable"
    "no_match" -> "nothing matched"
    "no_coverage" -> "has no coverage for this request"
    "bad_request" -> "the request could not be answered"
    "unknown_tool" -> "not available on this server"
    else -> legacy ?: "did not complete"
}

/** Human label for a violation code; the code is the stable part of the contract. */
fun violationLabel(code: String): String = when (code) {
    "over_budget" -> "over budget"
    "time_conflict" -> "overlapping activities"
    "insufficient_transfer" -> "not enough travel time"
    "day_out_of_range" -> "date outside the trip"
    "duplicate_day" -> "duplicate day"
    "unsociable_hours" -> "unsociably early or late"
    "overlong_day" -> "day is overloaded"
    "empty_day" -> "empty day"
    "missing_accommodation" -> "no accommodation booked"
    "vague_venue" -> "venue not named"
    "outside_opening_hours" -> "venue closed then"
    "understated_cost" -> "cost looks understated"
    else -> code
}
