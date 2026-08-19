package com.wandergent.app.data

/**
 * Light, dark, or whatever the phone is set to.
 *
 * Stored per device rather than per account, unlike [Currency]: it is a property of the
 * screen you are looking at, not of the traveller. It also has to apply on the login
 * screen, where there is no account yet.
 */
enum class ThemeMode(val label: String) {
    SYSTEM("System"),
    LIGHT("Light"),
    DARK("Dark"),
    ;

    companion object {
        val DEFAULT = SYSTEM

        fun fromName(raw: String?): ThemeMode? =
            entries.firstOrNull { it.name.equals(raw, ignoreCase = true) }
    }
}
