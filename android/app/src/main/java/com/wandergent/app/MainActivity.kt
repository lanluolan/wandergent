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
import com.wandergent.app.data.LlmCredentials
import com.wandergent.app.data.Network
import com.wandergent.app.data.ThemeMode
import com.wandergent.app.data.local.SettingsStore
import com.wandergent.app.ui.AppRoot
import com.wandergent.app.ui.theme.WandergentTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            // Device-wide rather than per-account, so it is read here rather than
            // inside the app shell: it has to be in force before there is an account,
            // on the login screen.
            val settings = remember { SettingsStore(applicationContext) }
            val mode by settings.themeMode.collectAsStateWithLifecycle(ThemeMode.DEFAULT)

            // Read here rather than on the planning screen: an anonymous run uses it
            // too, and the network layer needs it before the first request, not when
            // some composable happens to mount.
            val llm by settings.llmCredentials.collectAsStateWithLifecycle(LlmCredentials.NONE)
            LaunchedEffect(llm) { Network.llmCredentials = llm }

            val dark = when (mode) {
                ThemeMode.SYSTEM -> isSystemInDarkTheme()
                ThemeMode.LIGHT -> false
                ThemeMode.DARK -> true
            }

            // The manifest theme picks the status bar icon colour from the *system* dark
            // mode, which is the wrong source once the app has its own Light/Dark/System
            // setting: force dark on a light phone and the bar keeps dark-on-dark icons.
            // The window is the only place this can be corrected, so it is corrected here.
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
