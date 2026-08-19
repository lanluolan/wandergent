package com.wandergent.app.data

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/**
 * One event from `POST /plan/stream`, mirroring `app/agent/events.py`.
 *
 * Flat rather than a sealed hierarchy, matching the backend: `type` says which fields
 * are meaningful, and everything else is nullable. A sealed class with a polymorphic
 * discriminator would buy type-safety at the cost of a custom serializer, for six
 * event kinds that each carry two or three fields.
 *
 * Unknown `type` values are ignored by the consumer rather than treated as errors, so
 * a newer backend can add event kinds without breaking older clients.
 */
@Serializable
data class PlanEventDto(
    val type: String,
    val message: String? = null,
    val name: String? = null,
    val arguments: JsonObject? = null,
    val ok: Boolean? = null,
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
 * The three budget codes collapse into one sentence on purpose. Which ceiling the run
 * hit -- calls dropped mid-round, calls spent exactly, rounds spent -- is a real
 * distinction to whoever is tuning the tool loop and no distinction at all to someone
 * deciding whether to double-check a closing time. The codes stay separate on the wire
 * so the backend keeps that detail; only this rendering flattens them.
 *
 * Unknown codes fall through to `detail`. It is developer wording, which is the thing
 * this function exists to stop showing -- but a blank line where a caveat belongs is
 * worse, and a client older than the server is the only way to get here.
 */
fun warningText(warning: RunWarning): String = when (warning.code) {
    "tool_calls_dropped", "tool_calls_spent", "tool_rounds_spent" ->
        "Built on less research than usual: the planner hit its lookup limit before it " +
            "had checked everything. Worth confirming opening times and prices yourself."
    else -> warning.detail
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
