package com.wandergent.app.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import retrofit2.http.Body
import retrofit2.http.POST

@Serializable
data class FeedbackRequest(
    @SerialName("run_id") val runId: String,
    val helpful: Boolean,
    val category: String = "other",
    @SerialName("day_index") val dayIndex: Int? = null,
    @SerialName("activity_index") val activityIndex: Int? = null,
)

interface FeedbackApi {
    @POST("feedback")
    suspend fun submit(@Body request: FeedbackRequest)
}
