package com.wandergent.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.wandergent.app.data.ThemeMode
import com.wandergent.app.data.local.SettingsStore
import com.wandergent.app.ui.AppRoot
import com.wandergent.app.ui.theme.WandergentTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            // Device-wide, not per-account: it must apply on the login screen too.
            val settings = remember { SettingsStore(applicationContext) }
            val mode by settings.themeMode.collectAsStateWithLifecycle(ThemeMode.DEFAULT)

            // Erase the API key the removed "AI model" screen used to store.
            LaunchedEffect(Unit) { settings.forgetLlmCredentials() }

            val dark = when (mode) {
                ThemeMode.SYSTEM -> isSystemInDarkTheme()
                ThemeMode.LIGHT -> false
                ThemeMode.DARK -> true
            }

            // The manifest theme takes bar icon colour from the *system* dark mode, so
            // forcing dark on a light phone would leave dark-on-dark icons. Only the
            // window can correct that.
            val view = LocalView.current
            SideEffect {
                WindowCompat.getInsetsController(window, view).apply {
                    isAppearanceLightStatusBars = !dark
                    isAppearanceLightNavigationBars = !dark
                }
            }

            WandergentTheme(darkTheme = dark) {
                AppRoot()
            }
        }
    }
}
