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
     * An itinerary to **revise** rather than replace; null asks for a new plan.
     *
     * Sent by us because the backend is stateless. A revision runs the same
     * validate-repair-revalidate cycle, so an edit cannot quietly bust the budget.
     */
    val previous: Itinerary? = null,
    @SerialName("previous_constraints") val previousConstraints: TripConstraints? = null,
    val constraints: TripConstraints? = null,
)

@Serializable
data class PlanResponse(
    @SerialName("run_id") val runId: String? = null,
    @SerialName("trace_id") val traceId: String? = null,
    @SerialName("activity_evidence") val activityEvidence: List<ActivityEvidence> = emptyList(),
    @SerialName("feedback_available") val feedbackAvailable: Boolean = false,
    val constraints: TripConstraints? = null,
    val itinerary: Itinerary? = null,
    @SerialName("tool_calls") val toolCalls: List<ToolCallRecord> = emptyList(),
    @SerialName("tool_usage") val toolUsage: ToolUsage = ToolUsage(),
    val warnings: List<@Serializable(with = TolerantRunWarning::class) RunWarning> = emptyList(),
    @SerialName("raw_reply") val rawReply: String? = null,
    val validation: ValidationReport? = null,
)

/**
 * Something the run could not finish, as a code plus the numbers behind it.
 *
 * A code, not a sentence: the wording belongs on this side, where we know who is reading.
 * `detail` is only the fallback for a code this build predates. `String` rather than an
 * enum, because the server ships ahead of the app and an unknown code must not throw.
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
 * Room stores the response verbatim, so trips saved before warnings gained codes hold the
 * string form and would otherwise vanish from the library. `ignoreUnknownKeys` covers a
 * new field; only this covers a changed type.
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
 * `advisory` findings are remarks about pace -- a long day, a late finish -- which are
 * the traveller's own call and were never enforced, so they are not drawn as failures.
 */
@Serializable
data class ValidationReport(val violations: List<Violation> = emptyList()) {
    val blocking: List<Violation> get() = violations.filter { !it.advisory }
    val advisory: List<Violation> get() = violations.filter { it.advisory }
    val ok: Boolean get() = blocking.isEmpty()
}

/** Mirrors `ADVISORY_CODES` in `app/agent/validation.py`; the code is the stable contract. */
private val ADVISORY_CODES = setOf("overlong_day", "unsociable_hours", "transfer_unverified")

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
    // Mirrors the backend `Itinerary.currency` default; both only apply when a plan
    // arrives without the field. Change one and change the other.
    val currency: String = "USD",
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
    @SerialName("travel_mode") val travelMode: String? = null,
    val indoor: Boolean? = null,
    @SerialName("estimated_cost") val estimatedCost: Double = 0.0,
    /** Dishes to order, exhibits worth the queue, what to book ahead. Often empty. */
    val highlights: List<String> = emptyList(),
    val notes: String? = null,
)

/**
 * One tool the agent invoked. `arguments` is free-form, so it stays raw JSON.
 *
 * `code` says what kind of failure; the wording lives on this side. `error` is the old
 * developer sentence, kept only so trips saved before the change still parse.
 */
@Serializable
data class ToolCallRecord(
    val name: String,
    val arguments: JsonObject = JsonObject(emptyMap()),
    val ok: Boolean,
    val code: String? = null,
    val error: String? = null,
    @SerialName("cache_status") val cacheStatus: String = "miss",
    @SerialName("cache_age_seconds") val cacheAgeSeconds: Double? = null,
    @SerialName("duration_ms") val durationMs: Int = 0,
    val attempts: Int = 1,
    val contributed: Boolean = false,
    @SerialName("collected_at") val collectedAt: String? = null,
)

@Serializable
data class ActivityEvidence(
    @SerialName("day_index") val dayIndex: Int,
    @SerialName("activity_index") val activityIndex: Int,
    val source: String? = null,
    @SerialName("collected_at") val collectedAt: String? = null,
    @SerialName("venue_verified") val venueVerified: Boolean = false,
    @SerialName("hours_available") val hoursAvailable: Boolean = false,
    @SerialName("price_level_available") val priceLevelAvailable: Boolean = false,
    @SerialName("price_confidence") val priceConfidence: String = "estimate",
    @SerialName("recheck_before_departure") val recheckBeforeDeparture: Boolean = true,
    @SerialName("route_source") val routeSource: String? = null,
    @SerialName("route_collected_at") val routeCollectedAt: String? = null,
    @SerialName("route_departure") val routeDeparture: String? = null,
    @SerialName("route_mode") val routeMode: String? = null,
    @SerialName("route_seconds") val routeSeconds: Int? = null,
)

@Serializable
data class ToolUsage(
    @SerialName("requested_calls") val requestedCalls: Int = 0,
    @SerialName("executed_calls") val executedCalls: Int = 0,
    @SerialName("cache_hits") val cacheHits: Int = 0,
    @SerialName("run_cache_hits") val runCacheHits: Int = 0,
    @SerialName("shared_cache_hits") val sharedCacheHits: Int = 0,
    @SerialName("failed_calls") val failedCalls: Int = 0,
    @SerialName("retried_calls") val retriedCalls: Int = 0,
    @SerialName("contributed_calls") val contributedCalls: Int = 0,
    @SerialName("dropped_calls") val droppedCalls: Int = 0,
    @SerialName("duration_ms") val durationMs: Int = 0,
    @SerialName("calls_by_tool") val callsByTool: Map<String, Int> = emptyMap(),
    @SerialName("cache_hit_rate") val cacheHitRate: Double = 0.0,
)

/** FastAPI's error envelope, used for every non-2xx response. */
@Serializable
data class ApiError(val detail: String? = null)


@Serializable
data class TripConstraints(
    val budget: Double? = null,
    val currency: String? = null,
    @SerialName("start_date") val startDate: String? = null,
    @SerialName("end_date") val endDate: String? = null,
    val days: Int? = null,
    val travelers: Int? = null,
    @SerialName("allowed_modes") val allowedModes: List<String>? = null,
    @SerialName("lodging_arranged") val lodgingArranged: Boolean? = null,
)
