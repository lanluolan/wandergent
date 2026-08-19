package com.wandergent.app.data

import com.wandergent.app.BuildConfig
import java.util.concurrent.TimeUnit
import kotlinx.serialization.json.Json
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory

/**
 * JSON setup, kept separate from the HTTP stack so it can be exercised in plain JVM
 * unit tests without constructing OkHttp and Retrofit.
 */
object ApiJson {

    /**
     * `ignoreUnknownKeys` matters: the backend gains fields as phases land (Phase 2 adds
     * streaming metadata), and an older client must not crash on a newer server.
     */
    val json = Json {
        ignoreUnknownKeys = true
        explicitNulls = false
    }

    /** Parse FastAPI's `{"detail": "..."}` body; returns null when it is not that shape. */
    fun parseErrorDetail(body: String?): String? {
        if (body.isNullOrBlank()) return null
        return runCatching { json.decodeFromString<ApiError>(body).detail }.getOrNull()
    }
}

/**
 * Single Retrofit instance for the app.
 *
 * Deliberately a plain object rather than Hilt: Phase 1 has one screen and one
 * ViewModel, so a DI container would be scaffolding around a single construction site.
 * Hilt lands in Phase 2 when there is a second screen -- see docs/decisions.md.
 */
object Network {

    /**
     * Points every request at whatever [ServerConfig] currently holds.
     *
     * Retrofit fixes its base URL at construction, so redirecting per request is the
     * way to make the address a setting without rebuilding Retrofit on every change.
     * Only scheme/host/port are replaced -- a path prefix on the configured address is
     * not honoured, which is fine for "which machine is the dev server on".
     */
    private val retarget = Interceptor { chain ->
        val target = ServerConfig.baseUrl.toHttpUrlOrNull()
            ?: return@Interceptor chain.proceed(chain.request())
        val request = chain.request()
        chain.proceed(
            request.newBuilder()
                .url(
                    request.url.newBuilder()
                        .scheme(target.scheme)
                        .host(target.host)
                        .port(target.port)
                        .build()
                )
                .build()
        )
    }

    /**
     * The bearer token for the signed-in account, or null.
     *
     * Held here as plain state rather than injected, for the same reason [ServerConfig]
     * is: OkHttp builds its interceptor chain once, and every request has to see the
     * *current* value. Set by the auth layer on sign-in and cleared on sign-out.
     */
    @Volatile
    var authToken: String? = null

    /**
     * Called when the server rejects a token we attached.
     *
     * A lambda rather than a dependency on the session store, so the network layer stays
     * ignorant of DataStore and Room. Set by the auth layer at startup.
     */
    @Volatile
    var onUnauthorized: (() -> Unit)? = null

    /**
     * Attaches the session to every request that does not already carry one.
     *
     * Unconditional rather than per-endpoint: the alternative is remembering to add it,
     * and the failure mode of forgetting is a request that silently acts as a stranger.
     * Endpoints that do not need it simply ignore it.
     */
    private val authenticate = Interceptor { chain ->
        val token = authToken
        val original = chain.request()
        val attached = !token.isNullOrEmpty() && original.header("Authorization") == null
        val request = if (attached) {
            original.newBuilder().header("Authorization", "Bearer $token").build()
        } else {
            original
        }

        val response = chain.proceed(request)
        // Only when *we* supplied the token, and never for the auth endpoints themselves:
        // a 401 from `/auth/login` means "wrong password", not "your session died", and
        // treating it as the latter would sign people out for a typo.
        if (response.code == 401 && attached && !request.url.encodedPath.startsWith("/auth/")) {
            authToken = null
            onUnauthorized?.invoke()
        }
        response
    }

    /** Shared with [DayMapClient], so timeouts and logging are configured in one place. */
    val httpClient: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        // A planning run makes several LLM calls; OkHttp's 10s default cuts it off
        // mid-flight and surfaces as an indistinguishable network failure.
        .readTimeout(120, TimeUnit.SECONDS)
        .writeTimeout(15, TimeUnit.SECONDS)
        .addInterceptor(retarget)
        .addInterceptor(authenticate)
        .apply {
            if (BuildConfig.DEBUG) {
                addInterceptor(
                    HttpLoggingInterceptor().apply { level = HttpLoggingInterceptor.Level.BASIC }
                )
            }
        }
        .build()

    val planApi: PlanApi = Retrofit.Builder()
        // A placeholder: `retarget` above rewrites the host of every request before it
        // leaves. Retrofit only needs a syntactically valid base to resolve paths against.
        .baseUrl(ServerConfig.default)
        .client(httpClient)
        .addConverterFactory(ApiJson.json.asConverterFactory("application/json".toMediaType()))
        .build()
        .create(PlanApi::class.java)

    /**
     * Its own client rather than sharing the planner's.
     *
     * [httpClient]'s 120-second read timeout is sized for a run that makes several LLM
     * calls. Nothing on the community feed touches the model, so inheriting that would
     * leave someone watching a spinner for two minutes when the server is simply down.
     * The builder is derived from [httpClient], so the retarget interceptor, the
     * connection pool and debug logging are all still shared.
     */
    val authApi: AuthApi = Retrofit.Builder()
        .baseUrl(ServerConfig.default)
        .client(httpClient.newBuilder().readTimeout(20, TimeUnit.SECONDS).build())
        .addConverterFactory(ApiJson.json.asConverterFactory("application/json".toMediaType()))
        .build()
        .create(AuthApi::class.java)

    val communityApi: CommunityApi = Retrofit.Builder()
        .baseUrl(ServerConfig.default)
        .client(httpClient.newBuilder().readTimeout(20, TimeUnit.SECONDS).build())
        .addConverterFactory(ApiJson.json.asConverterFactory("application/json".toMediaType()))
        .build()
        .create(CommunityApi::class.java)
}
