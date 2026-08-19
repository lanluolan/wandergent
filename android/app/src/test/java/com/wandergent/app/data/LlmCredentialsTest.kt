package com.wandergent.app.data

import okhttp3.HttpUrl.Companion.toHttpUrl
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * The rules around a credential the app holds on someone's behalf.
 *
 * Worth testing rather than reading: every one of these is a quiet failure. A key that
 * leaks into a log or onto a LAN does not look like a bug from inside the app, and a key
 * attached to the wrong request does not either.
 */
class LlmCredentialsTest {

    private val key = "sk-traveller-owns-this-9876"

    @Test
    fun `the key never appears in the string form`() {
        // Data classes get printed by logging, crash reporters and debuggers without
        // anyone choosing to print them.
        val text = LlmCredentials(key, model = "some-model").toString()

        assertFalse(text.contains(key), text)
        assertTrue(text.contains("***9876"), text)
        assertTrue(text.contains("some-model"), text)
    }

    @Test
    fun `only the last four characters are offered to the UI`() {
        assertEquals("9876", LlmCredentials(key).hint)
    }

    @Test
    fun `no key means nothing to send`() {
        assertFalse(LlmCredentials.NONE.isSet)
        assertFalse(LlmCredentials("   ").isSet)
        assertTrue(LlmCredentials(key).isSet)
    }

    @Test
    fun `https carries a key, cleartext to a real host does not`() {
        assertTrue(LlmCredentials.safeToSend("https://wandergent.example.com/plan".toHttpUrl()))
        // The failure this prevents: a debug build pointed at a LAN address puts the
        // traveller's API key on a shared Wi-Fi in the clear.
        assertFalse(LlmCredentials.safeToSend("http://192.168.1.10:8000/plan".toHttpUrl()))
        assertFalse(LlmCredentials.safeToSend("http://wandergent.example.com/plan".toHttpUrl()))
    }

    @Test
    fun `the key goes to planning and nowhere else`() {
        val credentials = LlmCredentials(key)

        assertEquals(
            key,
            credentials.headersFor("http://127.0.0.1:8000/plan/stream".toHttpUrl())["X-LLM-Api-Key"],
        )
        // Signing in and the community feed have no use for it, and a secret should
        // reach exactly the handlers that need it.
        assertTrue(credentials.headersFor("http://127.0.0.1:8000/auth/login".toHttpUrl()).isEmpty())
        assertTrue(credentials.headersFor("http://127.0.0.1:8000/community".toHttpUrl()).isEmpty())
        assertTrue(credentials.headersFor("http://127.0.0.1:8000/day-map".toHttpUrl()).isEmpty())
    }

    @Test
    fun `a cleartext LAN address gets no key even on a planning path`() {
        assertTrue(
            LlmCredentials(key).headersFor("http://192.168.1.10:8000/plan".toHttpUrl()).isEmpty()
        )
    }

    @Test
    fun `blank optional fields are left out rather than sent empty`() {
        // The backend refuses a model or endpoint that arrives without a key, and an
        // empty string is a value -- sending one would turn "unset" into a 400.
        val url = "http://127.0.0.1:8000/plan".toHttpUrl()

        assertEquals(setOf("X-LLM-Api-Key"), LlmCredentials(key).headersFor(url).keys)
        assertEquals(
            setOf("X-LLM-Api-Key", "X-LLM-Model"),
            LlmCredentials(key, model = "gpt-4o").headersFor(url).keys,
        )
        assertEquals(
            setOf("X-LLM-Api-Key", "X-LLM-Base-Url", "X-LLM-Model"),
            LlmCredentials(key, "https://api.example.com/v1", "gpt-4o").headersFor(url).keys,
        )
    }

    @Test
    fun `no key means no headers at all`() {
        assertTrue(
            LlmCredentials.NONE.headersFor("http://127.0.0.1:8000/plan".toHttpUrl()).isEmpty()
        )
    }

    @Test
    fun `loopback is exempt, because that is the adb reverse tunnel`() {
        // The debug default. It is a USB tunnel, not a network -- refusing it would mean
        // the feature could never be tested on the one setup the project actually uses.
        assertTrue(LlmCredentials.safeToSend("http://127.0.0.1:8000/plan".toHttpUrl()))
        assertTrue(LlmCredentials.safeToSend("http://localhost:8000/plan".toHttpUrl()))
    }
}
