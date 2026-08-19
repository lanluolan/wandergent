package com.wandergent.app.data.local

import com.wandergent.app.data.ApiJson
import com.wandergent.app.data.PlanResponse
import kotlinx.coroutines.flow.Flow

/** Local library of saved itineraries, one shelf per account, all on the device. */
class SavedPlanRepository(private val dao: SavedPlanDao) {

    fun observeFor(userId: Long): Flow<List<SavedPlanEntity>> = dao.observeFor(userId)

    /**
     * Hand pre-account rows to this user. Call once when a library is first opened.
     *
     * Returns how many were adopted, so the screen can say what happened rather than
     * silently growing by five entries.
     */
    suspend fun adoptLegacy(userId: Long): Int =
        if (userId == SavedPlanEntity.LEGACY_USER) 0 else dao.adoptLegacy(userId)

    /**
     * Persist a generated plan. Returns false when there is no itinerary to save --
     * a 200 with a null itinerary is a real case and must not be stored as an empty
     * entry the user cannot open.
     */
    suspend fun save(
        userId: Long,
        request: String,
        response: PlanResponse,
        sharedPlanId: String? = null,
    ): Boolean {
        val itinerary = response.itinerary ?: return false
        // Copying the same community post twice is a no-op, not a second row. The
        // server's save is keyed on (plan, user) and is already idempotent; without this
        // the library was not, so save -> unsave -> save left two identical trips.
        // Matching on the post's identity rather than on contents keeps the legitimate
        // case working: delete your copy, save it again, get it back.
        if (sharedPlanId != null && dao.findShared(userId, sharedPlanId) != null) return true
        dao.insert(
            SavedPlanEntity(
                userId = userId,
                destination = itinerary.destination,
                startDate = itinerary.startDate,
                endDate = itinerary.endDate,
                dayCount = itinerary.days.size,
                totalCost = itinerary.totalEstimatedCost,
                currency = itinerary.currency,
                request = request,
                planJson = ApiJson.json.encodeToString(response),
                createdAt = System.currentTimeMillis(),
                sharedPlanId = sharedPlanId,
            )
        )
        return true
    }

    suspend fun delete(plan: SavedPlanEntity) = dao.delete(plan)

    /**
     * Put a deleted plan back, id and all.
     *
     * Room binds a non-zero primary key as given even with `autoGenerate`, so an undo
     * restores the same row rather than a copy -- which matters because the list is
     * keyed on the id.
     */
    suspend fun restore(plan: SavedPlanEntity) {
        dao.insert(plan)
    }

    /**
     * Rehydrate a stored plan. Returns null rather than throwing if the JSON predates a
     * breaking model change, so one unreadable row cannot take down the whole list.
     */
    fun decode(plan: SavedPlanEntity): PlanResponse? =
        runCatching { ApiJson.json.decodeFromString<PlanResponse>(plan.planJson) }.getOrNull()
}
