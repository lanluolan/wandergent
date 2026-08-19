package com.wandergent.app.data

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * What goes on the wire when a follow-up edits the plan above it.
 *
 * The shape matters more than it looks: `previous` present means "edit this", absent
 * means "plan something new", and the backend branches on exactly that. A serialiser
 * that emitted `"previous":null` on every first request would be harmless; one that
 * dropped a populated `previous` would silently turn every edit back into a replan.
 */
class RevisionRequestTest {

    private val itinerary = Itinerary(
        destination = "Los Angeles",
        startDate = "2026-09-01",
        endDate = "2026-09-01",
        currency = "USD",
        budget = 500.0,
        days = listOf(
            DayPlan(
                date = "2026-09-01",
                summary = "Downtown in a day",
                activities = listOf(
                    Activity(
                        startTime = "09:30",
                        endTime = "11:30",
                        title = "Griffith Observatory",
                        category = "sightseeing",
                        location = "2800 E Observatory Rd, Los Angeles",
                        estimatedCost = 12.0,
                    ),
                ),
            ),
        ),
    )

    @Test
    fun `a first request carries no previous plan`() {
        val encoded = ApiJson.json.encodeToString(PlanRequest(message = "one day in Los Angeles"))

        assertEquals("""{"message":"one day in Los Angeles"}""", encoded)
    }

    @Test
    fun `a follow-up carries the whole plan being edited`() {
        val encoded = ApiJson.json.encodeToString(
            PlanRequest(message = "swap lunch for a taco place", currency = "USD", previous = itinerary),
        )

        assertTrue(encoded.contains(""""message":"swap lunch for a taco place""""), encoded)
        // The plan itself, not an id: the backend is stateless and cannot look it up.
        assertTrue(encoded.contains(""""previous":{"""), encoded)
        assertTrue(encoded.contains("Griffith Observatory"), encoded)
        assertTrue(encoded.contains("2800 E Observatory Rd"), encoded)
        // The budget rides along, so the revision is still checked against it.
        assertTrue(encoded.contains(""""budget":500.0"""), encoded)
    }

    @Test
    fun `a non-ASCII place name is not escaped on the way out`() {
        // Kotlinx does not escape non-ASCII by default, and the backend matches venues by
        // the string we send. "La Canada Flintridge" is spelled with an n-tilde on every map
        // there is, so an escaping serialiser would quietly stop matching it.
        val encoded = ApiJson.json.encodeToString(PlanRequest(message = "a day in La Cañada Flintridge"))

        assertEquals("""{"message":"a day in La Cañada Flintridge"}""", encoded)
    }

    @Test
    fun `a plan decoded from the backend can be sent straight back`() {
        // The round trip a revision actually makes: server -> client -> server. Server
        // -computed totals come back in, and are ignored there because they are derived
        // again -- so this only has to not throw.
        val fromServer = """
            {"itinerary":{"destination":"Los Angeles","start_date":"2026-09-01","end_date":"2026-09-01",
            "travelers":1,"currency":"USD","budget":500.0,"days":[{"date":"2026-09-01",
            "summary":"Downtown in a day","activities":[{"start_time":"09:30","end_time":"11:30",
            "title":"Griffith Observatory","category":"sightseeing","location":"2800 E Observatory Rd",
            "estimated_cost":12.0,"highlights":[]}],"estimated_cost":12.0}],"notes":[],
            "total_estimated_cost":12.0},"tool_calls":[],"warnings":[]}
        """.trimIndent().replace("\n", "")

        val decoded = ApiJson.json.decodeFromString<PlanResponse>(fromServer)
        val resent = ApiJson.json.encodeToString(
            PlanRequest(message = "change one thing", previous = decoded.itinerary),
        )

        assertTrue(resent.contains("Griffith Observatory"), resent)
        assertEquals(12.0, decoded.itinerary?.totalEstimatedCost)
    }
}
