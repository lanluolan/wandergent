package com.wandergent.app.data

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * Turning a 429 into something a person can act on.
 *
 * The server sends `Retry-After` precisely so the client does not have to guess. A message
 * without a number in it produces someone tapping the button every two seconds, which is
 * the behaviour the limit exists to stop.
 */
class RateLimitMessageTest {

    @Test
    fun `a short wait is given in seconds`() {
        assertEquals(
            "Too many requests. Try again in 20s.",
            tooManyRequests("Too many requests.", 20),
        )
    }

    @Test
    fun `a long wait is rounded up to minutes`() {
        // "Try again in 3540s" is technically true and useless.
        assertTrue(tooManyRequests(null, 3540).contains("59 min"), tooManyRequests(null, 3540))
    }

    @Test
    fun `rounding up never tells someone to come back too early`() {
        assertTrue(tooManyRequests(null, 91).contains("2 min"), tooManyRequests(null, 91))
    }

    @Test
    fun `a missing header still produces a sentence`() {
        val message = tooManyRequests(null, null)

        assertTrue(message.isNotBlank())
        assertTrue(message.contains("Try again"), message)
    }

    @Test
    fun `the server's own explanation is kept`() {
        val message = tooManyRequests("too many requests -- the limit is 5 per 60 min", 30)

        assertTrue(message.contains("5 per 60 min"), message)
        assertTrue(message.contains("30s"), message)
    }
}
