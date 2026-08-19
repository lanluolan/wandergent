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
 * Split by what the setting is *about*. Currency belongs to the traveller, so it is
 * keyed by user id and a second account starts from the default instead of inheriting
 * whatever the last person picked. Theme belongs to the device -- it has to apply on the
 * login screen, where there is no account yet.
 *
 * Kept out of [SessionStore], which holds *who is signed in*, and out of the Room user
 * table, which would mean a schema migration for a string.
 */
class SettingsStore(private val context: Context) {

    private fun currencyKey(userId: Long) = stringPreferencesKey("currency_$userId")

    private val themeKey = stringPreferencesKey("theme_mode")

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

}
