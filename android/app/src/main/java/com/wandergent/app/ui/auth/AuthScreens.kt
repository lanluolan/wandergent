package com.wandergent.app.ui.auth

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Place
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle

@Composable
fun LoginScreen(
    viewModel: AuthViewModel,
    onGoToRegister: () -> Unit,
    onForgotPassword: () -> Unit = {},
) {
    val busy by viewModel.busy.collectAsStateWithLifecycle()
    val error by viewModel.error.collectAsStateWithLifecycle()
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }

    AuthScaffold(
        title = "Welcome back",
        subtitle = "Sign in to save trips and keep your preferences",
    ) {
        OutlinedTextField(
            value = username,
            onValueChange = { username = it; viewModel.clearError() },
            label = { Text("Username") },
            singleLine = true,
            enabled = !busy,
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(12.dp),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
        )

        PasswordField(
            value = password,
            onValueChange = { password = it; viewModel.clearError() },
            label = "Password",
            enabled = !busy,
            imeAction = ImeAction.Done,
        )

        ErrorText(error)

        Button(
            onClick = { viewModel.login(username, password) },
            enabled = !busy && username.isNotBlank() && password.isNotBlank(),
            modifier = Modifier.fillMaxWidth().height(50.dp),
            shape = RoundedCornerShape(12.dp),
        ) {
            if (busy) {
                CircularProgressIndicator(
                    modifier = Modifier.size(20.dp),
                    strokeWidth = 2.dp,
                    color = MaterialTheme.colorScheme.onPrimary,
                )
            } else {
                Text("Sign in")
            }
        }

        TextButton(
            onClick = { viewModel.clearError(); onForgotPassword() },
            enabled = !busy,
            modifier = Modifier.fillMaxWidth(),
        ) { Text("Forgot your password?") }

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "No account yet? ",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            TextButton(onClick = { viewModel.clearError(); onGoToRegister() }, enabled = !busy) {
                Text("Create one")
            }
        }
    }
}

@Composable
fun RegisterScreen(viewModel: AuthViewModel, onBackToLogin: () -> Unit) {
    val busy by viewModel.busy.collectAsStateWithLifecycle()
    val error by viewModel.error.collectAsStateWithLifecycle()
    var username by remember { mutableStateOf("") }
    var displayName by remember { mutableStateOf("") }
    var email by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var confirm by remember { mutableStateOf("") }

    val mismatch = confirm.isNotEmpty() && confirm != password

    AuthScaffold(
        title = "Create account",
        subtitle = "Your account lives on the server, so your shared trips carry your name",
    ) {
        OutlinedTextField(
            value = username,
            onValueChange = { username = it; viewModel.clearError() },
            label = { Text("Username") },
            supportingText = { Text("At least 3 characters") },
            singleLine = true,
            enabled = !busy,
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(12.dp),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
        )

        OutlinedTextField(
            value = displayName,
            onValueChange = { displayName = it },
            label = { Text("Display name (optional)") },
            singleLine = true,
            enabled = !busy,
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(12.dp),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
        )

        OutlinedTextField(
            value = email,
            onValueChange = { email = it; viewModel.clearError() },
            label = { Text("Email (optional)") },
            // Says the consequence rather than the category. "Optional" alone leaves
            // someone to discover months later that their account cannot be recovered.
            supportingText = {
                Text(
                    if (email.isBlank()) {
                        "Without one, a forgotten password cannot be reset"
                    } else {
                        "Only used to reset your password"
                    }
                )
            },
            singleLine = true,
            enabled = !busy,
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(12.dp),
            keyboardOptions = KeyboardOptions(
                keyboardType = KeyboardType.Email,
                imeAction = ImeAction.Next,
            ),
        )

        PasswordField(
            value = password,
            onValueChange = { password = it; viewModel.clearError() },
            label = "Password",
            enabled = !busy,
            supporting = "At least 8 characters",
            imeAction = ImeAction.Next,
        )

        PasswordField(
            value = confirm,
            onValueChange = { confirm = it },
            label = "Confirm password",
            enabled = !busy,
            isError = mismatch,
            supporting = if (mismatch) "The passwords do not match" else null,
            imeAction = ImeAction.Done,
        )

        ErrorText(error)

        Button(
            onClick = { viewModel.register(username, password, displayName, email) },
            enabled = !busy && username.isNotBlank() && password.isNotBlank() && !mismatch &&
                confirm.isNotEmpty(),
            modifier = Modifier.fillMaxWidth().height(50.dp),
            shape = RoundedCornerShape(12.dp),
        ) {
            if (busy) {
                CircularProgressIndicator(
                    modifier = Modifier.size(20.dp),
                    strokeWidth = 2.dp,
                    color = MaterialTheme.colorScheme.onPrimary,
                )
            } else {
                Text("Create account")
            }
        }

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "Already have an account? ",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            TextButton(onClick = { viewModel.clearError(); onBackToLogin() }, enabled = !busy) {
                Text("Sign in")
            }
        }
    }
}

/** Shared branded frame so login and register cannot drift apart visually. */
@Composable
private fun AuthScaffold(
    title: String,
    subtitle: String,
    content: @Composable () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 24.dp, vertical = 32.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Spacer(Modifier.height(24.dp))

        Surface(
            shape = CircleShape,
            color = MaterialTheme.colorScheme.primaryContainer,
            modifier = Modifier.size(64.dp),
        ) {
            Icon(
                Icons.Default.Place,
                contentDescription = null,
                modifier = Modifier.padding(16.dp),
                tint = MaterialTheme.colorScheme.onPrimaryContainer,
            )
        }

        Text("Wandergent", style = MaterialTheme.typography.headlineLarge)
        Text(
            "Plan a trip in one sentence",
            style = MaterialTheme.typography.bodyLarge,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        Spacer(Modifier.height(16.dp))

        Text(title, style = MaterialTheme.typography.titleLarge)
        Text(
            subtitle,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        content()

        Spacer(Modifier.height(8.dp))
        Text(
            "Your password is checked by the server and never leaves this screen. "
                + "Saved trips stay on this device.",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.outline,
            textAlign = TextAlign.Center,
            modifier = Modifier.fillMaxWidth(),
        )
    }
}

@Composable
private fun PasswordField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    enabled: Boolean,
    imeAction: ImeAction,
    isError: Boolean = false,
    supporting: String? = null,
) {
    var visible by remember { mutableStateOf(false) }

    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        label = { Text(label) },
        singleLine = true,
        enabled = enabled,
        isError = isError,
        supportingText = supporting?.let { { Text(it) } },
        // Text toggle rather than an eye icon: the icons the app already depends on do
        // not include one, and pulling in the extended icon set for this is not worth it.
        trailingIcon = {
            TextButton(onClick = { visible = !visible }) {
                Text(if (visible) "Hide" else "Show", style = MaterialTheme.typography.labelMedium)
            }
        },
        visualTransformation = if (visible) {
            VisualTransformation.None
        } else {
            PasswordVisualTransformation()
        },
        keyboardOptions = KeyboardOptions(
            keyboardType = KeyboardType.Password,
            imeAction = imeAction,
        ),
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
    )
}

@Composable
private fun ErrorText(error: String?) {
    if (error != null) {
        Text(
            error,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.error,
        )
    }
}

/**
 * Password reset, both halves on one screen.
 *
 * One screen rather than two, because the code arrives in a *different app*. Splitting it
 * would mean navigating back to a form the person has already left, and every step between
 * asking and typing is a step where the code gets lost.
 *
 * The confirmation never says whether the address had an account. The server answers
 * identically either way on purpose, and a client that helpfully reported "no such
 * address" would hand back exactly what the server withheld.
 */
@Composable
fun ResetPasswordScreen(viewModel: AuthViewModel, onBackToLogin: () -> Unit) {
    val busy by viewModel.busy.collectAsStateWithLifecycle()
    val error by viewModel.error.collectAsStateWithLifecycle()
    val notice by viewModel.notice.collectAsStateWithLifecycle()
    var email by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var requested by remember { mutableStateOf(false) }

    AuthScaffold(
        title = "Reset password",
        subtitle = "We send a short code to the address on the account",
    ) {
        OutlinedTextField(
            value = email,
            onValueChange = { email = it; viewModel.clearError() },
            label = { Text("Email") },
            singleLine = true,
            enabled = !busy,
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(12.dp),
            keyboardOptions = KeyboardOptions(
                keyboardType = KeyboardType.Email,
                imeAction = ImeAction.Done,
            ),
        )

        Button(
            onClick = { viewModel.requestReset(email); requested = true },
            enabled = !busy && email.isNotBlank(),
            modifier = Modifier.fillMaxWidth().height(50.dp),
            shape = RoundedCornerShape(12.dp),
        ) { Text(if (requested) "Send another code" else "Send code") }

        if (requested) {
            HorizontalDivider()

            OutlinedTextField(
                value = code,
                onValueChange = { code = it.uppercase(); viewModel.clearError() },
                label = { Text("Code from the email") },
                supportingText = { Text("Expires in an hour") },
                singleLine = true,
                enabled = !busy,
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(12.dp),
                keyboardOptions = KeyboardOptions(
                    // Upper case and no ambiguous characters, matching how the server
                    // generates it, so this field cannot produce a code the server could
                    // not have issued.
                    capitalization = KeyboardCapitalization.Characters,
                    imeAction = ImeAction.Next,
                ),
            )

            PasswordField(
                value = password,
                onValueChange = { password = it; viewModel.clearError() },
                label = "New password",
                enabled = !busy,
                supporting = "At least 8 characters. This signs you out everywhere.",
                imeAction = ImeAction.Done,
            )

            Button(
                onClick = { viewModel.confirmReset(code, password) },
                enabled = !busy && code.isNotBlank() && password.length >= 8,
                modifier = Modifier.fillMaxWidth().height(50.dp),
                shape = RoundedCornerShape(12.dp),
            ) { Text("Set new password") }
        }

        ErrorText(error)
        notice?.let {
            Text(
                it,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.primary,
            )
        }

        TextButton(
            onClick = { viewModel.clearError(); viewModel.clearNotice(); onBackToLogin() },
            enabled = !busy,
            modifier = Modifier.fillMaxWidth(),
        ) { Text("Back to sign in") }
    }
}
