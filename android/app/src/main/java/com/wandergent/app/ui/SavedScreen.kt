package com.wandergent.app.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Share
import androidx.compose.material.icons.filled.FavoriteBorder
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.wandergent.app.data.formatMoney
import com.wandergent.app.data.local.SavedPlanEntity
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@Composable
fun SavedScreen(
    userId: Long? = null,
    onRevise: (SavedPlanEntity) -> Unit = {},
    /**
     * Publish a trip to the community feed, with whatever the traveller wrote about it.
     *
     * Hoisted rather than handled here so the tab that *shows* the feed and the tab that
     * *adds* to it work through one view model. Two instances would leave a trip shared
     * from here missing from the feed until it happened to reload.
     */
    onShare: (SavedPlanEntity, String, Boolean) -> Unit = { _, _, _ -> },
    /**
     * How the share went, to be shown here.
     *
     * Sharing starts on this screen but is carried out by the community view model, whose
     * own snackbar lives on the Community tab -- so a refused publish said nothing at all
     * here and then appeared, out of context, the next time that tab was opened. The
     * result has to surface where the tap happened.
     */
    shareMessage: String? = null,
    onShareMessageShown: () -> Unit = {},
    viewModel: SavedViewModel = viewModel(),
) {
    // The session owns who is signed in; the view model is told, the same way the
    // planner is. Also claims any plans saved before the library was per-account.
    LaunchedEffect(userId) { viewModel.setUser(userId) }

    val plans by viewModel.plans.collectAsStateWithLifecycle()
    val undo by viewModel.undo.collectAsStateWithLifecycle()
    val snackbars = remember { SnackbarHostState() }
    var confirming by remember { mutableStateOf<SavedPlanEntity?>(null) }
    var sharing by remember { mutableStateOf<SavedPlanEntity?>(null) }

    LaunchedEffect(shareMessage) {
        shareMessage?.let {
            snackbars.showSnackbar(it)
            onShareMessageShown()
        }
    }

    // The offer lives exactly as long as the snackbar does, so "Undo" is never a button
    // for a deletion the view model has already forgotten.
    LaunchedEffect(undo) {
        val pending = undo ?: return@LaunchedEffect
        val result = snackbars.showSnackbar(
            message = "Deleted ${pending.plan.destination}",
            actionLabel = "Undo",
            duration = SnackbarDuration.Short,
        )
        if (result == SnackbarResult.ActionPerformed) viewModel.undoDelete() else viewModel.clearUndo()
    }

    Scaffold(snackbarHost = { SnackbarHost(snackbars) }) { padding ->
        if (plans.isEmpty()) {
            EmptySaved(Modifier.padding(padding))
        } else {
            LazyColumn(
                modifier = Modifier.fillMaxSize().padding(padding),
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                items(plans, key = { it.id }) { plan ->
                    SavedPlanCard(
                        plan = plan,
                        onDelete = { confirming = plan },
                        onRevise = { onRevise(plan) },
                        onShare = { sharing = plan },
                        decode = { viewModel.decode(plan) },
                    )
                }
            }
        }
    }

    confirming?.let { plan ->
        AlertDialog(
            onDismissRequest = { confirming = null },
            title = { Text("Delete this trip?") },
            text = { Text("${plan.destination} will be removed from your library. You can undo this once.") },
            confirmButton = {
                TextButton(
                    onClick = {
                        viewModel.delete(plan)
                        confirming = null
                    },
                ) {
                    Text("Delete", color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = {
                TextButton(onClick = { confirming = null }) { Text("Cancel") }
            },
        )
    }

    // Sharing is public and one-way from the traveller's side -- there is no unshare
    // from here, only from the feed -- so it asks first rather than firing on the tap.
    sharing?.let { plan ->
        var note by remember(plan.id) { mutableStateOf("") }
        // Off by default. The original request is where private context ends up, and it
        // was previously published with no mention of it anywhere.
        var includeRequest by remember(plan.id) { mutableStateOf(false) }
        AlertDialog(
            onDismissRequest = { sharing = null },
            title = { Text("Share this trip?") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(
                        "${plan.destination} will be visible to everyone using this " +
                            "server, with your display name on it. You can take it down " +
                            "again from the Community tab.",
                        style = MaterialTheme.typography.bodySmall,
                    )
                    OutlinedTextField(
                        value = note,
                        onValueChange = { if (it.length <= 240) note = it },
                        label = { Text("Say something about it (optional)") },
                        singleLine = false,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    if (plan.request.isNotBlank()) {
                        HorizontalDivider()
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            modifier = Modifier
                                .fillMaxWidth()
                                .clickable { includeRequest = !includeRequest },
                        ) {
                            Checkbox(checked = includeRequest, onCheckedChange = { includeRequest = it })
                            Text(
                                "Also share what I asked for",
                                style = MaterialTheme.typography.bodyMedium,
                            )
                        }
                        // Shown, not summarised: the traveller has to be able to see the
                        // exact words before deciding, because only they know what is in
                        // them. A description of the field is not consent.
                        Text(
                            "“${plan.request}”",
                            style = MaterialTheme.typography.bodySmall,
                            color = if (includeRequest) {
                                MaterialTheme.colorScheme.onSurface
                            } else {
                                MaterialTheme.colorScheme.outline
                            },
                        )
                    }
                }
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        onShare(plan, note, includeRequest)
                        sharing = null
                    },
                ) { Text("Share") }
            },
            dismissButton = {
                TextButton(onClick = { sharing = null }) { Text("Cancel") }
            },
        )
    }
}

@Composable
private fun EmptySaved(modifier: Modifier = Modifier) {
    Column(
        modifier = modifier.fillMaxSize().padding(32.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp, Alignment.CenterVertically),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Icon(
            Icons.Default.FavoriteBorder,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.outline,
        )
        Text("No saved trips yet", style = MaterialTheme.typography.titleMedium)
        Text(
            "Save a plan after generating it.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
        )
    }
}

/**
 * Collapsed card that expands in place into the full itinerary.
 *
 * The plan is decoded lazily, only once expanded: the list must stay cheap to scroll
 * even with a lot of saved trips.
 */
@Composable
private fun SavedPlanCard(
    plan: SavedPlanEntity,
    onDelete: () -> Unit,
    onRevise: () -> Unit,
    onShare: () -> Unit,
    decode: () -> com.wandergent.app.data.PlanResponse?,
) {
    var expanded by remember { mutableStateOf(false) }

    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth().clickable { expanded = !expanded },
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text(plan.destination, style = MaterialTheme.typography.titleLarge)
                    Text(
                        "${plan.startDate} → ${plan.endDate} · ${plan.dayCount} days · " +
                            formatMoney(plan.totalCost, plan.currency),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Text(
                        formatSavedAt(plan.createdAt),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.outline,
                    )
                }
                IconButton(onClick = onShare) {
                    Icon(Icons.Default.Share, contentDescription = "Share to community")
                }
                IconButton(onClick = onDelete) {
                    Icon(Icons.Default.Delete, contentDescription = "Delete")
                }
            }

            AnimatedVisibility(expanded) {
                val response = remember(plan.id) { decode() }
                val itinerary = response?.itinerary
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    HorizontalDivider()
                    Text(
                        "Original request: ${plan.request}",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    if (itinerary == null) {
                        Text("This entry could not be read. It may have been saved by an older version.")
                    } else {
                        // Editing a saved trip is the point of saving one. Without this
                        // a plan could only be changed while it happened to still be in
                        // the planning tab's transcript -- that is, until the next
                        // restart.
                        FilledTonalButton(
                            onClick = onRevise,
                            modifier = Modifier.fillMaxWidth(),
                            shape = RoundedCornerShape(12.dp),
                        ) {
                            Icon(Icons.Default.Edit, contentDescription = null)
                            Spacer(Modifier.width(8.dp))
                            Text("Continue editing this trip")
                        }
                        itinerary.days.forEachIndexed { index, day ->
                            DayTimelineCard(day, itinerary.currency, dayNumber = index + 1)
                        }
                        if (itinerary.notes.isNotEmpty()) NotesCard(itinerary.notes)
                    }
                }
            }
        }
    }
}

private fun formatSavedAt(epochMillis: Long): String =
    "Saved " + SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.getDefault()).format(Date(epochMillis))
