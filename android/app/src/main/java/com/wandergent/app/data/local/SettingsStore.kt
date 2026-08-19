package com.wandergent.app.data.local

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.wandergent.app.data.Currency
import com.wandergent.app.data.LlmCredentials
import com.wandergent.app.data.ThemeMode
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.settingsDataStore by preferencesDataStore(name = "settings")

/**
 * Preferences that are choices, not credentials.
 *
 * Split by what the setting is *about*. Currency belongs to the traveller, so it is
 * keyed by user id and a second account starts from the default instead of inheriting
 * whatever the last person picked. Theme belongs to the device -- it has to apply on the
 * login screen, where there is no account yet.
 *
 * Kept out of [SessionStore], which holds *who is signed in*, and out of the Room user
 * table, which would mean a schema migration for a string.
 *
 * **One of these is a secret.** The LLM key is stored here in the clear, which is the
 * same protection the OS gives every app's private storage and no more: it is safe from
 * other apps, not from someone holding an unlocked rooted phone. `allowBackup="false"`
 * in the manifest is what keeps it off Google's servers and out of `adb backup`, and it
 * is load-bearing for that reason. Keystore-wrapping it would raise the bar; it is not
 * worth the key-rotation machinery for a credential the traveller can revoke upstream in
 * one click.
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
     * The traveller's own LLM account, or [LlmCredentials.NONE].
     *
     * Device-wide rather than per-account: the key belongs to whoever holds the phone,
     * and it has to be in force for an anonymous run too -- planning does not require
     * signing in.
     */
    val llmCredentials: Flow<LlmCredentials> = context.settingsDataStore.data.map { preferences ->
        val key = preferences[llmKeyKey].orEmpty()
        if (key.isBlank()) {
            LlmCredentials.NONE
        } else {
            LlmCredentials(
                apiKey = key,
                baseUrl = preferences[llmBaseUrlKey].orEmpty(),
                model = preferences[llmModelKey].orEmpty(),
            )
        }
    }

    /** Blank [LlmCredentials.apiKey] clears all three, so "remove my key" leaves nothing. */
    suspend fun setLlmCredentials(credentials: LlmCredentials) {
        context.settingsDataStore.edit { preferences ->
            if (!credentials.isSet) {
                preferences.remove(llmKeyKey)
                preferences.remove(llmBaseUrlKey)
                preferences.remove(llmModelKey)
                return@edit
            }
            preferences[llmKeyKey] = credentials.apiKey.trim()
            preferences[llmBaseUrlKey] = credentials.baseUrl.trim()
            preferences[llmModelKey] = credentials.model.trim()
        }
    }

}
