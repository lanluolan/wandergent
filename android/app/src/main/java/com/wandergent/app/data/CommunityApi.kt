package com.wandergent.app.data

import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query

/**
 * The community feed.
 *
 * Ordinary CRUD, and fast by this app's standards -- none of these touch the model, so
 * they finish in milliseconds rather than the minutes a planning run takes.
 */
interface CommunityApi {

    @POST("community/plans")
    suspend fun publish(@Body request: PublishRequest): SharedPlanDetail

    /**
     * A token fills in [SharedPlanCard.savedByViewer]; without one it stays null.
     *
     * `cursor` comes from the previous page's [FeedPage.nextCursor]. `destination` is a
     * case-insensitive substring match, and empty means no filter.
     */
    @GET("community/plans")
    suspend fun feed(
        @Query("limit") limit: Int = 30,
        @Query("cursor") cursor: String? = null,
        @Query("destination") destination: String = "",
    ): FeedPage

    @GET("community/plans/{id}")
    suspend fun plan(@Path("id") id: String): SharedPlanDetail

    @POST("community/plans/{id}/save")
    suspend fun setSaved(@Path("id") id: String, @Body request: SaveRequest): SaveResponse

    /** Withdraw your own post. 404 covers both "gone" and "not yours". */
    @DELETE("community/plans/{id}")
    suspend fun withdraw(@Path("id") id: String)
}
