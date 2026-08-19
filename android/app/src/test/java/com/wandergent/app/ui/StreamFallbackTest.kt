package com.wandergent.app.ui

import kotlin.test.Test
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * When a failed stream is worth retrying on the non-streaming endpoint.
 *
 * The cost of getting this wrong is invisible from the app: a wrong `true` doubles every
 * refused request, and on a 429 it spends the caller's allowance twice for one attempt.
 */
class StreamFallbackTest {

    @Test
    fun `nothing arrived and no status means the transport may be at fault`() {
        // The case the fallback exists for: a proxy that buffers SSE into one lump, so
        // the stream never delivers anything and never reports a status either.
        assertTrue(shouldFallBack(eventsSeen = 0, status = null))
    }

    @Test
    fun `a 4xx is the server answering, not the transport failing`() {
        // No API key, rejected request, spent allowance. The plain endpoint runs the same
        // checks and refuses the same way.
        assertFalse(shouldFallBack(eventsSeen = 0, status = 400))
        assertFalse(shouldFallBack(eventsSeen = 0, status = 422))
        // The expensive one: this request would be counted against the allowance too.
        assertFalse(shouldFallBack(eventsSeen = 0, status = 429))
        assertFalse(shouldFallBack(eventsSeen = 0, status = 499))
    }

    @Test
    fun `a 5xx could still be something in between the app and the app server`() {
        assertTrue(shouldFallBack(eventsSeen = 0, status = 502))
        assertTrue(shouldFallBack(eventsSeen = 0, status = 504))
    }

    @Test
    fun `once an event has arrived the transport has proved itself`() {
        // Whatever failed after that is not a reason to run the whole plan again.
        assertFalse(shouldFallBack(eventsSeen = 1, status = null))
        assertFalse(shouldFallBack(eventsSeen = 12, status = 502))
    }
}
