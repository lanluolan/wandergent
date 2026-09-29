package com.wandergent.app.data

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class FeedbackDtoTest {
    @Test
    fun `plan feedback contains no itinerary or user message`() {
        val encoded = ApiJson.json.encodeToString(
            FeedbackRequest(
                runId = "a".repeat(32),
                helpful = false,
                category = "validator_miss",
            ),
        )
        assertEquals(
            """{"run_id":"${"a".repeat(32)}","helpful":false,"category":"validator_miss"}""",
            encoded,
        )
        assertFalse(encoded.contains("message"))
        assertFalse(encoded.contains("itinerary"))
    }

    @Test
    fun `activity feedback carries a stable index pair`() {
        val encoded = ApiJson.json.encodeToString(
            FeedbackRequest(
                runId = "b".repeat(32),
                helpful = true,
                dayIndex = 1,
                activityIndex = 2,
            ),
        )
        assertTrue(encoded.contains(""""day_index":1"""))
        assertTrue(encoded.contains(""""activity_index":2"""))
    }
}
