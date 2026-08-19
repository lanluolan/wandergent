package com.wandergent.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.wandergent.app.data.ServerConfig
import com.wandergent.app.data.ThemeMode
import com.wandergent.app.data.local.SettingsStore
import com.wandergent.app.ui.AppRoot
import com.wandergent.app.ui.theme.WandergentTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            // Theme and server address are device-wide, so they are read here rather
            // than inside the app shell: both have to be in force before there is an
            // account, on the login screen.
            val settings = remember { SettingsStore(applicationContext) }
            val mode by settings.themeMode.collectAsStateWithLifecycle(ThemeMode.DEFAULT)
            val serverUrl by settings.serverUrl.collectAsStateWithLifecycle(ServerConfig.default)

            LaunchedEffect(serverUrl) { ServerConfig.set(serverUrl) }

            val dark = when (mode) {
                ThemeMode.SYSTEM -> isSystemInDarkTheme()
                ThemeMode.LIGHT -> false
                ThemeMode.DARK -> true
            }

            WandergentTheme(darkTheme = dark) {
                AppRoot()
            }
        }
    }
}
