package com.wandergent.app.data.local

import androidx.room.Dao
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query

/**
 * One completed round of the planning conversation, kept across restarts. Since a
 * follow-up edits the plan above it, losing the transcript loses **the plan you were
 * about to change**, not just the history.
 *
 * **Only finished rounds are stored.** A stream cannot be reattached to, so persisting one
 * would restore a spinner that never stops; a restored error would be noise.
 */
@Entity(tableName = "chat_turns")
data class ChatTurnEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    /** Whose conversation. [SavedPlanEntity.LEGACY_USER] for anonymous runs. */
    val userId: Long,
    val request: String,
    /** The whole `PlanResponse`, as it arrived. */
    val planJson: String,
    /** Whether this round edited the plan above it, so the label survives a restart. */
    val revision: Boolean,
    val createdAt: Long,
) {
    companion object {
        /**
         * How many rounds to keep and restore per account. Bounded because each row is a
         * full itinerary, and ten is more than anyone scrolls back through while editing.
         */
        const val KEEP = 10
    }
}

@Dao
interface ChatTurnDao {

    @Query("SELECT * FROM chat_turns WHERE userId = :userId ORDER BY id ASC")
    suspend fun forUser(userId: Long): List<ChatTurnEntity>

    @Insert
    suspend fun insert(turn: ChatTurnEntity): Long

    /** Drop everything but the newest [keep] rounds for this user. */
    @Query(
        """
        DELETE FROM chat_turns WHERE userId = :userId AND id NOT IN (
            SELECT id FROM chat_turns WHERE userId = :userId ORDER BY id DESC LIMIT :keep
        )
        """
    )
    suspend fun trim(userId: Long, keep: Int)

    @Query("DELETE FROM chat_turns WHERE userId = :userId")
    suspend fun clear(userId: Long)
}
