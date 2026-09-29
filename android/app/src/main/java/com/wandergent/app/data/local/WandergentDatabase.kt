package com.wandergent.app.data.local

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.SQLiteConnection
import androidx.sqlite.execSQL

@Database(
    entities = [SavedPlanEntity::class, UserEntity::class, ChatTurnEntity::class],
    version = 7,
    exportSchema = false,
)
abstract class WandergentDatabase : RoomDatabase() {

    abstract fun savedPlanDao(): SavedPlanDao

    abstract fun userDao(): UserDao

    abstract fun chatTurnDao(): ChatTurnDao

    companion object {
        /**
         * v1 -> v2 adds local accounts. A real migration, not a destructive fallback:
         * saved itineraries are the one thing here a user would mind losing.
         */
        private val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(connection: SQLiteConnection) {
                connection.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        username TEXT NOT NULL,
                        displayName TEXT NOT NULL,
                        passwordSalt TEXT NOT NULL,
                        passwordHash TEXT NOT NULL,
                        createdAt INTEGER NOT NULL
                    )
                    """.trimIndent()
                )
                connection.execSQL(
                    "CREATE UNIQUE INDEX IF NOT EXISTS index_users_username ON users (username)"
                )
            }
        }

        /**
         * v2 -> v3 gives every saved plan an owner. Existing rows default to `0` -- a
         * migration has no session -- and the first account to open the library adopts them.
         */
        private val MIGRATION_2_3 = object : Migration(2, 3) {
            override fun migrate(connection: SQLiteConnection) {
                connection.execSQL(
                    "ALTER TABLE saved_plans ADD COLUMN userId INTEGER NOT NULL DEFAULT 0"
                )
            }
        }

        /**
         * v3 -> v4 keeps the planning conversation across restarts. Since a follow-up
         * edits the plan above it, losing the transcript loses that plan too.
         */
        private val MIGRATION_3_4 = object : Migration(3, 4) {
            override fun migrate(connection: SQLiteConnection) {
                connection.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS chat_turns (
                        id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        userId INTEGER NOT NULL,
                        request TEXT NOT NULL,
                        planJson TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        createdAt INTEGER NOT NULL
                    )
                    """.trimIndent()
                )
            }
        }

        /**
         * v4 -> v5 records which community post a saved trip was copied from. Nullable with
         * no default: older rows were planned here, not copied. See
         * [SavedPlanEntity.sharedPlanId] for why the identity matters.
         */
        private val MIGRATION_4_5 = object : Migration(4, 5) {
            override fun migrate(connection: SQLiteConnection) {
                connection.execSQL("ALTER TABLE saved_plans ADD COLUMN sharedPlanId TEXT")
            }
        }

        /**
         * v5 -> v6 moves accounts to the server.
         *
         * A table rebuild rather than an `ALTER TABLE`, because the point is what it
         * **removes**: `passwordSalt` and `passwordHash`. A dead credential column is how a
         * "quick offline login" gets reinstated against it later. Rows carry across with a
         * blank `serverAccountId`, adopted by the first login matching their username.
         */
        private val MIGRATION_5_6 = object : Migration(5, 6) {
            override fun migrate(connection: SQLiteConnection) {
                connection.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS users_v6 (
                        id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        username TEXT NOT NULL,
                        displayName TEXT NOT NULL,
                        serverAccountId TEXT NOT NULL DEFAULT '',
                        createdAt INTEGER NOT NULL
                    )
                    """.trimIndent()
                )
                connection.execSQL(
                    "INSERT INTO users_v6 (id, username, displayName, serverAccountId, createdAt)"
                        + " SELECT id, username, displayName, '', createdAt FROM users"
                )
                connection.execSQL("DROP TABLE users")
                connection.execSQL("ALTER TABLE users_v6 RENAME TO users")
                connection.execSQL(
                    "CREATE UNIQUE INDEX IF NOT EXISTS index_users_username ON users (username)"
                )
            }
        }

        /**
         * v6 -> v7 caches the account's email and whether it is proven. A cache, not a
         * source of truth: the server decides, and this is refreshed from it on sign-in.
         */
        private val MIGRATION_6_7 = object : Migration(6, 7) {
            override fun migrate(connection: SQLiteConnection) {
                connection.execSQL("ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
                connection.execSQL(
                    "ALTER TABLE users ADD COLUMN emailVerified INTEGER NOT NULL DEFAULT 0"
                )
            }
        }

        @Volatile
        private var instance: WandergentDatabase? = null

        /** Process-wide singleton; Room is expensive to open and safe to share. */
        fun get(context: Context): WandergentDatabase =
            instance ?: synchronized(this) {
                instance ?: Room.databaseBuilder(
                    context.applicationContext,
                    WandergentDatabase::class.java,
                    "wandergent.db",
                )
                    .addMigrations(
                        MIGRATION_1_2,
                        MIGRATION_2_3,
                        MIGRATION_3_4,
                        MIGRATION_4_5,
                        MIGRATION_5_6,
                        MIGRATION_6_7,
                    )
                    .build()
                    .also { instance = it }
            }
    }
}
