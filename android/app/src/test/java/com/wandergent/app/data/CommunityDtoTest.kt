package com.wandergent.app.data

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * The community wire contract, checked against payloads the running backend produces.
 *
 * A compiling data class proves nothing about whether it parses what the server actually
 * sends -- the same reason `PlanDtoTest` exists.
 */
class CommunityDtoTest {

    private val card = """
        {"id":"e29d4dbf-42eb-49ba-b04b-2727375a2845","author_id":"1","author_name":"Yu",
        "note":"Rainy-day friendly","request":"2 days in Chicago","destination":"Chicago",
        "start_date":"2026-09-07","end_date":"2026-09-08","day_count":2,"total_cost":60.0,
        "currency":"USD","created_at":"2026-08-18T04:25:38.418492Z","save_count":2,
        "saved_by_viewer":true}
    """.trimIndent().replace("\n", "")

    @Test
    fun `a feed card parses the server's snake_case`() {
        val parsed = ApiJson.json.decodeFromString<SharedPlanCard>(card)

        assertEquals("Chicago", parsed.destination)
        assertEquals(2, parsed.dayCount)
        assertEquals(60.0, parsed.totalCost)
        assertEquals(2, parsed.saveCount)
        assertEquals("Yu", parsed.authorName)
    }

    @Test
    fun `an unknown viewer stays null rather than becoming false`() {
        // The heart is drawn from this. `false` is a claim about this reader; `null` is
        // "nobody told the server who is asking", and the two must not collapse.
        val anonymous = card.replace(""","saved_by_viewer":true""", "")
        val parsed = ApiJson.json.decodeFromString<SharedPlanCard>(anonymous)

        assertNull(parsed.savedByViewer)
    }

    @Test
    fun `an explicit false is preserved`() {
        val parsed = ApiJson.json.decodeFromString<SharedPlanCard>(
            card.replace(""""saved_by_viewer":true""", """"saved_by_viewer":false"""),
        )

        assertFalse(parsed.savedByViewer!!)
    }

    @Test
    fun `a card carries no itinerary and does not need one`() {
        // The feed deliberately omits it; thirty cards should not pull thirty plans.
        // Parsing must not require a field the endpoint never sends.
        ApiJson.json.decodeFromString<SharedPlanCard>(card)
    }

    @Test
    fun `the detail shape carries the plan`() {
        val detail = card.dropLast(1) +
            ""","itinerary":{"destination":"Chicago","start_date":"2026-09-07",""" +
            """"end_date":"2026-09-08","currency":"USD","days":[],"notes":[],""" +
            """"total_estimated_cost":60.0}}"""

        val parsed = ApiJson.json.decodeFromString<SharedPlanDetail>(detail)

        assertEquals("Chicago", parsed.itinerary.destination)
        assertEquals(60.0, parsed.itinerary.totalEstimatedCost)
    }

    @Test
    fun `a publish body states neither the trip's facts nor its author`() {
        // Destination, dates and totals are derived server-side from the itinerary, so a
        // client cannot advertise a trip as somewhere it is not. The author is taken from
        // the bearer token -- the field does not exist here at all, which is what makes
        // the check real rather than a formality.
        val body = ApiJson.json.encodeToString(
            PublishRequest(
                request = "2 days in Chicago",
                note = "Rainy-day friendly",
                itinerary = Itinerary(
                    destination = "Chicago",
                    startDate = "2026-09-07",
                    endDate = "2026-09-08",
                    currency = "USD",
                    days = emptyList(),
                ),
            ),
        )

        assertFalse(body.contains("author"), body)
        assertFalse(body.contains(""""day_count""""), body)
        assertFalse(body.contains(""""total_cost""""), body)
        assertTrue(body.contains("Chicago"), body)
    }

    @Test
    fun `a newer server field does not break an older client`() {
        val parsed = ApiJson.json.decodeFromString<SharedPlanCard>(
            card.dropLast(1) + ""","reported_count":3}""",
        )

        assertEquals("Chicago", parsed.destination)
    }
}
