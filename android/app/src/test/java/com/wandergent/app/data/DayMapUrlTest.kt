package com.wandergent.app.data

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * The two URLs a day can be opened with.
 *
 * Worth testing rather than eyeballing: both are built by string assembly, and a
 * dropped stop shows up on screen as a plausible-looking route rather than as an error.
 */
class DayMapUrlTest {

    private val day = listOf(
        "Griffith Observatory",
        "Echo Park, Los Angeles",
        "Grand Central Market, Los Angeles",
        "Hotel Figueroa, Los Angeles",
    )

    @Test
    fun `every stop reaches the route, ends included`() {
        val url = DayMapClient.externalRouteUrl(day)!!

        assertTrue(url.startsWith("https://www.google.com/maps/dir/?"), url)
        // First and last are the ends; everything between is a waypoint. Losing the
        // last stop is the easy mistake here -- it is the hotel you sleep at.
        assertTrue(url.contains("origin=Griffith%20Observatory"), url)
        assertTrue(url.contains("destination=Hotel%20Figueroa%2C%20Los%20Angeles"), url)
        assertTrue(
            url.contains("waypoints=Echo%20Park%2C%20Los%20Angeles%7CGrand%20Central%20Market%2C%20Los%20Angeles"),
            url,
        )
        assertTrue(url.contains("travelmode=walking"), url)
    }

    @Test
    fun `two stops need no waypoints at all`() {
        val url = DayMapClient.externalRouteUrl(day.take(2))!!

        assertTrue(url.contains("origin=Griffith%20Observatory"), url)
        assertTrue(url.contains("destination=Echo%20Park%2C%20Los%20Angeles"), url)
        assertTrue(!url.contains("waypoints"), url)
    }

    @Test
    fun `one stop is not a route`() {
        assertNull(DayMapClient.externalRouteUrl(day.take(1)))
        assertNull(DayMapClient.externalRouteUrl(emptyList()))
        assertNull(DayMapClient.externalRouteUrl(listOf("", "   ")))
    }

    @Test
    fun `the interactive page is asked of our own backend, one place per stop`() {
        val url = DayMapClient.interactiveUrl(day)!!

        assertTrue(url.startsWith(ServerConfig.baseUrl + "day-map/interactive?"), url)
        assertEquals(4, Regex("place=").findAll(url).count())
        // The Maps key lives on the server; nothing key-shaped is assembled here.
        assertTrue(!url.contains("key="), url)
    }

    // --- which activities become stops ------------------------------------------------

    private fun activity(title: String, category: String, location: String?) = Activity(
        startTime = "09:00",
        endTime = "10:00",
        title = title,
        category = category,
        location = location,
    )

    @Test
    fun `a walk between two places is not a stop`() {
        // A transport activity's location is the leg, not a place. Google cannot resolve
        // "W 30th St to W 53rd St", draws the day and stamps a Map error over it.
        val stops = DayMapClient.stops(
            listOf(
                activity("Coffee", "food", "251 W 30th St, New York, NY 10001"),
                activity("Walk to MoMA", "transport", "W 30th St to W 53rd St"),
                activity("MoMA", "sightseeing", "11 W 53rd St, New York, NY 10019"),
            )
        )

        assertEquals(
            listOf("251 W 30th St, New York, NY 10001", "11 W 53rd St, New York, NY 10019"),
            stops,
        )
    }

    @Test
    fun `the same place twice in a row is one stop`() {
        val stops = DayMapClient.stops(
            listOf(
                activity("Lunch", "food", "Grand Central Market, Los Angeles"),
                activity("Coffee after", "food", " grand central market, los angeles "),
                activity("Sleep", "accommodation", "Hotel Figueroa, Los Angeles"),
            )
        )

        assertEquals(
            listOf("Grand Central Market, Los Angeles", "Hotel Figueroa, Los Angeles"),
            stops,
        )
    }

    @Test
    fun `a place you come back to later keeps both visits`() {
        // Two visits are two points on the line; the server draws one pin for them.
        val stops = DayMapClient.stops(
            listOf(
                activity("Check in", "accommodation", "Hotel Figueroa, Los Angeles"),
                activity("Museum", "sightseeing", "The Broad, Los Angeles"),
                activity("Sleep", "accommodation", "Hotel Figueroa, Los Angeles"),
            )
        )

        assertEquals(3, stops.size)
    }

    @Test
    fun `activities with no location are skipped`() {
        val stops = DayMapClient.stops(
            listOf(
                activity("Rest", "rest", null),
                activity("Blank", "food", "   "),
                activity("Museum", "sightseeing", "The Broad, Los Angeles"),
            )
        )

        assertEquals(listOf("The Broad, Los Angeles"), stops)
    }
}
