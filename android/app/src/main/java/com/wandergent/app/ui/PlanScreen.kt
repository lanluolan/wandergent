package com.wandergent.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.wandergent.app.data.local.SavedPlanEntity
import com.wandergent.app.data.violationLabel

/** How wide a message bubble may get, as a share of the row. */
private const val BUBBLE_MAX_WIDTH = 0.86f

/**
 * The planner, as a conversation.
 *
 * Laid out as a chat because that is what the product is -- you describe a trip in your
 * own words and the agent works in the open. The transcript keeps every attempt on
 * screen, so a request and the plan it produced stay next to each other and an earlier
 * plan is a scroll away rather than gone.
 *
 * **A follow-up edits the plan above it.** The newest itinerary rides along with the
 * next message, and the revision is validated and repaired by the same code that
 * checked the first draft -- so "swap day 2's lunch" cannot quietly put the trip over
 * budget or leave ten minutes to cross the city. That re-check is the thing a chat
 * window cannot do for you.
 */
@Composable
fun PlanScreen(
    userId: Long? = null,
    currency: String = "",
    reviseSaved: SavedPlanEntity? = null,
    onReviseConsumed: () -> Unit = {},
    viewModel: PlanViewModel = viewModel(),
) {
    // The session owns who is signed in and the settings own the currency; the view
    // model is only told, so each has one source of truth.
    LaunchedEffect(userId) { viewModel.setUser(userId) }
    viewModel.currency = currency

    // A plan handed over from the library becomes an ordinary exchange, so it can be
    // edited by exactly the same path as one generated a moment ago.
    LaunchedEffect(reviseSaved) {
        reviseSaved?.let {
            viewModel.reviseSaved(it)
            onReviseConsumed()
        }
    }
    val transcript by viewModel.transcript.collectAsStateWithLifecycle()
    var draft by rememberSaveable { mutableStateOf("") }
    val busy = transcript.any { it.state is TurnState.Running }
    val listState = rememberLazyListState()

    // Follow the conversation down as it grows, including while a reply streams in --
    // the progress lines are the point, and they are useless off-screen. Keyed on the
    // whole transcript so every progress update re-runs it.
    LaunchedEffect(transcript) {
        if (transcript.isEmpty()) return@LaunchedEffect
        withFrameNanos { }  // let the new items lay out before asking how many there are
        val last = listState.layoutInfo.totalItemsCount - 1
        if (last >= 0) listState.animateScrollToItem(last)
    }

    val send = {
        viewModel.send(draft)
        draft = ""
    }

    Column(Modifier.fillMaxSize().imePadding()) {
        if (transcript.isEmpty()) {
            EmptyState(Modifier.weight(1f))
        } else {
            LazyColumn(
                state = listState,
                modifier = Modifier.weight(1f),
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                transcript.forEach { exchange ->
                    exchangeItems(
                        exchange = exchange,
                        onSave = { viewModel.save(exchange.id) },
                        onRetry = { viewModel.retry(exchange.id) },
                    )
                }
            }
        }

        Composer(
            draft = draft,
            onDraftChange = { draft = it },
            busy = busy,
            // Only worth saying once there is a plan above that a follow-up could
            // plausibly be aimed at.
            showRevisionHint = transcript.any { it.state is TurnState.Loaded },
            onSend = send,
            onNewConversation = viewModel::startNewConversation,
        )
    }
}

/** One round: the request on the right, whatever came back on the left. */
private fun LazyListScope.exchangeItems(
    exchange: Exchange,
    onSave: () -> Unit,
    onRetry: () -> Unit,
) {
    item(key = "ask-${exchange.id}") { UserBubble(exchange.request) }

    when (val state = exchange.state) {
        is TurnState.Running -> item(key = "run-${exchange.id}") { ProgressBubble(state.progress) }
        is TurnState.Error -> item(key = "err-${exchange.id}") { ErrorBubble(state, onRetry) }
        is TurnState.Loaded -> {
            // A revised plan is a full itinerary again, so without a label the reader
            // cannot tell "I edited your trip" from "here is a different trip".
            if (exchange.revision) {
                item(key = "rev-${exchange.id}") { RevisionLabel() }
            }
            planItems(
                key = exchange.id,
                response = state.response,
                saved = exchange.saved,
                onSave = onSave,
            )
        }
    }
}

/** Marks a plan as an edit of the one above rather than a replacement for it. */
@Composable
private fun RevisionLabel() {
    Row(
        Modifier.fillMaxWidth().padding(top = 2.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Icon(
            Icons.Default.Refresh,
            contentDescription = null,
            modifier = Modifier.size(14.dp),
            tint = MaterialTheme.colorScheme.primary,
        )
        Text(
            "Edited the plan above, then re-checked",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.primary,
        )
    }
}

@Composable
private fun UserBubble(text: String) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
        Spacer(Modifier.weight(1f - BUBBLE_MAX_WIDTH))
        Surface(
            // `fill = false` caps the bubble without stretching a short message to it.
            modifier = Modifier.weight(BUBBLE_MAX_WIDTH, fill = false),
            // Solid brand teal, not `primaryContainer`: that role is a near-neon cyan in
            // this palette and a bubble of it on every message shouts over the plan,
            // which is the part worth looking at. Matches the send button.
            color = MaterialTheme.colorScheme.primary,
            contentColor = MaterialTheme.colorScheme.onPrimary,
            shape = RoundedCornerShape(18.dp, 18.dp, 4.dp, 18.dp),
        ) {
            Text(
                text,
                modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                style = MaterialTheme.typography.bodyMedium,
            )
        }
    }
}

/** The agent's side of a bubble: left-aligned, with the mirrored corner. */
@Composable
private fun AgentBubble(
    color: Color = MaterialTheme.colorScheme.surfaceContainerHigh,
    contentColor: Color = MaterialTheme.colorScheme.onSurface,
    content: @Composable () -> Unit,
) {
    Row(Modifier.fillMaxWidth()) {
        Surface(
            modifier = Modifier.weight(BUBBLE_MAX_WIDTH, fill = false),
            color = color,
            contentColor = contentColor,
            shape = RoundedCornerShape(18.dp, 18.dp, 18.dp, 4.dp),
        ) {
            Box(Modifier.padding(14.dp)) { content() }
        }
        Spacer(Modifier.weight(1f - BUBBLE_MAX_WIDTH))
    }
}

/**
 * Live progress, driven entirely by events from the server.
 *
 * This replaced a timer that cycled through plausible-sounding stages. Every line here
 * corresponds to something that actually happened: a stage the agent entered, a tool it
 * called and how that came back, and the names of places as they are written into the
 * itinerary.
 */
@Composable
private fun ProgressBubble(progress: LiveProgress) {
    AgentBubble {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(
                horizontalArrangement = Arrangement.spacedBy(10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                CircularProgressIndicator(modifier = Modifier.size(16.dp), strokeWidth = 2.dp)
                Text(
                    progress.stage ?: "Working",
                    style = MaterialTheme.typography.titleSmall,
                )
            }

            LinearProgressIndicator(Modifier.fillMaxWidth())

            progress.tools.forEach { ToolProgressRow(it) }

            progress.violations?.let { violations ->
                HorizontalDivider()
                if (violations.isEmpty()) {
                    Text(
                        "Budget, timing and routing all check out",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.primary,
                    )
                } else {
                    Text(
                        "Found ${violations.size} problem(s), fixing",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.error,
                    )
                    violations.take(3).forEach {
                        Text(
                            "· ${violationLabel(it.code)}",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }

            if (progress.writing.isNotEmpty()) {
                HorizontalDivider()
                Text(
                    "Scheduling",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                progress.writing.forEach {
                    Text("· $it", style = MaterialTheme.typography.bodyMedium)
                }
            }
        }
    }
}

@Composable
private fun ToolProgressRow(tool: ToolProgress) {
    val label = when (tool.name) {
        "get_weather_forecast" -> tool.subject?.let { "Checking the forecast for $it" } ?: "Checking the forecast"
        "remember_preference" -> "Remembering your preference"
        "search_places" -> "Finding places to go"
        "get_travel_time" -> "Checking travel times"
        else -> tool.name
    }
    val (status, color) = when (tool.ok) {
        null -> "running" to MaterialTheme.colorScheme.onSurfaceVariant
        true -> "done" to MaterialTheme.colorScheme.primary
        false -> (tool.error ?: "degraded") to MaterialTheme.colorScheme.error
    }

    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(label, style = MaterialTheme.typography.bodyMedium)
        Text(status, style = MaterialTheme.typography.bodySmall, color = color)
    }
}

@Composable
private fun ErrorBubble(state: TurnState.Error, onRetry: () -> Unit) {
    AgentBubble(
        color = MaterialTheme.colorScheme.errorContainer,
        contentColor = MaterialTheme.colorScheme.onErrorContainer,
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(Icons.Default.Warning, contentDescription = null)
                Text("Could not generate a plan", style = MaterialTheme.typography.titleSmall)
            }
            Text(state.message, style = MaterialTheme.typography.bodyMedium)
            // Only retryable failures get a button: a misconfigured server (HTTP 500)
            // will fail identically no matter how many times it is tapped.
            if (state.retryable) {
                TextButton(onClick = onRetry, contentPadding = PaddingValues(0.dp)) {
                    Icon(Icons.Default.Refresh, contentDescription = null)
                    Spacer(Modifier.width(8.dp))
                    Text("Retry")
                }
            }
        }
    }
}

/**
 * The landing screen.
 *
 * The copy carries the shape a good request has -- city, days, budget, preference --
 * because that is the only guidance left now that the example chips are gone.
 */
@Composable
private fun EmptyState(modifier: Modifier = Modifier) {
    Column(
        modifier = modifier.fillMaxWidth().padding(horizontal = 24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            "Where to?",
            style = MaterialTheme.typography.headlineMedium,
            fontWeight = FontWeight.SemiBold,
        )
        Spacer(Modifier.size(8.dp))
        Text(
            "Say the city, how long, your budget and what you like, in one sentence.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
        )
    }
}

/** The pinned input row. Sits on the keyboard, the way a chat composer should. */
@Composable
private fun Composer(
    draft: String,
    onDraftChange: (String) -> Unit,
    busy: Boolean,
    showRevisionHint: Boolean,
    onSend: () -> Unit,
    onNewConversation: () -> Unit,
) {
    // The page colour, not a tinted slab. `tonalElevation` shaded this block towards the
    // brand teal and the navigation bar shaded itself differently again, which put three
    // competing bands across the bottom of the screen. Painting composer and tabs in the
    // page colour collapses all of it: one continuous surface, one hairline where the
    // content stops, and the input pill as the only tinted thing -- so the eye goes to
    // the one element that is actually interactive.
    Surface(color = MaterialTheme.colorScheme.surface) {
        Column {
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            Column(Modifier.padding(horizontal = 12.dp, vertical = 8.dp)) {
            if (showRevisionHint) {
                // The hint and its escape hatch belong together: the moment someone
                // reads "this will edit the plan above", the next thing they need is
                // the way to not do that.
                Row(
                    modifier = Modifier.fillMaxWidth().padding(start = 12.dp, bottom = 2.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Text(
                        "The next message edits the plan above, then re-checks budget and timing",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.weight(1f, fill = false),
                    )
                    TextButton(
                        onClick = onNewConversation,
                        enabled = !busy,
                        contentPadding = PaddingValues(horizontal = 8.dp),
                    ) {
                        Text("New chat", style = MaterialTheme.typography.labelSmall)
                    }
                }
            }
            Row(
                verticalAlignment = Alignment.Bottom,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                // A hand-rolled pill rather than `TextField`, for height. The Material
                // field reserves room for a label it will never have, so an empty
                // composer showed as a 56dp blank slab -- the largest, emptiest thing on
                // the screen. `BasicTextField` in a Surface is the same behaviour at the
                // size the content actually needs.
                Surface(
                    modifier = Modifier.weight(1f),
                    shape = RoundedCornerShape(22.dp),
                    color = MaterialTheme.colorScheme.surfaceContainerHigh,
                ) {
                    BasicTextField(
                        value = draft,
                        onValueChange = onDraftChange,
                        enabled = !busy,
                        maxLines = 5,
                        textStyle = MaterialTheme.typography.bodyLarge.copy(
                            color = MaterialTheme.colorScheme.onSurface,
                        ),
                        cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                        keyboardActions = KeyboardActions(onSend = { onSend() }),
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 18.dp, vertical = 13.dp),
                    )
                }
                FilledIconButton(
                    onClick = onSend,
                    enabled = !busy && draft.isNotBlank(),
                    modifier = Modifier.size(48.dp),
                    shape = CircleShape,
                ) {
                    if (busy) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(18.dp),
                            strokeWidth = 2.dp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    } else {
                        Icon(Icons.Default.KeyboardArrowUp, contentDescription = "Send")
                    }
                }
            }
            }
        }
    }
}
