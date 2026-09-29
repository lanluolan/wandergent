package com.wandergent.app.data.local

import androidx.room.Dao
import androidx.room.Entity
import androidx.room.Index
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

/**
 * The local half of an account: a storage partition, not an identity. The library, the
 * transcript and the currency setting are all keyed on [id]; [serverAccountId] says whose
 * they are.
 *
 * **No password material lives here** (2026-08-17). The server verifies against PBKDF2,
 * and the old salt and hash columns were dropped in v5 -> v6 rather than left dead.
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
     * The server account this local row belongs to. Blank on rows written before accounts
     * moved server-side; the first login matching their username adopts those.
     */
    val serverAccountId: String = "",
    /** Mirrored from the server. A cache, refreshed on sign-in; the server decides. */
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
