package com.wandergent.app.data

import com.wandergent.app.BuildConfig
import java.util.concurrent.TimeUnit
import kotlinx.serialization.json.Json
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

    /** `ignoreUnknownKeys`: the backend gains fields, and an older client must not crash. */
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
 * A plain object rather than Hilt: everything here is built once, at process start, and
 * nothing needs a scope. See docs/decisions.md.
 */
object Network {

    /**
     * The bearer token for the signed-in account, or null.
     *
     * Mutable state rather than a constructor argument: OkHttp builds its interceptor
     * chain once, and every request has to see the current value.
     */
    @Volatile
    var authToken: String? = null

    /**
     * Called when the server rejects a token we attached. A lambda, so this layer never
     * has to know about DataStore or Room.
     */
    @Volatile
    var onUnauthorized: (() -> Unit)? = null

    /**
     * Attaches the session to every request that does not already carry one.
     *
     * Unconditional rather than per-endpoint: forgetting one would silently send it as a
     * stranger. Endpoints that do not need it ignore it.
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
        // Never for `/auth/*`: a 401 there means "wrong password", not "session died",
        // and signing people out for a typo would be the worse failure.
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
        .addInterceptor(authenticate)
        .apply {
            if (BuildConfig.DEBUG) {
                addInterceptor(
                    // BASIC on purpose, not just for brevity: HEADERS would print the
                    // session bearer token, and a debug log is the easiest place in the
                    // system to leak a credential from.
                    HttpLoggingInterceptor().apply { level = HttpLoggingInterceptor.Level.BASIC }
                )
            }
        }
        .build()

    val planApi: PlanApi = Retrofit.Builder()
        .baseUrl(ServerConfig.baseUrl)
        .client(httpClient)
        .addConverterFactory(ApiJson.json.asConverterFactory("application/json".toMediaType()))
        .build()
        .create(PlanApi::class.java)

    /**
     * Derived from [httpClient] -- same interceptors and connection pool, shorter read
     * timeout. Nothing here or on [communityApi] touches the model, so 120s would mean a
     * two-minute spinner when the server is simply down.
     */
    val authApi: AuthApi = Retrofit.Builder()
        .baseUrl(ServerConfig.baseUrl)
        .client(httpClient.newBuilder().readTimeout(20, TimeUnit.SECONDS).build())
        .addConverterFactory(ApiJson.json.asConverterFactory("application/json".toMediaType()))
        .build()
        .create(AuthApi::class.java)

    val communityApi: CommunityApi = Retrofit.Builder()
        .baseUrl(ServerConfig.baseUrl)
        .client(httpClient.newBuilder().readTimeout(20, TimeUnit.SECONDS).build())
        .addConverterFactory(ApiJson.json.asConverterFactory("application/json".toMediaType()))
        .build()
        .create(CommunityApi::class.java)

    val preferenceApi: PreferenceApi = Retrofit.Builder()
        .baseUrl(ServerConfig.baseUrl)
        .client(httpClient.newBuilder().callTimeout(20, TimeUnit.SECONDS).build())
        .addConverterFactory(ApiJson.json.asConverterFactory("application/json".toMediaType()))
        .build()
        .create(PreferenceApi::class.java)

    val feedbackApi: FeedbackApi = Retrofit.Builder()
        .baseUrl(ServerConfig.baseUrl)
        .client(httpClient.newBuilder().callTimeout(20, TimeUnit.SECONDS).build())
        .addConverterFactory(ApiJson.json.asConverterFactory("application/json".toMediaType()))
        .build()
        .create(FeedbackApi::class.java)
}
