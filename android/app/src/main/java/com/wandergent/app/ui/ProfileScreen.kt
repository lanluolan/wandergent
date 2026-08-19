package com.wandergent.app.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import com.wandergent.app.BuildConfig
import com.wandergent.app.data.Currency
import com.wandergent.app.data.LlmCredentials
import com.wandergent.app.data.LlmSupport
import com.wandergent.app.data.ThemeMode
import com.wandergent.app.data.local.UserEntity
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@Composable
fun ProfileScreen(
    user: UserEntity?,
    onVerifyEmail: (String) -> Unit = {},
    onResendVerification: () -> Unit = {},
    onChangeEmail: (String) -> Unit = {},
    authNotice: String? = null,
    authError: String? = null,
    onAuthMessageShown: () -> Unit = {},
    currency: Currency,
    onCurrencyChange: (Currency) -> Unit,
    themeMode: ThemeMode,
    onThemeChange: (ThemeMode) -> Unit,
    llmCredentials: LlmCredentials,
    llmSupport: LlmSupport,
    onLlmCredentialsChange: (LlmCredentials) -> Unit,
    onLogout: () -> Unit,
) {
    var confirmLogout by remember { mutableStateOf(false) }
    var pickingCurrency by remember { mutableStateOf(false) }
    var pickingTheme by remember { mutableStateOf(false) }
    var editingLlm by remember { mutableStateOf(false) }
    var managingEmail by remember { mutableStateOf(false) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Column(
            modifier = Modifier.fillMaxWidth().padding(vertical = 24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Surface(
                shape = CircleShape,
                color = MaterialTheme.colorScheme.primaryContainer,
                modifier = Modifier.size(72.dp),
            ) {
                Text(
                    text = (user?.displayName ?: "?").take(1).uppercase(),
                    style = MaterialTheme.typography.headlineMedium,
                    color = MaterialTheme.colorScheme.onPrimaryContainer,
                    modifier = Modifier.padding(top = 18.dp),
                    textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                )
            }
            Text(
                user?.displayName ?: "Not signed in",
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.SemiBold,
            )
            user?.let {
                Text(
                    "@${it.username}",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    "Joined " + SimpleDateFormat("yyyy-MM-dd", Locale.getDefault())
                        .format(Date(it.createdAt)),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.outline,
                )
            }
        }

        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                SettingRow(
                    label = "Currency",
                    value = "${currency.symbol}  ${currency.label} (${currency.code})",
                    enabled = user != null,
                    onClick = { pickingCurrency = true },
                )
                HorizontalDivider()
                SettingRow(
                    label = "Appearance",
                    value = themeMode.label,
                    enabled = true,
                    onClick = { pickingTheme = true },
                )
                HorizontalDivider()
                // "Not set" rather than "this server's account", which the app is in no
                // position to claim: a backend run for other people has no account of its
                // own, and saying it does would be a lie told right up until planning
                // fails. Whether a fallback exists is the server's to answer, and it
                // answers it by refusing the run with a sentence pointing back here.
                SettingRow(
                    label = "AI model",
                    value = when {
                        !llmCredentials.isSet -> "Not set"
                        llmCredentials.model.isNotBlank() ->
                            "${llmCredentials.model} -- your key ****${llmCredentials.hint}"
                        else -> "Your key ****${llmCredentials.hint}"
                    },
                    enabled = true,
                    onClick = { editingLlm = true },
                )
                HorizontalDivider()
                // Says what is *lost*, not just what is missing. "Unverified" alone
                // leaves someone to find out months later that their account cannot be
                // recovered, at the moment they need it recovered.
                SettingRow(
                    label = "Email",
                    value = when {
                        user?.email.isNullOrBlank() -> "None -- password cannot be reset"
                        user?.emailVerified == true -> user.email
                        else -> "${user?.email} -- not confirmed yet"
                    },
                    enabled = true,
                    onClick = { managingEmail = true },
                )
                HorizontalDivider()
                InfoRow("Version", BuildConfig.VERSION_NAME)
            }
        }

        OutlinedButton(
            onClick = { confirmLogout = true },
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(12.dp),
        ) {
            Text("Sign out")
        }
    }

    if (pickingCurrency) {
        AlertDialog(
            onDismissRequest = { pickingCurrency = false },
            title = { Text("Currency") },
            text = {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    // Says what changing it actually does, because "convert" is the
                    // reasonable assumption and it is not what happens.
                    Text(
                        "New plans are estimated in this currency. Plans you already have are unchanged.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(bottom = 12.dp),
                    )
                    Currency.entries.forEach { option ->
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            modifier = Modifier
                                .fillMaxWidth()
                                .clickable {
                                    onCurrencyChange(option)
                                    pickingCurrency = false
                                }
                                .padding(vertical = 10.dp),
                        ) {
                            RadioButton(
                                selected = option == currency,
                                onClick = {
                                    onCurrencyChange(option)
                                    pickingCurrency = false
                                },
                            )
                            Text(
                                "${option.symbol}  ${option.label}",
                                style = MaterialTheme.typography.bodyLarge,
                                modifier = Modifier.padding(start = 4.dp),
                            )
                        }
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = { pickingCurrency = false }) { Text("Close") }
            },
        )
    }

    if (pickingTheme) {
        AlertDialog(
            onDismissRequest = { pickingTheme = false },
            title = { Text("Appearance") },
            text = {
                Column {
                    ThemeMode.entries.forEach { option ->
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            modifier = Modifier
                                .fillMaxWidth()
                                .clickable {
                                    onThemeChange(option)
                                    pickingTheme = false
                                }
                                .padding(vertical = 10.dp),
                        ) {
                            RadioButton(
                                selected = option == themeMode,
                                onClick = {
                                    onThemeChange(option)
                                    pickingTheme = false
                                },
                            )
                            Text(
                                option.label,
                                style = MaterialTheme.typography.bodyLarge,
                                modifier = Modifier.padding(start = 4.dp),
                            )
                        }
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = { pickingTheme = false }) { Text("Close") }
            },
        )
    }

    if (editingLlm) {
        LlmCredentialsDialog(
            current = llmCredentials,
            support = llmSupport,
            onDismiss = { editingLlm = false },
            onSave = {
                onLlmCredentialsChange(it)
                editingLlm = false
            },
        )
    }

    if (managingEmail) {
        EmailDialog(
            current = user?.email.orEmpty(),
            verified = user?.emailVerified == true,
            notice = authNotice,
            error = authError,
            onVerify = onVerifyEmail,
            onResend = onResendVerification,
            onChange = onChangeEmail,
            onDismiss = { managingEmail = false; onAuthMessageShown() },
        )
    }

    if (confirmLogout) {
        AlertDialog(
            onDismissRequest = { confirmLogout = false },
            title = { Text("Sign out?") },
            // Saved plans are keyed to the device, not the account, so logging out is
            // non-destructive. Saying so removes the main reason people hesitate.
            text = { Text("Your saved trips stay on this device.") },
            confirmButton = {
                TextButton(onClick = { confirmLogout = false; onLogout() }) { Text("Sign out") }
            },
            dismissButton = {
                TextButton(onClick = { confirmLogout = false }) { Text("Cancel") }
            },
        )
    }
}

/**
 * Where someone puts their own LLM account in, so the tokens are billed to them.
 *
 * Three fields, only the first required. A key alone is the case this exists for --
 * someone else's account on the same provider the backend already talks to -- so model
 * and endpoint stay blank and the backend fills in its own.
 *
 * The endpoint is offered but the backend decides whether to honour it: it is the
 * backend that would make the request, so an arbitrary address there is server-side
 * request forgery. An unlisted one comes back as a 400 rather than being silently
 * ignored, which is why this dialog does not pretend to validate it.
 */
@Composable
private fun LlmCredentialsDialog(
    current: LlmCredentials,
    support: LlmSupport,
    onDismiss: () -> Unit,
    onSave: (LlmCredentials) -> Unit,
) {
    var apiKey by remember { mutableStateOf(current.apiKey) }
    var model by remember { mutableStateOf(current.model) }
    var baseUrl by remember { mutableStateOf(current.baseUrl) }
    // Starts hidden even when the field is empty, so the default is never "shoulder
    // surfing works". Toggling is there because typing a long key blind is miserable.
    var revealed by remember { mutableStateOf(false) }
    // What the backend cannot lend, the traveller has to supply. Only asked once a key
    // is being entered: with no key the whole thing is off and nothing is required.
    val needsModel = !support.model
    val needsEndpoint = !support.endpoint
    val complete = apiKey.isBlank() ||
        ((!needsModel || model.isNotBlank()) && (!needsEndpoint || baseUrl.isNotBlank()))

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("AI model") },
        text = {
            Column(
                modifier = Modifier.verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Text(
                    "Planning runs on your own AI account, billed by your provider, on "
                        + "whatever model you name. "
                        + (
                            if (needsModel || needsEndpoint) {
                                "This backend has no AI account of its own, so it " +
                                    "needs all of these from you."
                            } else {
                                "This backend can fill in the model and endpoint, so " +
                                    "a key on its own is enough."
                            }
                        ),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                OutlinedTextField(
                    value = apiKey,
                    onValueChange = { apiKey = it },
                    singleLine = true,
                    label = { Text("API key") },
                    visualTransformation = if (revealed) {
                        VisualTransformation.None
                    } else {
                        PasswordVisualTransformation()
                    },
                    trailingIcon = {
                        TextButton(onClick = { revealed = !revealed }) {
                            Text(if (revealed) "Hide" else "Show")
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = model,
                    onValueChange = { model = it },
                    singleLine = true,
                    enabled = apiKey.isNotBlank(),
                    isError = apiKey.isNotBlank() && needsModel && model.isBlank(),
                    label = { Text(if (needsModel) "Model" else "Model (optional)") },
                    placeholder = { Text("gpt-4o") },
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = baseUrl,
                    onValueChange = { baseUrl = it },
                    singleLine = true,
                    enabled = apiKey.isNotBlank(),
                    isError = apiKey.isNotBlank() && needsEndpoint && baseUrl.isBlank(),
                    label = { Text(if (needsEndpoint) "Endpoint" else "Endpoint (optional)") },
                    placeholder = { Text("https://api.example.com/v1") },
                    modifier = Modifier.fillMaxWidth(),
                )
                Text(
                    "The well-known providers are accepted as endpoints without setup; "
                        + "anything else has to be allowed by whoever runs the backend.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        },
        confirmButton = {
            TextButton(
                // Refusing to save an incomplete account here rather than letting the
                // server refuse the first plan: the server does say which field is
                // missing, but only after someone has asked for a trip and waited.
                enabled = complete,
                onClick = {
                    onSave(
                        if (apiKey.isBlank()) {
                            LlmCredentials.NONE
                        } else {
                            LlmCredentials(apiKey.trim(), baseUrl.trim(), model.trim())
                        }
                    )
                }
            ) {
                Text(if (apiKey.isBlank()) "Use the server's" else "Save")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancel") }
        },
    )
}

/** An [InfoRow] you can tap, for the rows that are settings rather than facts. */
@Composable
private fun SettingRow(label: String, value: String, enabled: Boolean, onClick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(enabled = enabled, onClick = onClick),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        // The value takes what is left and the action keeps its own width. Without the
        // weight, a long value squeezes "Change" until it wraps to one letter per line --
        // which is what a long email address did to this row.
        Box(Modifier.weight(1f)) { InfoRow(label, value) }
        Spacer(Modifier.width(12.dp))
        Text(
            "Change",
            style = MaterialTheme.typography.labelLarge,
            maxLines = 1,
            color = if (enabled) {
                MaterialTheme.colorScheme.primary
            } else {
                MaterialTheme.colorScheme.outline
            },
        )
    }
}

@Composable
private fun InfoRow(label: String, value: String) {
    Column {
        Text(
            label,
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(value, style = MaterialTheme.typography.bodyMedium)
    }
}

/**
 * The one place an account's recoverability can be seen and fixed.
 *
 * Both jobs on one dialog -- enter the code, or change the address -- because they are the
 * same question from the user's side ("why can I not reset my password?") and the answer
 * differs only in which field they need.
 */
@Composable
private fun EmailDialog(
    current: String,
    verified: Boolean,
    notice: String?,
    error: String?,
    onVerify: (String) -> Unit,
    onResend: () -> Unit,
    onChange: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    var code by remember { mutableStateOf("") }
    var address by remember { mutableStateOf(current) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (verified) "Email" else "Confirm your email") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(
                    when {
                        current.isBlank() ->
                            "This account has no email, so a forgotten password cannot be " +
                                "reset. Add one and confirm it to make the account recoverable."
                        verified ->
                            "Confirmed. You can reset your password with this address."
                        else ->
                            "Not confirmed yet, so it cannot be used to reset your password. " +
                                "Enter the code we sent, or change the address."
                    },
                    style = MaterialTheme.typography.bodySmall,
                )

                if (current.isNotBlank() && !verified) {
                    OutlinedTextField(
                        value = code,
                        onValueChange = { code = it.uppercase() },
                        label = { Text("Code from the email") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        TextButton(onClick = { onVerify(code) }, enabled = code.isNotBlank()) {
                            Text("Confirm")
                        }
                        TextButton(onClick = onResend) { Text("Send another") }
                    }
                    HorizontalDivider()
                }

                OutlinedTextField(
                    value = address,
                    onValueChange = { address = it },
                    label = { Text("Email address") },
                    supportingText = { Text("Changing it means confirming the new one") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                TextButton(
                    onClick = { onChange(address) },
                    enabled = address.trim() != current,
                ) { Text(if (address.isBlank()) "Remove address" else "Save address") }

                error?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                }
                notice?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.primary)
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Done") } },
    )
}
