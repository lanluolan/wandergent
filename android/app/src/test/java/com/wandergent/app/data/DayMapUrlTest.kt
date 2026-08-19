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
        ServerConfig.reset()
        val url = DayMapClient.interactiveUrl(day)!!

        assertTrue(url.startsWith(ServerConfig.default + "day-map/interactive?"), url)
        assertEquals(4, Regex("place=").findAll(url).count())
        // The Maps key lives on the server; nothing key-shaped is assembled here.
        assertTrue(!url.contains("key="), url)
    }

    @Test
    fun `a changed backend address is followed`() {
        ServerConfig.set("http://192.168.1.10:8000")
        try {
            assertTrue(DayMapClient.interactiveUrl(day)!!.startsWith("http://192.168.1.10:8000/"))
        } finally {
            ServerConfig.reset()
        }
    }
}
