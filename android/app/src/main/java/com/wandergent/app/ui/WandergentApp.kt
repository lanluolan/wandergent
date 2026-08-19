package com.wandergent.app.ui

import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Place
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.rememberNavController
import com.wandergent.app.data.Currency
import com.wandergent.app.data.ServerConfig
import com.wandergent.app.data.ThemeMode
import com.wandergent.app.data.local.SavedPlanEntity
import com.wandergent.app.data.local.SettingsStore
import com.wandergent.app.data.local.UserEntity
import com.wandergent.app.ui.auth.AuthViewModel
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.launch

private data class Destination(
    val route: String,
    val label: String,
    val icon: ImageVector,
)

private val NAV_BAR_HEIGHT = 60.dp

private val DESTINATIONS = listOf(
    Destination("plan", "Plan", Icons.Default.Search),
    Destination("saved", "Saved", Icons.Default.Favorite),
    // A globe would be the obvious glyph, but `Public` lives in
    // material-icons-extended, which is a large dependency for one tab icon.
    // A pin is distinct from the heart, the magnifier and the person beside it.
    Destination("community", "Community", Icons.Default.Place),
    Destination("profile", "You", Icons.Default.Person),
)

/**
 * App shell: bottom navigation over four tabs.
 *
 * The tabs are the product skeleton the later phases slot into -- saved plans become
 * cloud-synced once there are accounts, and "You" is where sign-in will live. Having
 * the shell now means those land as content rather than as a restructure.
 */
@Composable
fun WandergentApp(user: UserEntity?, onLogout: () -> Unit) {
    val navController = rememberNavController()
    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = backStackEntry?.destination?.route

    // Settlement currency lives with the account, so it is read once here and handed to
    // the tabs that need it -- the planner to ask in, the profile to change.
    val context = LocalContext.current
    val settings = remember(context) { SettingsStore(context) }
    val scope = rememberCoroutineScope()
    val currency by remember(user?.id) {
        user?.id?.let(settings::currency) ?: flowOf(Currency.DEFAULT)
    }.collectAsStateWithLifecycle(Currency.DEFAULT)
    // Device-wide, unlike currency: both apply before anyone is signed in. Read again
    // here rather than passed down from the activity, so the profile tab can show and
    // change them without threading state through every screen in between.
    val themeMode by settings.themeMode.collectAsStateWithLifecycle(ThemeMode.DEFAULT)
    val serverUrl by settings.serverUrl.collectAsStateWithLifecycle(ServerConfig.default)

    /** A plan handed from the library to the planner, to be edited. Cleared on arrival. */
    var reviseSaved by remember { mutableStateOf<SavedPlanEntity?>(null) }

    // Held at the shell rather than inside the community destination, so the tab that
    // publishes (Saved) and the tab that lists (Community) share one instance. Scoped to
    // the activity, so a publish from the library shows up in the feed immediately
    // instead of waiting for whatever happens to reload it next.
    // The same instance the auth flow used, so the profile reads the account state that
    // sign-in wrote rather than a second copy of it.
    val auth: AuthViewModel = viewModel()
    val authNotice by auth.notice.collectAsStateWithLifecycle()
    val authError by auth.error.collectAsStateWithLifecycle()

    val community: CommunityViewModel = viewModel()
    val shareMessage by community.message.collectAsStateWithLifecycle()

    Scaffold(
        bottomBar = {
            // Shorter than Material's 80dp default. The tabs are a switch, not content;
            // on a chat screen every dp they give back goes to the transcript. The
            // inner row honours the height because `defaultMinSize` yields to an
            // incoming fixed constraint.
            // Page colour, matching the composer directly above it: the default
            // `surfaceContainer` drew a second band and split the bottom in two.
            NavigationBar(
                modifier = Modifier.height(NAV_BAR_HEIGHT),
                containerColor = MaterialTheme.colorScheme.surface,
            ) {
                DESTINATIONS.forEach { destination ->
                    NavigationBarItem(
                        selected = currentRoute == destination.route,
                        onClick = {
                            navController.navigate(destination.route) {
                                // Keep one entry per tab and preserve each tab's state,
                                // so switching tabs never stacks duplicates.
                                popUpTo(navController.graph.findStartDestination().id) {
                                    saveState = true
                                }
                                launchSingleTop = true
                                restoreState = true
                            }
                        },
                        icon = { Icon(destination.icon, contentDescription = destination.label) },
                        label = { Text(destination.label) },
                    )
                }
            }
        },
    ) { padding ->
        NavHost(
            navController = navController,
            startDestination = "plan",
            modifier = Modifier.padding(padding),
        ) {
            composable("plan") {
                PlanScreen(
                    userId = user?.id,
                    currency = currency.code,
                    reviseSaved = reviseSaved,
                    onReviseConsumed = { reviseSaved = null },
                )
            }
            composable("saved") {
                SavedScreen(
                    userId = user?.id,
                    shareMessage = shareMessage,
                    onShareMessageShown = community::clearMessage,
                    onShare = { plan, note, includeRequest ->
                        community.setUser(user?.id, user?.serverAccountId)
                        community.publish(
                            plan,
                            user?.displayName.orEmpty(),
                            note,
                            includeRequest,
                        )
                    },
                    onRevise = { plan ->
                        // Hoisted here rather than passed through navigation: the tab
                        // routes carry no arguments, and an itinerary is far too big to
                        // put in one. The planner consumes it and clears the handoff.
                        reviseSaved = plan
                        navController.navigate("plan") {
                            popUpTo(navController.graph.findStartDestination().id) {
                                saveState = true
                            }
                            launchSingleTop = true
                            restoreState = true
                        }
                    },
                )
            }
            composable("community") {
                CommunityScreen(
                    userId = user?.id,
                    accountId = user?.serverAccountId,
                    viewModel = community,
                )
            }
            composable("profile") {
                ProfileScreen(
                    user = user,
                    onVerifyEmail = auth::verifyEmail,
                    onResendVerification = auth::resendVerification,
                    onChangeEmail = auth::changeEmail,
                    authNotice = authNotice,
                    authError = authError,
                    onAuthMessageShown = { auth.clearNotice(); auth.clearError() },
                    currency = currency,
                    onCurrencyChange = { picked ->
                        user?.id?.let { scope.launch { settings.setCurrency(it, picked) } }
                    },
                    themeMode = themeMode,
                    onThemeChange = { scope.launch { settings.setThemeMode(it) } },
                    serverUrl = serverUrl,
                    onServerUrlChange = { scope.launch { settings.setServerUrl(it) } },
                    onServerUrlReset = { scope.launch { settings.resetServerUrl() } },
                    onLogout = onLogout,
                )
            }
        }
    }
}
