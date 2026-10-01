package com.wandergent.app.data

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * Contract tests for the wire types.
 *
 * [REAL_RESPONSE] is not hand-written: it is emitted by the backend's own Pydantic
 * models via `PlanResult.model_dump_json()`. That is the point -- "the Kotlin compiles"
 * says nothing about whether these classes can parse what the server actually sends,
 * and only the server's serialiser knows what that looks like. Regenerate it whenever a
 * backend model changes, by constructing an `Itinerary` and dumping a `PlanResult`.
 */
class PlanDtoTest {
    @Test
    fun `user confirmed lodging survives revision request round trip`() {
        val response = json.decodeFromString<PlanResponse>(
            """{"constraints":{"lodging_arranged":true}}"""
        )
        assertEquals(true, response.constraints?.lodgingArranged)
        val request = PlanRequest(message = "Change lunch", previousConstraints = response.constraints)
        val restored = json.decodeFromString<PlanRequest>(json.encodeToString(request))
        assertEquals(true, restored.previousConstraints?.lodgingArranged)
        assertNull(json.decodeFromString<TripConstraints>("{}").lodgingArranged)
        assertEquals(false, json.decodeFromString<TripConstraints>(
            """{"lodging_arranged":false}"""
        ).lodgingArranged)
    }

    @Test
    fun `fact coverage preserves timestamps and estimated prices`() {
        val response = json.decodeFromString<PlanResponse>("""
            {"trace_id":"0123456789abcdef0123456789abcdef", "activity_evidence":[
              {"day_index":0,"activity_index":1,"source":"Google Places",
               "collected_at":"2026-09-29T10:00:00+00:00","venue_verified":true,
               "hours_available":true,"price_confidence":"estimate",
               "recheck_before_departure":true}
            ]}
        """)
        val evidence = response.activityEvidence.single()
        assertEquals("estimate", evidence.priceConfidence)
        assertEquals("2026-09-29T10:00:00+00:00", evidence.collectedAt)
        assertTrue(evidence.venueVerified)
        assertTrue(evidence.recheckBeforeDeparture)
        assertEquals(response, json.decodeFromString<PlanResponse>(json.encodeToString(response)))
    }

    private val json = ApiJson.json

    @Test
    fun `parses a real backend response`() {
        val response = json.decodeFromString<PlanResponse>(REAL_RESPONSE)

        assertEquals("a".repeat(32), response.runId)
        assertTrue(response.feedbackAvailable)
        assertEquals(900.0, response.constraints?.budget)
        assertEquals(listOf("WALK", "TRANSIT"), response.constraints?.allowedModes)
        val itinerary = requireNotNull(response.itinerary)
        assertEquals("Los Angeles", itinerary.destination)
        assertEquals("2026-09-14", itinerary.startDate)
        assertEquals(2, itinerary.travelers)
        assertEquals(900.0, itinerary.budget)
        // Server-computed totals must survive the round trip untouched.
        assertEquals(65.0, itinerary.totalEstimatedCost)
        assertEquals(45.0, itinerary.days[0].estimatedCost)

        val first = itinerary.days[0].activities[0]
        assertEquals("09:00", first.startTime)
        assertEquals("Santa Monica Pier", first.title)
        assertEquals(false, first.indoor)
        assertNull(first.travelMode)
        assertNull(first.notes)

        assertEquals(listOf("Book the Getty parking slot ahead; the museum itself is free."), itinerary.notes)
        assertEquals("get_weather_forecast", response.toolCalls[0].name)
        assertTrue(response.toolCalls[0].ok)
    }

    @Test
    fun `highlights survive the round trip and default to empty`() {
        val itinerary = requireNotNull(json.decodeFromString<PlanResponse>(REAL_RESPONSE).itinerary)

        // The restaurant carries dish recommendations...
        assertEquals(
            listOf("Tacos at Las Morelianas", "Egg sandwich at Eggslut"),
            itinerary.days[0].activities[1].highlights,
        )
        // ...while a sight that has none serialises as [], not null.
        assertTrue(itinerary.days[0].activities[0].highlights.isEmpty())
    }

    @Test
    fun `an overnight trip carries its accommodation`() {
        val itinerary = requireNotNull(json.decodeFromString<PlanResponse>(REAL_RESPONSE).itinerary)

        val stay = itinerary.days[0].activities.single { it.category == "accommodation" }
        assertEquals("939 S Figueroa St, Los Angeles, CA 90015", stay.location)
    }

    @Test
    fun `indoor stays tri-state`() {
        val itinerary = requireNotNull(json.decodeFromString<PlanResponse>(REAL_RESPONSE).itinerary)
        // Day 2's activity omits `indoor`; mapping it to a non-null Boolean would
        // silently turn "unknown" into "outdoors".
        assertNull(itinerary.days[1].activities[0].indoor)
        assertEquals(true, itinerary.days[0].activities[1].indoor)
    }

    @Test
    fun `a 200 with no itinerary is still parseable`() {
        val body = """
            {"itinerary": null, "tool_calls": [],
             "warnings": [{"code": "no_itinerary",
                           "detail": "the model did not return a valid itinerary"}],
             "raw_reply": "{}"}
        """.trimIndent()

        val response = json.decodeFromString<PlanResponse>(body)

        assertNull(response.itinerary)
        assertEquals("{}", response.rawReply)
        assertEquals(listOf("no_itinerary"), response.warnings.map { it.code })
    }

    @Test
    fun `tool efficiency metrics survive saved response round trips`() {
        val body = """
            {"itinerary": null, "warnings": [],
             "tool_calls": [{"name":"search_places","arguments":{},"ok":true,
                              "cache_status":"shared","cache_age_seconds":12.5,
                              "duration_ms":0,"attempts":0,"contributed":true}],
             "tool_usage": {"requested_calls":2,"executed_calls":1,"cache_hits":1,
                              "run_cache_hits":0,"shared_cache_hits":1,"failed_calls":0,
                              "retried_calls":0,"contributed_calls":1,"dropped_calls":0,
                              "duration_ms":80,"calls_by_tool":{"search_places":2},
                              "cache_hit_rate":0.5}}
        """.trimIndent()

        val response = json.decodeFromString<PlanResponse>(body)

        assertEquals("shared", response.toolCalls.single().cacheStatus)
        assertEquals(1, response.toolUsage.sharedCacheHits)
        assertEquals(0.5, response.toolUsage.cacheHitRate)
    }

    @Test
    fun `a warning carries the numbers behind it, not just a sentence`() {
        // The point of the whole shape: the client can say "some lookups were skipped"
        // in its own words because the parameters arrive separately from the prose.
        val body = """
            {"itinerary": null, "tool_calls": [], "raw_reply": null,
             "warnings": [{"code": "tool_calls_dropped",
                           "detail": "reached the 16-call tool budget; skipped 3 further call(s) to search_places",
                           "budget": 16, "dropped_calls": 3, "dropped_tools": ["search_places"]}]}
        """.trimIndent()

        val warning = json.decodeFromString<PlanResponse>(body).warnings.single()

        assertEquals("tool_calls_dropped", warning.code)
        assertEquals(16, warning.budget)
        assertEquals(3, warning.droppedCalls)
        assertEquals(listOf("search_places"), warning.droppedTools)
        // What the traveller reads is ours, and it is not the developer sentence.
        assertEquals(false, warningText(warning).contains("tool budget"))
    }

    @Test
    fun `a trip saved before warnings had codes still opens`() {
        // Room stores the response verbatim, so every plan in an existing library holds
        // warnings as bare strings. Changing the type without this would make those
        // trips fail to parse and vanish from the list.
        val body = """
            {"itinerary": null, "tool_calls": [], "raw_reply": null,
             "warnings": ["stopped calling tools after 4 rounds"]}
        """.trimIndent()

        val warning = json.decodeFromString<PlanResponse>(body).warnings.single()

        assertEquals(RunWarning.LEGACY, warning.code)
        assertEquals("stopped calling tools after 4 rounds", warning.detail)
        // Nothing better exists to show for it: the wording is all that was stored.
        assertEquals("stopped calling tools after 4 rounds", warningText(warning))
    }

    @Test
    fun `the three budget codes collapse to one sentence, so the card must dedupe`() {
        // Seen on a live plan: the run spent its call budget *and* dropped a call, so two
        // distinct codes arrived and rendered identically -- the same caveat printed
        // twice, one under the other. The collapse is deliberate; what it also creates is
        // duplicates, and `WarningsCard` distinct()s on this rendered text because of it.
        val budgetCodes = listOf("tool_calls_dropped", "tool_calls_spent", "tool_rounds_spent")

        val rendered = budgetCodes.map { warningText(RunWarning(code = it, detail = "dev wording")) }

        assertEquals(1, rendered.distinct().size, rendered.toString())
    }

    @Test
    fun `an unknown warning code from a newer backend renders its detail`() {
        val body = """
            {"itinerary": null, "tool_calls": [], "raw_reply": null,
             "warnings": [{"code": "invented_later", "detail": "something new happened"}]}
        """.trimIndent()

        val warning = json.decodeFromString<PlanResponse>(body).warnings.single()

        assertEquals("something new happened", warningText(warning))
    }

    @Test
    fun `unknown fields from a newer backend do not break an older client`() {
        val body = """
            {"itinerary": null, "tool_calls": [], "warnings": [], "raw_reply": null,
             "streaming_id": "abc", "server_version": 7}
        """.trimIndent()

        val response = json.decodeFromString<PlanResponse>(body)

        assertNull(response.itinerary)
    }

    @Test
    fun `error envelope parses`() {
        assertEquals(
            "OPENAI_API_KEY is not set; put your key in backend/.env",
            ApiJson.parseErrorDetail("""{"detail":"OPENAI_API_KEY is not set; put your key in backend/.env"}"""),
        )
        assertNull(ApiJson.parseErrorDetail("not json at all"))
    }
}

private const val REAL_RESPONSE = """
{
  "run_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "feedback_available": true,
  "constraints": {
    "budget": 900.0,
    "currency": "USD",
    "start_date": "2026-09-14",
    "end_date": "2026-09-15",
    "days": 2,
    "travelers": 2,
    "allowed_modes": ["WALK", "TRANSIT"]
  },
  "itinerary": {
    "destination": "Los Angeles",
    "start_date": "2026-09-14",
    "end_date": "2026-09-15",
    "travelers": 2,
    "currency": "USD",
    "budget": 900.0,
    "days": [
      {
        "date": "2026-09-14",
        "summary": "Downtown and the coast",
        "weather": "Sunny, 26C",
        "activities": [
          {
            "start_time": "09:00",
            "end_time": "11:30",
            "title": "Santa Monica Pier",
            "category": "sightseeing",
            "location": "200 Santa Monica Pier, Santa Monica, CA 90401",
            "travel_mode": null,
            "indoor": false,
            "estimated_cost": 0.0,
            "highlights": [],
            "notes": null
          },
          {
            "start_time": "12:00",
            "end_time": "13:30",
            "title": "Lunch at Grand Central Market",
            "category": "food",
            "location": null,
            "travel_mode": null,
            "indoor": true,
            "estimated_cost": 45.0,
            "highlights": [
              "Tacos at Las Morelianas",
              "Egg sandwich at Eggslut"
            ],
            "notes": null
          },
          {
            "start_time": "21:00",
            "end_time": "22:00",
            "title": "Check in at Hotel Figueroa",
            "category": "accommodation",
            "location": "939 S Figueroa St, Los Angeles, CA 90015",
            "travel_mode": null,
            "indoor": true,
            "estimated_cost": 0.0,
            "highlights": [],
            "notes": null
          }
        ],
        "estimated_cost": 45.0
      },
      {
        "date": "2026-09-15",
        "summary": "Museums, indoors while it rains",
        "weather": null,
        "activities": [
          {
            "start_time": "10:00",
            "end_time": "13:00",
            "title": "The Getty Center",
            "category": "sightseeing",
            "location": null,
            "travel_mode": null,
            "indoor": null,
            "estimated_cost": 20.0,
            "highlights": [],
            "notes": null
          }
        ],
        "estimated_cost": 20.0
      }
    ],
    "notes": [
      "Book the Getty parking slot ahead; the museum itself is free."
    ],
    "total_estimated_cost": 65.0
  },
  "tool_calls": [
    {
      "name": "get_weather_forecast",
      "arguments": {
        "city": "Los Angeles",
        "start_date": "2026-09-14",
        "end_date": "2026-09-15"
      },
      "ok": true,
      "error": null
    }
  ],
  "warnings": [],
  "raw_reply": null,
  "validation": {
    "violations": []
  },
  "usage": {
    "llm_calls": 3,
    "prompt_tokens": 6161,
    "completion_tokens": 3322,
    "cached_prompt_tokens": 4480,
    "reasoning_tokens": 0,
    "tokens_by_model": {
      "mimo-v2.5-pro": 9483
    },
    "total_tokens": 9483
  }
}
"""
