package com.wandergent.app.data.local

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

private val Context.sessionDataStore by preferencesDataStore(name = "session")

/**
 * The live session: which local row owns this device's data, and the token that proves who
 * that is to the server. The `Long` is a storage key -- the library and the transcript are
 * partitioned on it -- and only the token is identity.
 *
 * **Known gap: the token is stored in plain DataStore**, readable with root.
 * `EncryptedSharedPreferences` would cost another dependency; today's mitigation is a
 * 30-day expiry, server-side revocation on sign-out, and the backup exclusion in
 * `res/xml/data_extraction_rules.xml`.
 */
class SessionStore(private val context: Context) {

    private val currentUserIdKey = longPreferencesKey("current_user_id")
    private val tokenKey = stringPreferencesKey("auth_token")

    val currentUserId: Flow<Long?> =
        context.sessionDataStore.data.map { preferences -> preferences[currentUserIdKey] }

    /** Read once at startup to re-arm the HTTP interceptor; not observed. */
    suspend fun token(): String? =
        context.sessionDataStore.data.first()[tokenKey]?.takeIf { it.isNotEmpty() }

    suspend fun signIn(userId: Long, token: String) {
        context.sessionDataStore.edit {
            it[currentUserIdKey] = userId
            it[tokenKey] = token
        }
    }

    suspend fun signOut() {
        context.sessionDataStore.edit {
            it.remove(currentUserIdKey)
            it.remove(tokenKey)
        }
    }
}
