package com.wandergent.app.data.local

import androidx.room.Dao
import androidx.room.Delete
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

/**
 * A saved itinerary.
 *
 * The plan itself is stored as the raw response JSON in one column rather than being
 * shredded into activity/day tables. An itinerary is a document that is always read
 * whole and never queried by its parts, so normalising it would buy nothing and would
 * couple the local schema to every backend model change. The few fields that the list
 * screen sorts and displays are denormalised out alongside it.
 */
@Entity(tableName = "saved_plans")
data class SavedPlanEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    /**
     * Which local account saved this.
     *
     * [LEGACY_USER] marks rows written before the library was per-account. They are
     * adopted by the first account that opens the library rather than hidden, because
     * making a user's saved trips silently vanish is a worse outcome than one device
     * owner inheriting their own old plans.
     */
    val userId: Long = LEGACY_USER,
    val destination: String,
    val startDate: String,
    val endDate: String,
    val dayCount: Int,
    val totalCost: Double,
    val currency: String,
    val request: String,
    val planJson: String,
    val createdAt: Long,
    /**
     * The community post this row was copied from, or null for a trip planned here.
     *
     * Exists so saving is idempotent by *identity* rather than by timing. Without it,
     * save -> unsave -> save left two identical trips in the library: the server's save
     * is keyed on (plan, user) and so is a no-op the second time, but the copy is not.
     * Deduping on contents instead would break the legitimate case where someone deleted
     * their copy and wants it back.
     */
    val sharedPlanId: String? = null,
) {
    companion object {
        /** Owner id for rows saved before the library became per-account. */
        const val LEGACY_USER = 0L
    }
}

@Dao
interface SavedPlanDao {

    @Query("SELECT * FROM saved_plans WHERE userId = :userId ORDER BY createdAt DESC")
    fun observeFor(userId: Long): Flow<List<SavedPlanEntity>>

    /** One-time hand-over of pre-account rows; see [SavedPlanEntity.userId]. */
    @Query("UPDATE saved_plans SET userId = :userId WHERE userId = 0")
    suspend fun adoptLegacy(userId: Long): Int

    @Insert
    suspend fun insert(plan: SavedPlanEntity): Long

    /** Is this community post already in this account's library? */
    @Query("SELECT * FROM saved_plans WHERE userId = :userId AND sharedPlanId = :sharedPlanId LIMIT 1")
    suspend fun findShared(userId: Long, sharedPlanId: String): SavedPlanEntity?

    @Delete
    suspend fun delete(plan: SavedPlanEntity)
}
