package com.wandergent.app.data

import retrofit2.http.Body
import retrofit2.http.POST

interface PlanApi {
    /** Slow -- 2 to 4 LLM calls plus tools -- so [Network] allows a generous read timeout. */
    @POST("plan")
    suspend fun plan(@Body request: PlanRequest): PlanResponse
}
