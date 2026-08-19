package com.wandergent.app.data

import kotlin.test.Test
import kotlin.test.assertEquals

/**
 * What travels with a shared trip, and what does not.
 *
 * The original request is the sentence the traveller typed, and it is where the private
 * half of a trip ends up -- "honeymoon", "my mother cannot manage stairs", "budget is
 * tight since the move", the names of whoever is coming. It was published verbatim and
 * rendered to every reader as "Original request: ...", while the share dialog promised
 * only that the destination and the display name would be visible.
 *
 * A privacy default is worth a test precisely because nothing else fails when it flips:
 * the app works either way, and the person who finds out is the one whose sentence is
 * already public.
 */
class SharedRequestTest {

    private val private = "honeymoon in New Orleans, my mother is coming, budget is tight"

    @Test
    fun `nothing goes out unless it was asked for`() {
        assertEquals("", sharedRequest(private, include = false))
    }

    @Test
    fun `an explicit yes shares it`() {
        assertEquals(private, sharedRequest(private, include = true))
    }

    @Test
    fun `whitespace is not a share`() {
        // A blank request rendered as an empty "Original request:" line on every card.
        assertEquals("", sharedRequest("   ", include = true))
    }

    @Test
    fun `the text is passed through unaltered`() {
        // No truncation and no summarising: the traveller saw these exact words in the
        // dialog before agreeing, so these are the words that get shared.
        val awkward = "  3 days, \"cheap\" hotels & no hiking  "

        assertEquals(awkward.trim(), sharedRequest(awkward, include = true))
    }
}
