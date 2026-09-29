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
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material3.Card
import androidx.compose.material3.Icon
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.wandergent.app.BuildConfig
import com.wandergent.app.data.Currency
import com.wandergent.app.data.ThemeMode
import com.wandergent.app.data.local.UserEntity
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@Composable
fun ProfileScreen(
    user: UserEntity?,
    currency: Currency,
    onCurrencyChange: (Currency) -> Unit,
    themeMode: ThemeMode,
    onThemeChange: (ThemeMode) -> Unit,
    onLogout: () -> Unit,
) {
    var confirmLogout by remember { mutableStateOf(false) }
    var pickingCurrency by remember { mutableStateOf(false) }
    var pickingTheme by remember { mutableStateOf(false) }
    var managingPreferences by remember(user?.id) { mutableStateOf(false) }

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
                    textAlign = TextAlign.Center,
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
                InfoRow("Version", BuildConfig.VERSION_NAME)
                HorizontalDivider()
                SettingRow(
                    label = "Saved preferences",
                    value = "View and delete",
                    enabled = user?.serverAccountId != null,
                    onClick = { managingPreferences = true },
                )
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

    if (managingPreferences && user != null) {
        key(user.id) {
            PreferencesDialog(onDismiss = { managingPreferences = false })
        }
    }

    if (pickingCurrency) {
        AlertDialog(
            onDismissRequest = { pickingCurrency = false },
            title = { Text("Currency") },
            text = {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    // "Convert" is the reasonable assumption, and not what happens.
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
        // The value takes what is left; the chevron keeps its own width so a long value
        // cannot squeeze it.
        Box(Modifier.weight(1f)) { InfoRow(label, value) }
        Spacer(Modifier.width(12.dp))
        Icon(
            Icons.AutoMirrored.Filled.KeyboardArrowRight,
            // Decorative: the row is the control, so naming the arrow would make a
            // screen reader read every row twice.
            contentDescription = null,
            modifier = Modifier.size(24.dp),
            tint = if (enabled) {
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
