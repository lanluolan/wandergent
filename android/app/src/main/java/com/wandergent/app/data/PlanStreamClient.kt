package com.wandergent.app.data

import java.util.concurrent.TimeUnit
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources

/**
 * Server-Sent Events client for `POST /plan/stream`.
 *
 * SSE is normally a GET protocol, but the request body here is user text, so this is a
 * POST with `Accept: text/event-stream`. OkHttp's EventSource does not care about the
 * method, only the response content type.
 */
class PlanStreamClient(private val baseUrl: () -> String = { ServerConfig.baseUrl }) {

    /** Read per request, not cached: the address is a setting the user can change. */
    private fun url() = baseUrl().trimEnd('/') + "/plan/stream"

    // Derived from the shared client rather than built fresh, so it inherits the
    // interceptors -- in particular the one that attaches the session. Built standalone
    // it silently streamed every plan as an anonymous caller even when signed in, which
    // looks like working software and quietly loses the traveller's preferences.
    private val client = Network.httpClient.newBuilder()
        .connectTimeout(15, TimeUnit.SECONDS)
        // The timeout applies between events, not to the whole stream. Generous enough
        // to cover the gap while the model composes, short enough that a dead
        // connection still fails instead of hanging forever.
        .readTimeout(180, TimeUnit.SECONDS)
        .writeTimeout(15, TimeUnit.SECONDS)
        .build()

    private val factory = EventSources.createFactory(client)

    /**
     * Emits every event until the stream ends.
     *
     * Cancelling the collector cancels the HTTP request, so navigating away stops the
     * work rather than leaving a socket open.
     */
    fun stream(
        message: String,
        currency: String = "",
        previous: Itinerary? = null,
    ): Flow<PlanEventDto> = callbackFlow {
        val body = ApiJson.json.encodeToString(PlanRequest(message, currency, previous))
            .toRequestBody("application/json".toMediaType())

        val request = Request.Builder()
            .url(url())
            .post(body)
            .header("Accept", "text/event-stream")
            .build()

        val listener = object : EventSourceListener() {
            override fun onEvent(
                eventSource: EventSource,
                id: String?,
                type: String?,
                data: String,
            ) {
                val event = runCatching {
                    ApiJson.json.decodeFromString<PlanEventDto>(data)
                }.getOrNull() ?: return
                trySend(event)
            }

            override fun onClosed(eventSource: EventSource) {
                close()
            }

            override fun onFailure(
                eventSource: EventSource,
                t: Throwable?,
                response: Response?,
            ) {
                // A non-2xx never becomes a stream, so its body is the error envelope.
                val detail = response?.body?.let { runCatching { it.string() }.getOrNull() }
                    ?.let { ApiJson.parseErrorDetail(it) }
                close(StreamFailure(status = response?.code, detail = detail, cause = t))
            }
        }

        val source = factory.newEventSource(request, listener)
        awaitClose { source.cancel() }
    }
}

/** Transport-level failure, carrying the status code so the UI can decide on a retry. */
class StreamFailure(
    val status: Int?,
    val detail: String?,
    cause: Throwable?,
) : Exception(detail ?: cause?.message ?: "stream failed", cause)
