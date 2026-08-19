package com.wandergent.app.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.wandergent.app.ui.auth.AuthViewModel
import com.wandergent.app.ui.auth.LoginScreen
import com.wandergent.app.ui.auth.RegisterScreen
import com.wandergent.app.ui.auth.ResetPasswordScreen
import com.wandergent.app.ui.auth.SessionState

private const val ROUTE_SPLASH = "splash"
private const val ROUTE_LOGIN = "login"
private const val ROUTE_REGISTER = "register"
private const val ROUTE_RESET = "reset"
private const val ROUTE_MAIN = "main"

/**
 * Top-level routing between the auth flow and the app.
 *
 * The session is the single source of truth: screens only ask the view model to sign in
 * or out, and navigation follows the resulting state. That keeps sign-in, sign-out and
 * "already signed in on launch" as one code path instead of three, and means the back
 * stack cannot leak a signed-in screen behind the login form.
 *
 * Routing waits for [SessionState.Loading] to resolve, otherwise every cold start would
 * flash the login screen before the persisted session finishes loading.
 */
@Composable
fun AppRoot(authViewModel: AuthViewModel = viewModel()) {
    val session by authViewModel.session.collectAsStateWithLifecycle()
    val navController = rememberNavController()

    // Keyed on *which* state, not on the state object. `SessionState.SignedIn` carries the
    // user row, so anything that edits it -- confirming an email, changing an address --
    // produced a new instance and re-ran this, navigating to `main` and resetting the tab
    // to Plan. Someone confirming their email from the profile was thrown out of the
    // profile, which reads as the app losing its place.
    val phase = when (session) {
        is SessionState.Loading -> "loading"
        is SessionState.SignedIn -> "in"
        is SessionState.SignedOut -> "out"
    }

    LaunchedEffect(phase) {
        when (session) {
            is SessionState.Loading -> Unit
            is SessionState.SignedIn -> navController.navigate(ROUTE_MAIN) {
                popUpTo(navController.graph.id) { inclusive = true }
            }
            is SessionState.SignedOut -> navController.navigate(ROUTE_LOGIN) {
                popUpTo(navController.graph.id) { inclusive = true }
            }
        }
    }

    NavHost(navController = navController, startDestination = ROUTE_SPLASH) {
        composable(ROUTE_SPLASH) { Splash() }

        composable(ROUTE_LOGIN) {
            LoginScreen(
                viewModel = authViewModel,
                onGoToRegister = { navController.navigate(ROUTE_REGISTER) },
                onForgotPassword = { navController.navigate(ROUTE_RESET) },
            )
        }

        composable(ROUTE_REGISTER) {
            RegisterScreen(
                viewModel = authViewModel,
                onBackToLogin = { navController.popBackStack() },
            )
        }

        composable(ROUTE_RESET) {
            ResetPasswordScreen(
                viewModel = authViewModel,
                onBackToLogin = { navController.popBackStack() },
            )
        }

        composable(ROUTE_MAIN) {
            // The user is passed down rather than re-read through viewModel(): a nested
            // NavHost entry would get its own AuthViewModel instance, not this one.
            WandergentApp(
                user = (session as? SessionState.SignedIn)?.user,
                onLogout = authViewModel::logout,
            )
        }
    }
}

@Composable
private fun Splash() {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        CircularProgressIndicator()
    }
}
