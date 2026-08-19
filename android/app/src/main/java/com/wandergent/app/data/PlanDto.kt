package com.wandergent.app.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.JsonTransformingSerializer
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * Wire types for `POST /plan`, mirroring the contract in `docs/api.md`.
 *
 * Two things the backend guarantees that shape these declarations:
 * - `itinerary` can be null on a 200 -- that is the "model could not produce a valid
 *   plan" case, not an error status.
 * - `indoor` is genuinely tri-state; "unknown" is a real value, so it must stay
 *   nullable rather than defaulting to false.
 *
 * Cost fields are computed server-side and are read-only here.
 */

@Serializable
data class PlanRequest(
    val message: String,
    // No user id. Whose preferences the run recalls and updates comes from the bearer
    // token the interceptor attaches; a request with no token is simply anonymous.
    /**
     * ISO 4217 code the traveller settles up in. The backend *estimates* in it rather
     * than converting into it. Empty lets the backend use the destination's local
     * currency, which is what it did before this setting existed.
     */
    val currency: String = "",
    /**
     * An itinerary to **revise** rather than replace.
     *
     * Sent by us because the backend is stateless and we already hold the plan being
     * changed. The revision runs the same validate-repair-revalidate cycle as a fresh
     * plan, so an edit cannot quietly bust the budget or leave no time to get anywhere.
     * Null asks for a brand-new plan.
     */
    val previous: Itinerary? = null,
)

@Serializable
data class PlanResponse(
    val itinerary: Itinerary? = null,
    @SerialName("tool_calls") val toolCalls: List<ToolCallRecord> = emptyList(),
    val warnings: List<@Serializable(with = TolerantRunWarning::class) RunWarning> = emptyList(),
    @SerialName("raw_reply") val rawReply: String? = null,
    val validation: ValidationReport? = null,
)

/**
 * Something the run could not finish, as a code plus the numbers behind it.
 *
 * Not a sentence. The backend used to send one, and it was a sentence written for
 * whoever wrote the tool loop: travellers were shown `reached the 16-call tool budget;
 * skipped 3 further call(s) to search_places`. The wording belongs here, where we know
 * who is reading, and `detail` is only the fallback for a code this build predates.
 *
 * Deliberately `String`, not an enum: an unrecognised code has to render as *something*,
 * not throw, because the server ships ahead of the app.
 */
@Serializable
data class RunWarning(
    val code: String,
    val detail: String = "",
    val budget: Int? = null,
    @SerialName("dropped_calls") val droppedCalls: Int? = null,
    @SerialName("dropped_tools") val droppedTools: List<String> = emptyList(),
) {
    companion object {
        /** Code stamped on warnings recovered from a plan saved before they had codes. */
        const val LEGACY = "legacy"
    }
}

/**
 * Reads a warning that is either an object or a bare string.
 *
 * The string form is what saved plans in Room already hold: `planJson` is the response
 * verbatim, so every trip in the library predating this change would otherwise fail to
 * parse and disappear from the list. `ignoreUnknownKeys` covers a *new field*; nothing
 * covers a changed type but this. Legacy entries keep their old developer wording,
 * because that is genuinely all that was stored -- there is nothing to upgrade them to.
 */
object TolerantRunWarning : JsonTransformingSerializer<RunWarning>(RunWarning.serializer()) {
    override fun transformDeserialize(element: JsonElement): JsonElement =
        if (element is JsonPrimitive && element.isString) {
            buildJsonObject {
                put("code", RunWarning.LEGACY)
                put("detail", element.content)
            }
        } else {
            element
        }
}

/**
 * Outcome of the server-side hard-constraint check. Anything left in `blocking`
 * survived a repair attempt, so the UI must not imply the itinerary is sound.
 *
 * `advisory` findings are different: they are remarks about pace -- a long day, a late
 * finish -- which are the traveller's own call and were deliberately never enforced.
 * Showing them in red would mean scolding someone for the trip they asked for.
 */
@Serializable
data class ValidationReport(val violations: List<Violation> = emptyList()) {
    val blocking: List<Violation> get() = violations.filter { !it.advisory }
    val advisory: List<Violation> get() = violations.filter { it.advisory }
    val ok: Boolean get() = blocking.isEmpty()
}

/**
 * Mirrors `ADVISORY_CODES` in the backend's `app/agent/validation.py`. Duplicated rather
 * than sent on the wire because the *code* is the stable contract and this is a display
 * decision; if the two ever drift, the header still comes from the backend's own list.
 */
private val ADVISORY_CODES = setOf("overlong_day", "unsociable_hours")

@Serializable
data class Violation(
    val code: String,
    val message: String,
    val day: String? = null,
) {
    val advisory: Boolean get() = code in ADVISORY_CODES
}

@Serializable
data class Itinerary(
    val destination: String,
    @SerialName("start_date") val startDate: String,
    @SerialName("end_date") val endDate: String,
    val travelers: Int = 1,
    val currency: String = "CNY",
    val budget: Double? = null,
    val days: List<DayPlan> = emptyList(),
    val notes: List<String> = emptyList(),
    @SerialName("total_estimated_cost") val totalEstimatedCost: Double = 0.0,
)

@Serializable
data class DayPlan(
    val date: String,
    val summary: String,
    val weather: String? = null,
    val activities: List<Activity> = emptyList(),
    @SerialName("estimated_cost") val estimatedCost: Double = 0.0,
)

@Serializable
data class Activity(
    @SerialName("start_time") val startTime: String,
    @SerialName("end_time") val endTime: String,
    val title: String,
    val category: String = "other",
    val location: String? = null,
    val indoor: Boolean? = null,
    @SerialName("estimated_cost") val estimatedCost: Double = 0.0,
    /** Dishes to order, exhibits worth the queue, what to book ahead. Often empty. */
    val highlights: List<String> = emptyList(),
    val notes: String? = null,
)

/** One tool the agent invoked. `arguments` is free-form, so it stays raw JSON. */
@Serializable
data class ToolCallRecord(
    val name: String,
    val arguments: JsonObject = JsonObject(emptyMap()),
    val ok: Boolean,
    val error: String? = null,
)

/** FastAPI's error envelope, used for every non-2xx response. */
@Serializable
data class ApiError(val detail: String? = null)
