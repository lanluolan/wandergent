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
import androidx.compose.foundation.text.KeyboardOptions
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
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.wandergent.app.BuildConfig
import com.wandergent.app.data.Currency
import com.wandergent.app.data.ServerConfig
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
    serverUrl: String,
    onServerUrlChange: (String) -> Unit,
    onServerUrlReset: () -> Unit,
    onLogout: () -> Unit,
) {
    var confirmLogout by remember { mutableStateOf(false) }
    var pickingCurrency by remember { mutableStateOf(false) }
    var pickingTheme by remember { mutableStateOf(false) }
    var editingServer by remember { mutableStateOf(false) }
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
                SettingRow(
                    label = "Backend address",
                    value = serverUrl,
                    enabled = true,
                    onClick = { editingServer = true },
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
                InfoRow("Saved trips", "Stored on this device, per account. Uninstalling removes them.")
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

    if (editingServer) {
        ServerUrlDialog(
            current = serverUrl,
            onDismiss = { editingServer = false },
            onSave = {
                onServerUrlChange(it)
                editingServer = false
            },
            onReset = {
                onServerUrlReset()
                editingServer = false
            },
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
 * Where the backend lives.
 *
 * A dev-facing setting, so it says which address goes with which setup rather than
 * making the reader remember that `10.0.2.2` is the emulator's word for the host.
 */
@Composable
private fun ServerUrlDialog(
    current: String,
    onDismiss: () -> Unit,
    onSave: (String) -> Unit,
    onReset: () -> Unit,
) {
    var text by remember { mutableStateOf(current) }
    val valid = ServerConfig.isValid(text)

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Backend address") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(
                    value = text,
                    onValueChange = { text = it },
                    singleLine = true,
                    isError = !valid,
                    label = { Text("Address") },
                    placeholder = { Text(ServerConfig.default) },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                    modifier = Modifier.fillMaxWidth(),
                )
                Text(
                    "USB debugging: ${ServerConfig.default} (run adb reverse tcp:8000 tcp:8000 first)\n"
                        + "Emulator: http://10.0.2.2:8000\n"
                        + "Same Wi-Fi: http://<your computer's LAN IP>:8000 (backend needs --host 0.0.0.0)",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (!valid) {
                    Text(
                        "That is not a usable address. You can leave out http:// -- it will be added.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            }
        },
        confirmButton = {
            TextButton(onClick = { onSave(text) }, enabled = valid) { Text("Save") }
        },
        dismissButton = {
            Row {
                TextButton(onClick = onReset) { Text("Reset") }
                TextButton(onClick = onDismiss) { Text("Cancel") }
            }
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
