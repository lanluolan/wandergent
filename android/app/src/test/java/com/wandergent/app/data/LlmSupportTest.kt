package com.wandergent.app.data

import kotlinx.serialization.json.Json
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * What the app assumes about a backend's AI account, and what it does when it cannot ask.
 *
 * The failure this guards is quiet in both directions: assume too much and someone with a
 * perfectly configured backend is blocked from saving; assume too little and a
 * half-filled account saves cleanly and fails on the first plan, minutes later.
 */
class LlmSupportTest {

    private val json = Json { ignoreUnknownKeys = true }

    @Test
    fun `the backend's answer is read as sent`() {
        val body = """{"status":"ok","app":"Wandergent",
                       "llm":{"key":false,"endpoint":true,"model":false}}"""

        val llm = json.decodeFromString<HealthDto>(body).llm

        assertEquals(LlmSupport(key = false, endpoint = true, model = false), llm)
    }

    @Test
    fun `a server too old to answer is treated as able to supply everything`() {
        // Permissive on purpose: this is what the app did before it asked at all, and an
        // absent answer is not evidence that the backend lacks an account. A wrong
        // requirement here would block someone whose backend is fine.
        val llm = json.decodeFromString<HealthDto>("""{"status":"ok","app":"Wandergent"}""").llm

        assertEquals(LlmSupport.UNKNOWN, llm)
        assertTrue(llm.endpoint && llm.model && llm.key)
    }

    @Test
    fun `a deployment carrying nothing reports nothing`() {
        val body = """{"status":"ok","app":"W","llm":{"key":false,"endpoint":false,"model":false}}"""

        val llm = json.decodeFromString<HealthDto>(body).llm

        // The shipping configuration: the traveller supplies all three.
        assertTrue(!llm.key && !llm.endpoint && !llm.model)
    }
}
