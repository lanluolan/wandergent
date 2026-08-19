package com.wandergent.app.data

import com.wandergent.app.BuildConfig
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/**
 * Where the backend lives, at runtime.
 *
 * The build-time `BASE_URL` assumes `adb reverse tcp:8000 tcp:8000`, which is right for
 * a USB-tethered phone and wrong for everything else -- an emulator wants `10.0.2.2`, a
 * phone on the same Wi-Fi wants the host's LAN address, and a deployed backend wants its
 * own host. Without this, each of those needs a rebuild.
 *
 * Held as a plain volatile rather than injected: three call sites read it, and the
 * process-wide singleton is the same shape [Network] already uses.
 */
object ServerConfig {

    /** What the APK was built against, and what "Reset" goes back to. */
    val default: String = BuildConfig.BASE_URL

    @Volatile
    var baseUrl: String = default
        private set

    /** Applies a user-entered address. Ignores anything [parse] cannot make sense of. */
    fun set(raw: String) {
        parse(raw)?.let { baseUrl = it.toString() }
    }

    fun reset() {
        baseUrl = default
    }

    /**
     * What a bare address means when nobody typed a scheme.
     *
     * A debug build talks to a dev machine on a local network, where `192.168.1.10:8000`
     * read off `ipconfig` means http and https would be the surprising guess. A release
     * build cannot make that assumption: `res/xml/network_security_config.xml` denies
     * cleartext outright there, so prepending `http` would build an address the platform
     * refuses to dial and hand back an indistinguishable network failure.
     */
    private val assumedScheme: String = if (BuildConfig.DEBUG) "http" else "https"

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

    fun isValid(raw: String): Boolean = parse(raw) != null
}
