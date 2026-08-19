package com.wandergent.app.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Wire types for the community feed, mirroring `app/community/store.py`.
 *
 * The feed and the detail view are separate shapes on purpose: a card carries only what
 * it draws, so listing thirty trips does not pull thirty itineraries over a phone
 * connection. [SharedPlanDetail] is the same card plus the plan itself.
 */

/**
 * No author field: the server takes the byline from the bearer token. Sending one would
 * now be rejected as an unknown field rather than quietly believed, which is the point.
 */
@Serializable
data class PublishRequest(
    val request: String = "",
    val note: String = "",
    val itinerary: Itinerary,
)

/**
 * One page of the feed.
 *
 * An envelope rather than a bare list because a page has to say where it *ends*. Without
 * that the client only knows how many it skipped, which is offset paging -- and the feed
 * grows at the top, so one post published while somebody reads shifts every later page by
 * one and shows them a duplicate.
 */
@Serializable
data class FeedPage(
    val items: List<SharedPlanCard> = emptyList(),
    /** Pass back as `cursor` for the next page. Null means this was the last one. */
    @SerialName("next_cursor") val nextCursor: String? = null,
)

@Serializable
data class SharedPlanCard(
    val id: String,
    @SerialName("author_id") val authorId: String,
    @SerialName("author_name") val authorName: String,
    val note: String = "",
    val request: String = "",
    val destination: String,
    @SerialName("start_date") val startDate: String,
    @SerialName("end_date") val endDate: String,
    @SerialName("day_count") val dayCount: Int,
    @SerialName("total_cost") val totalCost: Double,
    val currency: String,
    @SerialName("created_at") val createdAt: String,
    @SerialName("save_count") val saveCount: Int = 0,
    /**
     * Whether *this* reader already saved it.
     *
     * Genuinely tri-state, so it must stay nullable: null means the request carried no
     * viewer, which is not the same claim as "you have not saved this" and must not
     * draw an empty heart.
     */
    @SerialName("saved_by_viewer") val savedByViewer: Boolean? = null,
)

@Serializable
data class SharedPlanDetail(
    val id: String,
    @SerialName("author_id") val authorId: String,
    @SerialName("author_name") val authorName: String,
    val note: String = "",
    val request: String = "",
    val destination: String,
    @SerialName("start_date") val startDate: String,
    @SerialName("end_date") val endDate: String,
    @SerialName("day_count") val dayCount: Int,
    @SerialName("total_cost") val totalCost: Double,
    val currency: String,
    @SerialName("created_at") val createdAt: String,
    @SerialName("save_count") val saveCount: Int = 0,
    @SerialName("saved_by_viewer") val savedByViewer: Boolean? = null,
    val itinerary: Itinerary,
)

/** No user id: whose save this is comes from the token. */
@Serializable
data class SaveRequest(
    /** False unsaves. One call for both directions, so a toggle is one request. */
    val saved: Boolean = true,
)

@Serializable
data class SaveResponse(
    val saved: Boolean,
    @SerialName("save_count") val saveCount: Int,
)
