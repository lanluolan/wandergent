package com.wandergent.app.data

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.Request

/**
 * Fetches a day's map from **our** backend, not from Google.
 *
 * That is the whole design: the Maps key never leaves the server, nothing map-related
 * ships in the APK, and the app needs no Google Play services -- which the test device
 * does not have, so the Maps SDK could not run on it at all.
 *
 * No image-loading library. One picture per day card does not justify a dependency when
 * OkHttp is already here and the result is thirty lines; see the "no over-engineering"
 * rule in CLAUDE.md.
 */
object DayMapClient {

    /** Beyond this the backend drops the extras rather than mislabel them. */
    private const val MAX_STOPS = 19

    /**
     * The same stops as a page a WebView can pan and zoom.
     *
     * Built here rather than in the UI so both renderings of a day agree on the stop
     * limit and on which backend they ask.
     */
    fun interactiveUrl(places: List<String>): String? {
        val stops = places.filter { it.isNotBlank() }.take(MAX_STOPS)
        if (stops.isEmpty()) return null
        return ServerConfig.baseUrl.toHttpUrl().newBuilder()
            .addPathSegment("day-map")
            .addPathSegment("interactive")
            .apply { stops.forEach { addQueryParameter("place", it) } }
            .build()
            .toString()
    }

    /**
     * The day as a Google Maps route, for handing off to whatever browser or map app
     * the phone has.
     *
     * This is the path that works on hardware where the embedded map does not: it needs
     * no Play services, no key, and no modern WebView -- google.com/maps renders on the
     * device's own browser, which is newer than its system WebView. It is also the only
     * one of the three that offers turn-by-turn navigation.
     *
     * Uses the documented Maps URLs form, so no key is involved and nothing is billed.
     */
    fun externalRouteUrl(places: List<String>): String? {
        val stops = places.filter { it.isNotBlank() }.take(MAX_STOPS)
        if (stops.size < 2) return null
        return "https://www.google.com/maps/dir/".toHttpUrl().newBuilder()
            .addQueryParameter("api", "1")
            .addQueryParameter("origin", stops.first())
            .addQueryParameter("destination", stops.last())
            .apply {
                val between = stops.drop(1).dropLast(1)
                if (between.isNotEmpty()) {
                    // Maps URLs wants waypoints pipe-separated in one parameter.
                    addQueryParameter("waypoints", between.joinToString("|"))
                }
            }
            .addQueryParameter("travelmode", "walking")
            .build()
            .toString()
    }

    /**
     * Returns the rendered map, or null if it could not be drawn.
     *
     * Null rather than an exception on purpose: the map is a bonus on top of a plan
     * that is already complete and useful, so a failure hides the picture and leaves
     * the itinerary alone.
     */
    suspend fun load(places: List<String>, widthPx: Int, heightPx: Int): Bitmap? {
        val stops = places.filter { it.isNotBlank() }.take(MAX_STOPS)
        if (stops.isEmpty()) return null

        val url = ServerConfig.baseUrl.toHttpUrl().newBuilder()
            .addPathSegment("day-map")
            .apply { stops.forEach { addQueryParameter("place", it) } }
            .addQueryParameter("width", widthPx.toString())
            .addQueryParameter("height", heightPx.toString())
            .build()

        return withContext(Dispatchers.IO) {
            runCatching {
                Network.httpClient.newCall(Request.Builder().url(url).build()).execute().use { response ->
                    if (!response.isSuccessful) return@use null
                    response.body?.byteStream()?.let(BitmapFactory::decodeStream)
                }
            }.getOrNull()
        }
    }
}
