package com.wandergent.app.ui

import com.wandergent.app.data.SharedPlanCard
import kotlin.test.Test
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * Who owns a community post.
 *
 * Small enough to look not worth testing, and it shipped a bug anyway: the app carries two
 * different ids for one person, and this is the only place they meet.
 */
class CardOwnershipTest {

    private fun card(authorId: String) = SharedPlanCard(
        id = "post-1",
        authorId = authorId,
        authorName = "someone",
        destination = "Los Angeles",
        startDate = "2026-09-14",
        endDate = "2026-09-15",
        dayCount = 2,
        totalCost = 65.0,
        currency = "USD",
        createdAt = "2026-09-01T10:00:00Z",
    )

    private val account = "0f6869e7-c1a1-4616-8729-82557ccd6f43"

    @Test
    fun `my own post is mine`() {
        assertTrue(ownsCard(account, card(account)))
    }

    @Test
    fun `someone else's post is not`() {
        assertFalse(ownsCard(account, card("1d58ad84-4502-4889-84ad-885d52bb106a")))
    }

    @Test
    fun `a local row id does not match a server account id`() {
        // The live bug. The library partitions on a local Room row id ("1", "2"); the feed
        // identifies authors by an opaque server id. Passing the wrong one in showed other
        // people's posts as the reader's own, with a withdraw button on each.
        //
        // This only demonstrates that the two id spaces do not collide in practice -- the
        // function compares strings and cannot tell which space a caller passed. What keeps
        // the bug fixed is the call site, which is why `accountId` is a separate field from
        // the local user id rather than one nullable value doing both jobs.
        assertFalse(ownsCard("1", card(account)))
        assertFalse(ownsCard("2", card(account)))
    }

    @Test
    fun `signed out owns nothing`() {
        // Null and empty both mean "no account". Empty is the one that bites: a blank string
        // compared against a blank author id would make every post look like the reader's.
        assertFalse(ownsCard(null, card(account)))
        assertFalse(ownsCard("", card(account)))
        assertFalse(ownsCard("", card("")))
    }
}
