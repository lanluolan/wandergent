package com.wandergent.app.data

import com.wandergent.app.BuildConfig
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/**
 * Where the backend lives.
 *
 * Fixed at build time from the `wandergent.serverUrl` Gradle property, not a setting: an
 * editable address shipped a dev affordance to every user, and it sat behind the sign-in
 * that a wrong address blocked. See docs/decisions.md.
 */
object ServerConfig {

    /**
     * What a bare address means when the build property carried no scheme.
     *
     * http on debug, where the target is a dev machine on the LAN. https on release,
     * where `res/xml/network_security_config.xml` denies cleartext and an http address
     * would fail as an indistinguishable network error.
     */
    private val assumedScheme: String = if (BuildConfig.DEBUG) "http" else "https"

    /** Fails at class load, so a typo in the build flag surfaces at launch. */
    val baseUrl: String = parse(BuildConfig.BASE_URL)?.toString()
        ?: error("BASE_URL is not a usable address: ${BuildConfig.BASE_URL}")

    /** Read an address as typed. [scheme] is overridable only so tests reach both branches. */
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
