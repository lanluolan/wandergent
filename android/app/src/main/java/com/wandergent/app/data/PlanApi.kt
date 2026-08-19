package com.wandergent.app.data

import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST

interface PlanApi {
    /**
     * Plan a trip from a free-text request.
     *
     * Slow by API standards -- 2 to 4 LLM calls plus tool calls -- so the client's read
     * timeout has to be generous. See [Network].
     */
    @POST("plan")
    suspend fun plan(@Body request: PlanRequest): PlanResponse

    /** What the backend can supply for callers who bring only part of an LLM account. */
    @GET("health")
    suspend fun health(): HealthDto
}
