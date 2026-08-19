package com.wandergent.app.data

import com.wandergent.app.BuildConfig
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/**
 * Where the backend lives.
 *
 * Fixed when the APK is built, from the `wandergent.serverUrl` Gradle property. It used
 * to be editable from the profile screen, which meant a travel app shipped a "Backend
 * address" row to every user so a developer could switch machines without rebuilding --
 * and, worse, the row lived behind sign-in, so a wrong address locked you out of the one
 * screen that could fix it. See docs/decisions.md.
 *
 * Held as a plain object rather than injected: several call sites read it, and the
 * process-wide singleton is the same shape [Network] already uses.
 */
object ServerConfig {

    /**
     * What a bare address means when the build property carried no scheme.
     *
     * A debug build talks to a dev machine on a local network, where `192.168.1.10:8000`
     * read off `ipconfig` means http and https would be the surprising guess. A release
     * build cannot make that assumption: `res/xml/network_security_config.xml` denies
     * cleartext outright there, so prepending `http` would build an address the platform
     * refuses to dial and hand back an indistinguishable network failure.
     */
    private val assumedScheme: String = if (BuildConfig.DEBUG) "http" else "https"

    /**
     * The address every request goes to.
     *
     * Fails at class load rather than at the first request, so a typo in the build flag
     * surfaces the moment the app starts instead of as an unexplained network error.
     */
    val baseUrl: String = parse(BuildConfig.BASE_URL)?.toString()
        ?: error("BASE_URL is not a usable address: ${BuildConfig.BASE_URL}")

    /**
     * Read an address the way a person would type it.
     *
     * [scheme] exists so both branches of [assumedScheme] are reachable from a test; call
     * sites pass nothing and get the one that matches the build.
     */
    fun parse(raw: String, scheme: String = assumedScheme): HttpUrl? {
        val trimmed = raw.trim()
        if (trimmed.isEmpty()) return null
        val withScheme = if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) {
            trimmed
        } else {
            "$scheme://$trimmed"
        }
        return withScheme.trimEnd('/').plus("/").toHttpUrlOrNull()
    }
}
