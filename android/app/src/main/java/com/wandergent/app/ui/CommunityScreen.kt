package com.wandergent.app.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.filled.FavoriteBorder
import androidx.compose.material.icons.filled.Clear
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.wandergent.app.data.SharedPlanCard
import com.wandergent.app.data.formatMoney

/**
 * Trips other people shared, and the ones you shared back.
 *
 * Sharing happens from the library rather than here -- you share a trip you have, and
 * that is where your trips live. This tab is for reading.
 *
 * **Nothing on this feed is authenticated or moderated**; see `app/community/store.py`.
 * The banner says so rather than leaving it to be discovered.
 */
@Composable
fun CommunityScreen(
    userId: Long?,
    accountId: String?,
    viewModel: CommunityViewModel = viewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val opened by viewModel.opened.collectAsStateWithLifecycle()
    val message by viewModel.message.collectAsStateWithLifecycle()
    val query by viewModel.query.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }

    // Reload every time the tab is entered, not only when the reader changes. The
    // destination leaves composition on a tab switch, so this fires again on return.
    LaunchedEffect(userId, accountId) {
        viewModel.setUser(userId, accountId)
        viewModel.refresh()
    }
    LaunchedEffect(message) {
        message?.let {
            snackbar.showSnackbar(it)
            viewModel.clearMessage()
        }
    }

    Scaffold(snackbarHost = { SnackbarHost(snackbar) }) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            Row(
                Modifier.fillMaxWidth().padding(start = 16.dp, top = 12.dp, end = 8.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text("Community", style = MaterialTheme.typography.headlineSmall)
                    Text(
                        "Trips other travellers shared. Posting needs an account; nothing here is moderated.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                IconButton(onClick = viewModel::refresh) {
                    Icon(Icons.Default.Refresh, contentDescription = "Refresh")
                }
            }

            OutlinedTextField(
                value = query,
                onValueChange = viewModel::search,
                label = { Text("Search by destination") },
                singleLine = true,
                leadingIcon = { Icon(Icons.Default.Search, contentDescription = null) },
                trailingIcon = {
                    if (query.isNotEmpty()) {
                        IconButton(onClick = { viewModel.search("") }) {
                            Icon(Icons.Default.Clear, contentDescription = "Clear search")
                        }
                    }
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
            )

            state.error?.let {
                Card(
                    Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.errorContainer,
                    ),
                ) {
                    Text(it, Modifier.padding(12.dp), style = MaterialTheme.typography.bodySmall)
                }
            }

            when {
                state.loading && state.cards.isEmpty() ->
                    Column(
                        Modifier.fillMaxSize(),
                        verticalArrangement = Arrangement.Center,
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) { CircularProgressIndicator() }

                state.loaded && state.cards.isEmpty() && state.error == null ->
                    Column(
                        Modifier.fillMaxSize().padding(32.dp),
                        verticalArrangement = Arrangement.Center,
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        // "Nothing shared yet" under an active search would be a lie, and
                        // the fix for the two cases is different: post something, or
                        // search for something else.
                        val searching = query.isNotBlank()
                        Text(
                            if (searching) "No trips to “$query”" else "Nothing shared yet",
                            style = MaterialTheme.typography.titleMedium,
                        )
                        Text(
                            if (searching) {
                                "Try a different place, or clear the search."
                            } else {
                                "Open a trip in Saved and tap Share to put it here."
                            },
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }

                else -> LazyColumn(
                    Modifier.fillMaxSize(),
                    contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    items(state.cards, key = { it.id }) { card ->
                        SharedPlanRow(
                            card = card,
                            detail = opened[card.id],
                            mine = viewModel.isMine(card),
                            onOpen = { viewModel.open(card.id) },
                            onToggleSave = { viewModel.toggleSave(card) },
                            onWithdraw = { viewModel.withdraw(card) },
                        )
                    }

                    if (state.hasMore) {
                        item(key = "load-more") {
                            // Composing this row *is* the trigger: it only enters
                            // composition when the reader has scrolled to the end, which
                            // is exactly when the next page is wanted. The view model
                            // guards against the repeat calls that scrolling produces.
                            LaunchedEffect(state.nextCursor) { viewModel.loadMore() }
                            Row(
                                Modifier.fillMaxWidth().padding(16.dp),
                                horizontalArrangement = Arrangement.Center,
                            ) {
                                CircularProgressIndicator(Modifier.width(24.dp))
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun SharedPlanRow(
    card: SharedPlanCard,
    detail: com.wandergent.app.data.SharedPlanDetail?,
    mine: Boolean,
    onOpen: () -> Unit,
    onToggleSave: () -> Unit,
    onWithdraw: () -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }

    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth().clickable {
                    expanded = !expanded
                    if (expanded) onOpen()
                },
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text(card.destination, style = MaterialTheme.typography.titleLarge)
                    Text(
                        "${card.startDate} → ${card.endDate} · ${card.dayCount} days · " +
                            formatMoney(card.totalCost, card.currency),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Text(
                        if (mine) "Shared by you" else "Shared by ${card.authorName}",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.outline,
                    )
                }
                // Your own post gets a way down, not a way to save it to the library it
                // is already in.
                if (mine) {
                    IconButton(onClick = onWithdraw) {
                        Icon(Icons.Default.Delete, contentDescription = "Take down")
                    }
                } else {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        if (card.saveCount > 0) {
                            Text(
                                "${card.saveCount}",
                                style = MaterialTheme.typography.labelMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        IconButton(onClick = onToggleSave) {
                            // savedByViewer is tri-state: null means nobody told the
                            // server who is asking, so the outline is the honest icon.
                            Icon(
                                if (card.savedByViewer == true) {
                                    Icons.Default.Favorite
                                } else {
                                    Icons.Default.FavoriteBorder
                                },
                                contentDescription =
                                    if (card.savedByViewer == true) "Remove from library" else "Save",
                            )
                        }
                    }
                }
            }

            if (card.note.isNotBlank()) {
                Text(card.note, style = MaterialTheme.typography.bodyMedium)
            }

            AnimatedVisibility(expanded) {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    HorizontalDivider()
                    if (card.request.isNotBlank()) {
                        Text(
                            "Original request: ${card.request}",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 3,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                    val itinerary = detail?.itinerary
                    if (itinerary == null) {
                        // The card carries no itinerary; it is fetched when opened.
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            CircularProgressIndicator(Modifier.width(18.dp))
                            Spacer(Modifier.width(10.dp))
                            Text("Loading the itinerary…", style = MaterialTheme.typography.bodySmall)
                        }
                    } else {
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
