package com.wandergent.app.data

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class ServerConfigTest {

    @Test
    fun `a bare host and port is read as http in a debug build`() {
        // What someone reads off `ipconfig` and types in. A debug build talks to a dev
        // machine on a local network, so https would be the surprising assumption.
        // These run against the debug variant, so the default scheme is the debug one.
        assertEquals("http://192.168.1.10:8000/", ServerConfig.parse("192.168.1.10:8000")?.toString())
        assertEquals("http://10.0.2.2:8000/", ServerConfig.parse("10.0.2.2:8000")?.toString())
    }

    @Test
    fun `a bare host is read as https when the build denies cleartext`() {
        // The release branch. Guessing http there produces an address the platform
        // refuses to dial, and the refusal is indistinguishable from the server
        // being down -- so the guess has to match what the build actually permits.
        assertEquals(
            "https://wandergent.example.com/",
            ServerConfig.parse("wandergent.example.com", scheme = "https")?.toString(),
        )
    }

    @Test
    fun `an explicit http address survives the release scheme`() {
        // The assumed scheme fills a gap; it never overrides what someone typed. A
        // deliberate http address stays http and fails loudly on the network layer,
        // rather than being silently rewritten into a different server.
        assertEquals(
            "http://192.168.1.10:8000/",
            ServerConfig.parse("http://192.168.1.10:8000", scheme = "https")?.toString(),
        )
    }

    @Test
    fun `an explicit scheme is kept`() {
        assertEquals("https://api.example.com/", ServerConfig.parse("https://api.example.com")?.toString())
    }

    @Test
    fun `the address always ends in a slash`() {
        // Retrofit resolves paths against the base; without the slash the last segment
        // is replaced rather than appended.
        assertEquals("http://127.0.0.1:8000/", ServerConfig.parse("http://127.0.0.1:8000")?.toString())
        assertEquals("http://127.0.0.1:8000/", ServerConfig.parse("http://127.0.0.1:8000/")?.toString())
    }

    @Test
    fun `surrounding whitespace is forgiven`() {
        assertEquals("http://127.0.0.1:8000/", ServerConfig.parse("  127.0.0.1:8000  ")?.toString())
    }

    @Test
    fun `nonsense is rejected rather than half-accepted`() {
        assertNull(ServerConfig.parse(""))
        assertNull(ServerConfig.parse("   "))
        assertNull(ServerConfig.parse("http://"))
        assertFalse(ServerConfig.isValid("not a url at all"))
        assertTrue(ServerConfig.isValid("127.0.0.1:8000"))
    }

    @Test
    fun `setting an unusable address leaves the old one alone`() {
        // A silent no-op beats pointing the app at nothing: the previous address still
        // worked, and the dialog will not let an invalid one be saved anyway.
        ServerConfig.reset()
        val before = ServerConfig.baseUrl
        ServerConfig.set("::::")
        assertEquals(before, ServerConfig.baseUrl)
    }

    @Test
    fun `reset goes back to what the APK was built with`() {
        ServerConfig.set("http://192.168.1.10:8000")
        assertEquals("http://192.168.1.10:8000/", ServerConfig.baseUrl)
        ServerConfig.reset()
        assertEquals(ServerConfig.default, ServerConfig.baseUrl)
    }
}
