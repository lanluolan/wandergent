package com.wandergent.app.data.local

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.wandergent.app.data.Currency
import com.wandergent.app.data.ThemeMode
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.settingsDataStore by preferencesDataStore(name = "settings")

/**
 * Preferences that are choices, not credentials.
 *
 * Split by what the setting is *about*. Currency is keyed by user id, so a second account
 * starts from the default rather than inheriting. Theme is not: it has to apply on the
 * login screen, where there is no account yet. Both stay out of [SessionStore], which
 * holds who is signed in, and out of Room, which would mean a migration for a string.
 */
class SettingsStore(private val context: Context) {

    private fun currencyKey(userId: Long) = stringPreferencesKey("currency_$userId")

    private val themeKey = stringPreferencesKey("theme_mode")
    private val llmKeyKey = stringPreferencesKey("llm_api_key")
    private val llmBaseUrlKey = stringPreferencesKey("llm_base_url")
    private val llmModelKey = stringPreferencesKey("llm_model")

    /** Never null: an account that has not chosen falls back to [Currency.DEFAULT]. */
    fun currency(userId: Long): Flow<Currency> =
        context.settingsDataStore.data.map { preferences ->
            Currency.fromCode(preferences[currencyKey(userId)]) ?: Currency.DEFAULT
        }

    suspend fun setCurrency(userId: Long, currency: Currency) {
        context.settingsDataStore.edit { it[currencyKey(userId)] = currency.code }
    }

    val themeMode: Flow<ThemeMode> = context.settingsDataStore.data.map { preferences ->
        ThemeMode.fromName(preferences[themeKey]) ?: ThemeMode.DEFAULT
    }

    suspend fun setThemeMode(mode: ThemeMode) {
        context.settingsDataStore.edit { it[themeKey] = mode.name }
    }

    /**
     * Delete the LLM credentials the removed "AI model" screen used to store.
     *
     * Nothing in the UI can reach them any more, so a key typed in before that feature was
     * dropped would sit here forever. Runs every launch; a no-op after the first.
     */
    suspend fun forgetLlmCredentials() {
        context.settingsDataStore.edit { preferences ->
            preferences.remove(llmKeyKey)
            preferences.remove(llmBaseUrlKey)
            preferences.remove(llmModelKey)
        }
    }
}
