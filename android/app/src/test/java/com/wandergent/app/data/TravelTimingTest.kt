package com.wandergent.app.data

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class TravelTimingTest {
    private val payload = """
        {
          "itinerary": {
            "destination": "Los Angeles", "start_date": "2026-11-01", "end_date": "2026-11-02",
            "timing": {
              "arrival": {"location": "Los Angeles", "at": "2026-11-01T18:00:00-08:00", "buffer_minutes": 60},
              "hotel_stays": [{"id": "h1", "hotel": "Hotel", "check_in": "2026-11-01T19:00:00-08:00", "check_out": "2026-11-02T11:00:00-08:00"}],
              "journeys": [{"id": "j1", "origin": "Tokyo", "destination": "Los Angeles", "mode": "FLIGHT", "departure": "2026-11-02T01:00:00+09:00", "arrival": "2026-11-01T18:00:00-08:00", "timing_confidence": "confirmed", "arrival_buffer_minutes": 60}]
            },
            "days": [{"date": "2026-11-02", "summary": "Travel", "activities": [{
              "title": "Flight", "start_time": "01:00", "end_time": "18:00", "category": "transport",
              "start_at": "2026-11-02T01:00:00+09:00", "end_at": "2026-11-01T18:00:00-08:00",
              "journey_id": "j1", "travel_mode": "FLIGHT"
            }]}]
          },
          "constraints": {"journeys": [{"id": "j1", "origin": "Tokyo", "destination": "Los Angeles", "mode": "FLIGHT", "departure": "2026-11-02T01:00:00+09:00", "arrival": "2026-11-01T18:00:00-08:00"}]}
        }
    """.trimIndent()

    @Test
    fun `saved itinerary and constraints preserve absolute travel times on round trip`() {
        val response = ApiJson.json.decodeFromString<PlanResponse>(payload)
        val restored = ApiJson.json.decodeFromString<PlanResponse>(ApiJson.json.encodeToString(response))
        assertEquals(response, restored)
        assertEquals("j1", restored.itinerary!!.days.first().activities.first().journeyId)
        assertEquals("h1", restored.itinerary!!.timing!!.hotelStays!!.first().id)
        assertEquals("estimate", restored.constraints!!.journeys!!.first().timingConfidence)
    }

    @Test
    fun `date line display retains both local dates and both utc offsets`() {
        val response = ApiJson.json.decodeFromString<PlanResponse>(payload)
        val label = activityTimeLabel(response.itinerary!!.days.first().activities.first())
        assertEquals("2026-11-02 01:00 UTC+09:00 → 2026-11-01 18:00 UTC-08:00", label)
    }

    @Test
    fun `timing card distinguishes supplied confirmation from estimates and shows hotel windows`() {
        val response = ApiJson.json.decodeFromString<PlanResponse>(payload)
        val lines = timingLines(response.itinerary!!.timing!!)
        assertTrue(lines.any { "check-in from" in it && "check-out by" in it })
        assertTrue(lines.any { "Times supplied as confirmed" in it && "60 min after" in it })
        val estimated = timingLines(TimingContext(journeys = response.constraints!!.journeys))
        assertTrue(estimated.single().contains("Estimated times"))
    }

    @Test
    fun `legacy plans keep the original local clock label`() {
        assertEquals("10:00 – 11:00", activityTimeLabel(Activity("10:00", "11:00", "Visit")))
    }

    @Test
    fun `estimated journey warning is advisory and clock uncertainty blocks`() {
        assertTrue(ValidationReport(listOf(Violation("journey_time_estimated", "estimated"))).ok)
        assertTrue(!ValidationReport(listOf(Violation("temporal_unverified", "unknown"))).ok)
    }
}
