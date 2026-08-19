package com.wandergent.app.data

import okhttp3.HttpUrl

/**
 * An LLM account the traveller brings themselves.
 *
 * The backend normally uses the key in its own `.env`. Someone who is not the person
 * running that backend can supply their own here instead, and their provider bills them
 * for the tokens. The key is sent per request as a header and never stored server-side.
 *
 * [model] and [baseUrl] are optional; blank means "whatever the backend is configured
 * with". [baseUrl] must additionally be one the backend allowlists -- it is the backend
 * that would make the request, so the set of reachable hosts is closed there, not here.
 *
 * `toString` is written by hand. The generated one would print the key, and data classes
 * end up in logs and crash reports without anyone deciding that they should.
 */
data class LlmCredentials(
    val apiKey: String,
    val baseUrl: String = "",
    val model: String = "",
) {
    val isSet: Boolean get() = apiKey.isNotBlank()

    /** The last four characters, which is the most any UI should show. */
    val hint: String get() = apiKey.takeLast(4)

    override fun toString(): String =
        "LlmCredentials(apiKey=***$hint, baseUrl='$baseUrl', model='$model')"

    /**
     * The headers to attach for a request to [url], which is usually none.
     *
     * Every rule about where this key may go lives here rather than in the interceptor,
     * so all four are visible together and testable without a server:
     *
     * - nothing to send if the traveller set no key;
     * - planning endpoints only, because `/auth` and `/community` have no use for it and
     *   a secret should reach exactly the handlers that need it;
     * - never over cleartext to a real host (see [safeToSend]);
     * - model and endpoint only when they were actually filled in, since blank means
     *   "whatever the backend is configured with" and the backend refuses those two
     *   without a key anyway.
     */
    fun headersFor(url: HttpUrl): Map<String, String> {
        if (!isSet || !url.encodedPath.startsWith("/plan") || !safeToSend(url)) return emptyMap()
        return buildMap {
            put("X-LLM-Api-Key", apiKey)
            if (baseUrl.isNotBlank()) put("X-LLM-Base-Url", baseUrl)
            if (model.isNotBlank()) put("X-LLM-Model", model)
        }
    }

    companion object {
        val NONE = LlmCredentials(apiKey = "")

        /**
         * Whether a key may be sent to this address.
         *
         * A release build cannot reach a cleartext host at all -- `network_security_config`
         * denies it -- but a debug build can, and pointing one at a LAN address over http
         * would put the traveller's API key on the wire in the clear for anyone on that
         * Wi-Fi. Loopback is exempt because the debug default is an `adb reverse` tunnel
         * over USB, which never touches a network.
         */
        fun safeToSend(url: HttpUrl): Boolean =
            url.isHttps || url.host == "127.0.0.1" || url.host == "localhost" || url.host == "::1"
    }
}
