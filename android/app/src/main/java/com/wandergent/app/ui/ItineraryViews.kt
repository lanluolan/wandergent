package com.wandergent.app.ui

import android.graphics.Bitmap
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.wandergent.app.data.Activity
import com.wandergent.app.data.DayMapClient
import com.wandergent.app.data.DayPlan
import com.wandergent.app.data.Itinerary
import com.wandergent.app.data.PlanResponse
import com.wandergent.app.data.RunWarning
import com.wandergent.app.data.toolFailureText
import com.wandergent.app.data.toolLabel
import com.wandergent.app.data.ToolCallRecord
import com.wandergent.app.data.ValidationReport
import com.wandergent.app.data.formatMoney
import com.wandergent.app.data.violationLabel
import com.wandergent.app.data.warningText
import java.time.DayOfWeek
import java.time.LocalDate

// Itinerary rendering shared by the plan, saved and community screens, so a saved trip
// looks exactly like it did when it was generated.

/**
 * A finished plan, as the sequence of cards one agent reply expands into.
 *
 * A `LazyListScope` extension rather than a composable, so each day stays its own lazy
 * item: a two-week trip must not compose every day to draw the first one.
 */
internal fun LazyListScope.planItems(
    key: Any,
    response: PlanResponse,
    saved: Boolean,
    onSave: () -> Unit,
) {
    val itinerary = response.itinerary

    if (itinerary != null) {
        item(key = "$key-summary") { SummaryCard(itinerary) }
        // Nothing to show when the plan passed cleanly -- that is the normal case.
        response.validation?.let { report ->
            if (!report.ok || report.advisory.isNotEmpty()) {
                item(key = "$key-validation") { ValidationCard(report) }
            }
        }
        item(key = "$key-save") { SaveButton(saved, onSave) }
        item(key = "$key-feedback") { FeedbackButton(response) }
        itemsIndexed(itinerary.days, key = { index, _ -> "$key-day-$index" }) { index, day ->
            DayTimelineCard(day, itinerary.currency, dayNumber = index + 1)
        }
        if (itinerary.notes.isNotEmpty()) {
            item(key = "$key-notes") { NotesCard(itinerary.notes) }
        }
    } else {
        // A 200 with no itinerary: the model could not produce a valid plan.
        item(key = "$key-empty") { NoItineraryCard(response.rawReply) }
    }

    // Warnings are about the run; the validation card above is about the plan, and the
    // backend keeps the two lists disjoint. `no_itinerary` is dropped -- NoItineraryCard
    // below is that message, said properly.
    val runWarnings = response.warnings.filterNot { it.code == "no_itinerary" }
    if (runWarnings.isNotEmpty()) {
        item(key = "$key-warnings") { WarningsCard(runWarnings) }
    }
    // Only the tools that came back degraded. `search_places -- ok` says nothing to a
    // traveller; a failed lookup explains a thin-looking plan, and nothing else does.
    val degraded = response.toolCalls.filterNot { it.ok }
    if (degraded.isNotEmpty()) {
        item(key = "$key-degraded") { DegradedToolsCard(degraded) }
    }
}

/**
 * What the hard-constraint check concluded, when there is something to say.
 *
 * A clean pass draws nothing: it is the normal case, and the card cost a screenful above
 * the itinerary. Unresolved violations and pace remarks still earn it.
 */
@Composable
private fun ValidationCard(report: ValidationReport) {
    val passed = report.ok
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = if (passed) {
                MaterialTheme.colorScheme.secondaryContainer
            } else {
                MaterialTheme.colorScheme.errorContainer
            },
        ),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            if (!passed) {
                Row(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Icon(Icons.Default.Warning, contentDescription = null)
                    Text(
                        "${report.blocking.size} issue(s) unresolved",
                        style = MaterialTheme.typography.titleSmall,
                    )
                }
                report.blocking.forEach {
                    Text("· ${it.message}", style = MaterialTheme.typography.bodySmall)
                }
                Text(
                    "The automatic repair did not resolve everything. These are worth adjusting yourself.",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            // Plain text rather than a failure: the plan is sound, this is just what the
            // days look like. The full message, not the short label -- two activities can
            // share a label and would otherwise render as the same line twice.
            report.advisory.forEach {
                Text("Note: ${it.message}", style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@Composable
private fun SaveButton(saved: Boolean, onSave: () -> Unit) {
    FilledTonalButton(
        onClick = onSave,
        enabled = !saved,
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
    ) {
        Icon(
            if (saved) Icons.Default.Check else Icons.Default.Favorite,
            contentDescription = null,
        )
        Spacer(Modifier.width(8.dp))
        Text(if (saved) "Saved to your library" else "Save this trip")
    }
}

/**
 * Caveats about how the plan was produced, in the traveller's terms. The wording comes
 * from [warningText], not the wire -- the server sends only a code and its numbers.
 */
@Composable
private fun WarningsCard(warnings: List<RunWarning>) {
    // Deduplicated on the *rendered* sentence, not on the code: the three budget codes
    // collapse into one sentence, so two distinct codes can read identically.
    val lines = warnings.map(::warningText).distinct()
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            lines.forEach {
                Text("⚠ $it", style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@Composable
private fun NoItineraryCard(rawReply: String?) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("No valid itinerary", style = MaterialTheme.typography.titleMedium)
            Text(
                "What the model returned did not validate. Rephrasing usually fixes it.",
                style = MaterialTheme.typography.bodySmall,
            )
            rawReply?.let {
                Text(
                    it.take(500),
                    style = MaterialTheme.typography.bodySmall,
                    fontFamily = FontFamily.Monospace,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

/**
 * The lookups that did not answer, and nothing else. A plan built without the forecast is
 * thinner and the traveller has no other way to know; a call that worked is not news, and
 * the plan itself is the evidence.
 */
@Composable
private fun DegradedToolsCard(calls: List<ToolCallRecord>) {
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.errorContainer,
        ),
    ) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("Built without everything", style = MaterialTheme.typography.labelLarge)
            calls.forEach { call ->
                Text(
                    "${toolLabel(call.name)}: ${toolFailureText(call.code, call.error)}",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }
    }
}

@Composable
internal fun SummaryCard(itinerary: Itinerary) {
    val overBudget = itinerary.budget != null && itinerary.totalEstimatedCost > itinerary.budget

    ElevatedCard(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(itinerary.destination, style = MaterialTheme.typography.headlineMedium)
            Text(
                "${itinerary.startDate} → ${itinerary.endDate} · ${itinerary.days.size} days · ${itinerary.travelers} travellers",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            HorizontalDivider()
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Column {
                    Text("Estimated", style = MaterialTheme.typography.labelMedium)
                    Text(
                        formatMoney(itinerary.totalEstimatedCost, itinerary.currency),
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.SemiBold,
                        color = if (overBudget) {
                            MaterialTheme.colorScheme.error
                        } else {
                            MaterialTheme.colorScheme.tertiary
                        },
                    )
                }
                Column(horizontalAlignment = Alignment.End) {
                    Text("Budget", style = MaterialTheme.typography.labelMedium)
                    Text(
                        itinerary.budget?.let { formatMoney(it, itinerary.currency) } ?: "not set",
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
            }
            if (overBudget) {
                Text(
                    "Over budget. Lower the bar in your request and generate again.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }
        }
    }
}

@Composable
internal fun DayTimelineCard(day: DayPlan, currency: String, dayNumber: Int) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.Bottom,
            ) {
                Column {
                    // "Day 2" is what a traveller thinks in; the date is supporting detail.
                    Text(
                        "Day $dayNumber",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        listOfNotNull(day.date, weekdayLabel(day.date)).joinToString(" · "),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Text(
                    formatMoney(day.estimatedCost, currency),
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.tertiary,
                )
            }
            Text(day.summary, style = MaterialTheme.typography.bodyMedium)

            day.weather?.let { weather ->
                Surface(
                    color = MaterialTheme.colorScheme.secondaryContainer,
                    shape = RoundedCornerShape(8.dp),
                ) {
                    Text(
                        weather,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                    )
                }
            }

            DayMap(day, dayNumber)

            day.activities.forEachIndexed { index, activity ->
                TimelineRow(
                    activity,
                    currency = currency,
                    isLast = index == day.activities.lastIndex,
                )
            }
        }
    }
}

/**
 * The day's stops on a map, numbered in visiting order.
 *
 * Absent by design when it cannot be drawn -- no locations, no maps key, a failed request.
 * The itinerary is complete without it, so an error card would be noise about a missing
 * bonus. The still image is what the card shows: one cheap request that renders anywhere.
 * Tapping it opens the interactive version, which costs a dynamic map load.
 */
@Composable
private fun DayMap(day: DayPlan, dayNumber: Int) {
    val places = remember(day) { DayMapClient.stops(day.activities) }
    if (places.size < 2) return  // One pin is not a route; it earns no vertical space.

    var bitmap by remember(places) { mutableStateOf<Bitmap?>(null) }
    var expanded by remember(places) { mutableStateOf(false) }
    val interactiveUrl = remember(places) { DayMapClient.interactiveUrl(places) }

    LaunchedEffect(places) {
        bitmap = DayMapClient.load(places, widthPx = 640, heightPx = 360)
    }

    bitmap?.let {
        Box {
            Image(
                bitmap = it.asImageBitmap(),
                contentDescription = "Day $dayNumber trip map, ${places.size} stops in order" +
                    if (interactiveUrl != null) ", tap to enlarge" else "",
                contentScale = ContentScale.FillWidth,
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(10.dp))
                    .then(
                        if (interactiveUrl != null) {
                            Modifier.clickable { expanded = true }
                        } else {
                            Modifier
                        }
                    ),
            )
            if (interactiveUrl != null) {
                Surface(
                    color = MaterialTheme.colorScheme.surface.copy(alpha = 0.85f),
                    shape = RoundedCornerShape(topStart = 8.dp, bottomEnd = 10.dp),
                    modifier = Modifier.align(Alignment.BottomEnd),
                ) {
                    Text(
                        "Tap to enlarge",
                        style = MaterialTheme.typography.labelSmall,
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                    )
                }
            }
        }
    }

    if (expanded && interactiveUrl != null) {
        InteractiveMapDialog(
            places = places,
            url = interactiveUrl,
            title = "Day $dayNumber · ${places.size} stops",
            externalUrl = remember(places) { DayMapClient.externalRouteUrl(places) },
            onDismiss = { expanded = false },
        )
    }
}

/** One activity against a vertical rail, so a day reads as a sequence rather than a list. */
@Composable
private fun TimelineRow(activity: Activity, currency: String, isLast: Boolean) {
    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            modifier = Modifier.width(12.dp),
        ) {
            Box(
                Modifier
                    .padding(top = 6.dp)
                    .size(10.dp)
                    .background(MaterialTheme.colorScheme.primary, CircleShape),
            )
            if (!isLast) {
                Box(
                    Modifier
                        .padding(top = 2.dp)
                        .width(2.dp)
                        .height(44.dp)
                        .background(MaterialTheme.colorScheme.surfaceVariant),
                )
            }
        }

        Column(
            modifier = Modifier
                .weight(1f)
                .padding(bottom = if (isLast) 0.dp else 14.dp),
            verticalArrangement = Arrangement.spacedBy(3.dp),
        ) {
            // Time and kind on one quiet line, so the title below owns the row.
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Text(
                    "${activity.startTime} – ${activity.endTime}",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.primary,
                )
                CategoryChip(activity)
            }

            // Title and price share a line: price is the second thing anyone looks for,
            // and right-aligning it makes a day's costs scannable down the edge.
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.Top,
            ) {
                Text(
                    activity.title,
                    style = MaterialTheme.typography.bodyLarge,
                    // Takes the slack, so prices line up in a column down the edge
                    // rather than trailing each title.
                    modifier = Modifier.weight(1f),
                )
                if (activity.estimatedCost > 0) {
                    Text(
                        formatMoney(activity.estimatedCost, currency),
                        style = MaterialTheme.typography.labelLarge,
                        color = MaterialTheme.colorScheme.tertiary,
                    )
                }
            }

            // Its own line: merged into a dotted run-on it wrapped mid-word.
            activity.location?.let {
                Text(
                    it,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            // Dishes to order, exhibits worth the queue. Often empty.
            activity.highlights.forEach { highlight ->
                Text(
                    "· $highlight",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurface,
                )
            }
            activity.notes?.let {
                Text(
                    it,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

/**
 * Category and indoor/outdoor as one tinted chip. A chip rather than an icon because
 * `material-icons-core` has no glyph for restaurants, hotels or museums, and the extended
 * set is not worth the artifact size for decoration.
 */
@Composable
private fun CategoryChip(activity: Activity) {
    val label = buildString {
        append(categoryLabel(activity.category))
        // Tri-state on purpose: the backend distinguishes "outdoors" from "unknown".
        when (activity.indoor) {
            true -> append(" · indoor")
            false -> append(" · outdoor")
            null -> Unit
        }
    }
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant,
        shape = RoundedCornerShape(6.dp),
    ) {
        Text(
            label,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
        )
    }
}

/** Weekday for an ISO date, or null if the backend ever sends something else. */
private fun weekdayLabel(isoDate: String): String? = runCatching {
    when (LocalDate.parse(isoDate).dayOfWeek) {
        DayOfWeek.MONDAY -> "Mon"
        DayOfWeek.TUESDAY -> "Tue"
        DayOfWeek.WEDNESDAY -> "Wed"
        DayOfWeek.THURSDAY -> "Thu"
        DayOfWeek.FRIDAY -> "Fri"
        DayOfWeek.SATURDAY -> "Sat"
        DayOfWeek.SUNDAY -> "Sun"
    }
}.getOrNull()

/** The backend's category names, shortened where the traveller's word is shorter. */
private fun categoryLabel(category: String): String = when (category) {
    "sightseeing" -> "sights"
    "accommodation" -> "stay"
    "food", "transport", "activity", "rest" -> category
    else -> "other"
}

@Composable
internal fun NotesCard(notes: List<String>) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("Trip notes", style = MaterialTheme.typography.titleSmall)
            notes.forEach { Text("· $it", style = MaterialTheme.typography.bodySmall) }
        }
    }
}
