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
 * The live session: which local row owns this device's data, and the token that proves
 * who that is to the server.
 *
 * Two values rather than one because they answer different questions. The `Long` is a
 * local storage key -- the library and the transcript are partitioned on it. The token is
 * identity, and only the server can judge it.
 *
 * **Known gap: the token is stored in plain DataStore.** Readable with root, and it lands
 * in a device backup. `EncryptedSharedPreferences` would fix that at the cost of another
 * dependency; the honest mitigation today is that the token expires in 30 days and
 * signing out revokes it server-side.
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
