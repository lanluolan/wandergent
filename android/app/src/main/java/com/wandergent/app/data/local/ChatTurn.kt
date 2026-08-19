package com.wandergent.app.data.local

import androidx.room.Dao
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query

/**
 * One completed round of the planning conversation, kept across restarts.
 *
 * **Only finished rounds are stored.** A run that was still streaming when the process
 * died is not resumable -- there is no server-side session to reattach to -- so
 * persisting it would restore a spinner that never stops. Failures are not stored
 * either: a restored error is noise, and the request that caused it is still visible
 * above it if the user wants to try again.
 *
 * This exists because of what revision made it cost. Losing the transcript used to mean
 * losing chat history; since a follow-up edits the plan above it, losing the transcript
 * means losing **the plan you were about to change**.
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
         * How many rounds to keep and restore per account.
         *
         * Bounded because each row is a full itinerary: unbounded, a heavy user restores
         * a hundred plans into a lazy list on launch and decodes them to draw the first.
         * Ten is far more history than anyone scrolls back through while editing.
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
