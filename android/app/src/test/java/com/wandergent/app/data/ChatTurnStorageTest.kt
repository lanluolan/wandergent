package com.wandergent.app.data

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * The encode/decode round trip a restored conversation depends on.
 *
 * Room and the DAO are Android-only, so what is testable here is the part that actually
 * breaks: a plan stored as JSON has to come back as something a follow-up can revise.
 * If that round trip loses the itinerary, the transcript restores to a wall of cards
 * with nothing editable behind them -- which looks fine and is useless.
 */
class ChatTurnStorageTest {

    private val stored = """
        {"itinerary":{"destination":"Los Angeles","start_date":"2026-09-01","end_date":"2026-09-02",
        "travelers":1,"currency":"USD","budget":400.0,"days":[{"date":"2026-09-01",
        "summary":"Day one","activities":[{"start_time":"09:00","end_time":"11:00",
        "title":"Griffith Observatory","category":"sightseeing","location":"2800 E Observatory Rd",
        "estimated_cost":12.0,"highlights":["Zeiss telescope"]}],"estimated_cost":12.0}],
        "notes":["Book ahead"],"total_estimated_cost":12.0},"tool_calls":[],"warnings":[],
        "validation":{"violations":[]}}
    """.trimIndent().replace("\n", "")

    @Test
    fun `a stored plan comes back complete enough to revise`() {
        val decoded = ApiJson.json.decodeFromString<PlanResponse>(stored)
        val itinerary = assertNotNull(decoded.itinerary)

        assertEquals("Los Angeles", itinerary.destination)
        assertEquals(400.0, itinerary.budget)
        assertEquals(listOf("Zeiss telescope"), itinerary.days[0].activities[0].highlights)

        // The actual use: it goes straight back out as the base of an edit.
        val resent = ApiJson.json.encodeToString(
            PlanRequest(message = "swap the lunch", previous = itinerary),
        )
        assertTrue(resent.contains("Griffith Observatory"), resent)
        assertTrue(resent.contains(""""budget":400.0"""), resent)
    }

    @Test
    fun `re-encoding and re-decoding is stable`() {
        // Restore -> edit -> store -> restore is a real sequence; a lossy round trip
        // would degrade the plan a little on each pass.
        val once = ApiJson.json.decodeFromString<PlanResponse>(stored)
        val again = ApiJson.json.decodeFromString<PlanResponse>(
            ApiJson.json.encodeToString(once),
        )

        assertEquals(once.itinerary?.destination, again.itinerary?.destination)
        assertEquals(once.itinerary?.totalEstimatedCost, again.itinerary?.totalEstimatedCost)
        assertEquals(
            once.itinerary?.days?.first()?.activities?.size,
            again.itinerary?.days?.first()?.activities?.size,
        )
        assertEquals(once.validation?.ok, again.validation?.ok)
    }

    @Test
    fun `a row written by an older version is skipped, not fatal`() {
        // What `ChatTurnRepository.restore` relies on: one unreadable row must not stop
        // the app from opening.
        val broken = """{"itinerary":{"destination":"Los Angeles"}}"""

        val decoded = runCatching {
            ApiJson.json.decodeFromString<PlanResponse>(broken)
        }.getOrNull()

        assertNull(decoded)
    }
}
