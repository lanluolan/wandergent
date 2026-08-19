package com.wandergent.app.data.local

import com.wandergent.app.data.ApiJson
import com.wandergent.app.data.PlanResponse

/** One restored round: the request and the plan it produced. */
data class StoredTurn(
    val id: Long,
    val request: String,
    val response: PlanResponse,
    val revision: Boolean,
)

/**
 * The planning conversation, across restarts.
 *
 * Separate from [SavedPlanRepository] because the two mean different things: saving is
 * a deliberate "keep this trip", while this is the working state of the current
 * conversation. Conflating them would either fill the library with drafts or lose the
 * draft you were editing.
 */
class ChatTurnRepository(private val dao: ChatTurnDao) {

    /**
     * Restore the conversation, oldest first.
     *
     * Rows that no longer decode are dropped rather than thrown: a plan stored before a
     * breaking model change must not stop the app from opening.
     */
    suspend fun restore(userId: Long): List<StoredTurn> =
        dao.forUser(userId).mapNotNull { row ->
            runCatching { ApiJson.json.decodeFromString<PlanResponse>(row.planJson) }
                .getOrNull()
                ?.let { StoredTurn(row.id, row.request, it, row.revision) }
        }

    /** Remember a finished round. Returns its row id, which becomes the turn's identity. */
    suspend fun append(
        userId: Long,
        request: String,
        response: PlanResponse,
        revision: Boolean,
    ): Long {
        val id = dao.insert(
            ChatTurnEntity(
                userId = userId,
                request = request,
                planJson = ApiJson.json.encodeToString(response),
                revision = revision,
                createdAt = System.currentTimeMillis(),
            )
        )
        dao.trim(userId, ChatTurnEntity.KEEP)
        return id
    }

    suspend fun clear(userId: Long) = dao.clear(userId)
}
