package com.wandergent.app.data.local

import androidx.room.Dao
import androidx.room.Entity
import androidx.room.Index
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

/**
 * The local half of an account.
 *
 * **No password material lives here any more** (2026-08-17). Verification happens on the
 * server against PBKDF2, and the columns that used to hold a local salt and SHA-256 hash
 * were dropped in the v5 -> v6 migration rather than left behind: a dead credential column
 * is a live vulnerability the day somebody reinstates a "quick offline login" against it.
 *
 * What remains is a storage partition. The library, the transcript and the currency
 * setting are all keyed on [id]; [serverAccountId] says whose they are.
 */
@Entity(
    tableName = "users",
    indices = [Index(value = ["username"], unique = true)],
)
data class UserEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val username: String,
    val displayName: String,
    /**
     * The server account this local row belongs to.
     *
     * Identity lives on the server now; this row exists only to *partition local
     * storage* -- the library, the transcript, the currency setting are all keyed on
     * [id], and a `Long` row id is cheaper to key on than a uuid across three tables.
     *
     * Blank on rows written before accounts moved server-side. Those are adopted by the
     * first server login that matches their username.
     */
    val serverAccountId: String = "",
    /**
     * Mirrored from the server so the profile can say whether this account is
     * recoverable without a round trip on every open. The server is the authority --
     * this is a cache, refreshed on sign-in and whenever the address changes.
     */
    val email: String = "",
    val emailVerified: Boolean = false,
    val createdAt: Long,
)

@Dao
interface UserDao {

    @Query("SELECT * FROM users WHERE username = :username LIMIT 1")
    suspend fun findByUsername(username: String): UserEntity?

    @Query("SELECT * FROM users WHERE id = :id LIMIT 1")
    fun observeById(id: Long): Flow<UserEntity?>

    @Insert
    suspend fun insert(user: UserEntity): Long

    /** Attach a local row to a server account, and refresh the name it shows. */
    @Query(
        "UPDATE users SET serverAccountId = :accountId, displayName = :displayName," +
            " email = :email, emailVerified = :emailVerified WHERE id = :id"
    )
    suspend fun linkToAccount(
        id: Long,
        accountId: String,
        displayName: String,
        email: String,
        emailVerified: Boolean,
    )
}
