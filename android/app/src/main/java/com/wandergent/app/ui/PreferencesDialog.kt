package com.wandergent.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.wandergent.app.data.PreferenceOutcome
import com.wandergent.app.data.PreferenceRepository
import com.wandergent.app.data.SavedPreference
import kotlinx.coroutines.launch

@Composable
fun PreferencesDialog(onDismiss: () -> Unit) {
    val repository = remember { PreferenceRepository() }
    val scope = rememberCoroutineScope()
    var preferences by remember { mutableStateOf<List<SavedPreference>>(emptyList()) }
    var busy by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var loaded by remember { mutableStateOf(false) }
    var refresh by remember { mutableStateOf(0) }

    LaunchedEffect(refresh) {
        busy = true
        error = null
        when (val result = repository.list()) {
            is PreferenceOutcome.Success -> {
                preferences = result.value
                loaded = true
            }
            is PreferenceOutcome.Failure -> error = result.message
        }
        busy = false
    }

    PreferencesContent(
        preferences = preferences,
        busy = busy,
        loaded = loaded,
        error = error,
        onRetry = { refresh++ },
        onDelete = { preference ->
            scope.launch {
                busy = true
                error = null
                when (val result = repository.delete(preference.id)) {
                    is PreferenceOutcome.Success -> {
                        preferences = preferences.filterNot { it.id == preference.id }
                    }
                    is PreferenceOutcome.Failure -> error = result.message
                }
                busy = false
            }
        },
        onDismiss = onDismiss,
    )
}

@Composable
internal fun PreferencesContent(
    preferences: List<SavedPreference>,
    busy: Boolean,
    loaded: Boolean,
    error: String?,
    onRetry: () -> Unit,
    onDelete: (SavedPreference) -> Unit,
    onDismiss: () -> Unit,
) {
    var deleting by remember { mutableStateOf<SavedPreference?>(null) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Saved preferences") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("These preferences help shape future trips. Delete anything that no longer fits.")
                if (busy) CircularProgressIndicator(Modifier.size(24.dp))
                if (error != null) {
                    Text(error, color = MaterialTheme.colorScheme.error)
                    TextButton(onClick = onRetry, enabled = !busy) { Text("Refresh") }
                }
                if (loaded && preferences.isEmpty() && !busy && error == null) {
                    Text("No saved preferences yet. Tell the planner what you enjoy on your next trip.")
                }
                LazyColumn(
                    modifier = Modifier.heightIn(max = 340.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    items(preferences, key = { it.id }) { preference ->
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                            Text(preference.text, modifier = Modifier.weight(1f))
                            TextButton(onClick = { deleting = preference }, enabled = !busy) { Text("Delete") }
                        }
                        HorizontalDivider()
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Close") } },
    )
    deleting?.let { preference ->
        AlertDialog(
            onDismissRequest = { deleting = null },
            title = { Text("Delete this preference?") },
            text = {
                Text(
                    "“${preference.text}”\n\nFuture plans will stop using this saved preference. " +
                        "Existing plans and trips already being planned will stay as they are.",
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        deleting = null
                        onDelete(preference)
                    },
                    enabled = !busy,
                ) { Text("Delete preference") }
            },
            dismissButton = { TextButton(onClick = { deleting = null }) { Text("Cancel") } },
        )
    }
}
