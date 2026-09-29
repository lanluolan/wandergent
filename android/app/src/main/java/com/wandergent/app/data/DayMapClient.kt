package com.wandergent.app.data

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.Request
import java.util.concurrent.TimeUnit

/**
 * Fetches a day's map from **our** backend, not from Google.
 *
 * That is the whole design: the Maps key never leaves the server, nothing map-related
 * ships in the APK, and the app needs no Play services -- which the test device lacks.
 *
 * No image-loading library: one picture per day card does not justify a dependency when
 * OkHttp is already here.
 */
object DayMapClient {

    private val imageClient by lazy {
        Network.httpClient.newBuilder().callTimeout(20, TimeUnit.SECONDS).build()
    }

    /** Beyond this the backend drops the extras rather than mislabel them. */
    private const val MAX_STOPS = 19

    /**
     * The places on a day worth putting a pin in, in visiting order.
     *
     * `transport` rows are dropped: their location is a leg ("W 30th St to W 53rd St"),
     * which Google cannot resolve and which burns marker labels. A place repeated back to
     * back is one place -- the second marker would land under the first.
     */
    fun stops(activities: List<Activity>): List<String> {
        val places = activities
            .filter { it.category != "transport" }
            .mapNotNull { it.location?.trim()?.takeIf(String::isNotEmpty) }
        return places
            .filterIndexed { index, place -> index == 0 || !place.equals(places[index - 1], ignoreCase = true) }
            .take(MAX_STOPS)
    }

    /**
     * The same stops as a page a WebView can pan and zoom. Built here so both renderings
     * of a day agree on the stop limit and on which backend they ask.
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
     * The day as a Google Maps route, handed off to the phone's browser or map app.
     *
     * The path that survives old hardware: no Play services, no key, and no modern
     * WebView -- and the only one of the three with turn-by-turn navigation. Uses the
     * documented Maps URLs form, so nothing is billed.
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
     * The rendered map, or null if it could not be drawn. Null rather than an exception:
     * the map is a bonus on top of a plan that is already complete, so a failure just
     * hides the picture.
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
                imageClient.newCall(Request.Builder().url(url).build()).execute().use { response ->
                    if (!response.isSuccessful) return@use null
                    BitmapFactory.decodeStream(response.body.byteStream())
                }
            }.getOrNull()
        }
    }
}
