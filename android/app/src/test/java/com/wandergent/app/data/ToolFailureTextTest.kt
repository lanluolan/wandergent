package com.wandergent.app.data

import kotlinx.serialization.json.Json
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * What a traveller is told when a tool call did not deliver.
 *
 * The failure being guarded is specific and was seen live: the backend's own sentence,
 * written for the model, reached the itinerary complete with an upstream URL and a link
 * to MDN's page on HTTP 400. The wording now lives here and the wire carries a code.
 */
class ToolFailureTextTest {

    private val json = Json { ignoreUnknownKeys = true }

    @Test
    fun `every code the backend can send has wording of its own`() {
        val codes = listOf(
            "not_configured", "timed_out", "rate_limited", "unavailable",
            "no_match", "no_coverage", "bad_request", "unknown_tool",
        )

        val rendered = codes.map { toolFailureText(it) }

        assertEquals(codes.size, rendered.distinct().size, rendered.toString())
        // None of them is developer wording, and none names a service or a status line.
        assertTrue(rendered.none { it.contains("http", ignoreCase = true) }, rendered.toString())
        assertTrue(rendered.none { it.contains("_") }, rendered.toString())
    }

    @Test
    fun `a trip saved before codes existed still explains itself`() {
        // Room stores the response verbatim, so an old saved plan carries the old field
        // and no code. Silence would be worse than the old sentence, which was accurate.
        val body = """{"name":"get_weather_forecast","ok":false,
                       "error":"weather service timed out"}"""

        val call = json.decodeFromString<ToolCallRecord>(body)

        assertEquals(null, call.code)
        assertEquals("weather service timed out", toolFailureText(call.code, call.error))
    }

    @Test
    fun `a code the backend invents later does not render as blank`() {
        // The server ships ahead of the app; an unknown code has to say *something*.
        assertTrue(toolFailureText("invented_later").isNotBlank())
        assertTrue(toolFailureText(null, legacy = null).isNotBlank())
    }

    @Test
    fun `the code wins over a legacy sentence when both are present`() {
        val text = toolFailureText("timed_out", legacy = "weather service unavailable: https://x")

        assertFalse(text.contains("https"))
        assertEquals(toolFailureText("timed_out"), text)
    }
}
