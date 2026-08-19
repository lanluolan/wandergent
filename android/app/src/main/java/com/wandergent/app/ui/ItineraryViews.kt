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
import com.wandergent.app.data.ToolCallRecord
import com.wandergent.app.data.ValidationReport
import com.wandergent.app.data.formatMoney
import com.wandergent.app.data.violationLabel
import com.wandergent.app.data.warningText
import java.time.DayOfWeek
import java.time.LocalDate

/**
 * Itinerary rendering shared by the plan screen and the saved-plan screen, so a saved
 * trip looks exactly like it did when it was generated.
 */

/**
 * A finished plan, as the sequence of cards one agent reply expands into.
 *
 * A `LazyListScope` extension rather than a composable so each day stays its own lazy
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
        response.validation?.let { item(key = "$key-validation") { ValidationCard(it) } }
        item(key = "$key-save") { SaveButton(saved, onSave) }
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

    // Warnings are about the run; the validation card above is about the plan. The
    // backend keeps those two lists disjoint now, so this no longer has to strike out
    // duplicates by comparing sentences -- it used to, because the same finding arrived
    // in both lists and showed up twice on a live plan.
    //
    // `no_itinerary` is dropped: NoItineraryCard below is that message, said properly.
    val runWarnings = response.warnings.filterNot { it.code == "no_itinerary" }
    if (runWarnings.isNotEmpty()) {
        item(key = "$key-warnings") { WarningsCard(runWarnings) }
    }
    if (response.toolCalls.isNotEmpty()) {
        item(key = "$key-tools") { ToolCallsCard(response.toolCalls) }
    }
}

/**
 * What the hard-constraint check concluded about the plan being shown.
 *
 * Shown on success as well as failure: "we checked the budget, the schedule and the
 * route" is the claim that separates this from a plausible-looking guess, and a plan
 * with unresolved violations must not be presented as if it were sound.
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
            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(
                    if (passed) Icons.Default.Check else Icons.Default.Warning,
                    contentDescription = null,
                )
                Text(
                    if (passed) "Checked: budget, timing, routing" else "${report.blocking.size} issue(s) unresolved",
                    style = MaterialTheme.typography.titleSmall,
                )
            }
            if (passed) {
                Text(
                    "Total is within budget, no overlapping activities, and there is time to travel between them.",
                    style = MaterialTheme.typography.bodySmall,
                )
            } else {
                report.blocking.forEach {
                    Text("· ${it.message}", style = MaterialTheme.typography.bodySmall)
                }
                Text(
                    "The automatic repair did not resolve everything. These are worth adjusting yourself.",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            // Pace remarks sit below the verdict either way, in plain text rather than as
            // failures: the plan is sound, this is just what the days look like.
            //
            // The full message, not the short label: two activities can both be "unsociably
            // early or late" and the label alone renders them as an identical line twice,
            // saying nothing about which activity or when. Observed on a live plan.
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
 * Caveats about how the plan was produced, in the traveller's terms.
 *
 * The text comes from [warningText] rather than from the wire: what the server sends is
 * a code and its numbers, which is what lets this card say "worth confirming opening
 * times" where it used to say "reached the 16-call tool budget".
 */
@Composable
private fun WarningsCard(warnings: List<RunWarning>) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            warnings.forEach {
                Text("⚠ ${warningText(it)}", style = MaterialTheme.typography.bodySmall)
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
 * The agent's tool trail. Kept visible because a thin-looking plan is usually
 * explained by a degraded tool rather than by a bad model.
 */
@Composable
private fun ToolCallsCard(calls: List<ToolCallRecord>) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("Tool calls", style = MaterialTheme.typography.labelLarge)
            calls.forEach { call ->
                val suffix = if (call.ok) "ok" else "degraded: ${call.error ?: "no reason given"}"
                Text(
                    "${call.name} — $suffix",
                    style = MaterialTheme.typography.bodySmall,
                    color = if (call.ok) {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    } else {
                        MaterialTheme.colorScheme.error
                    },
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
                    // "Day 2" is what a traveller actually thinks in; the calendar date
                    // and weekday are the supporting detail, not the headline.
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
 * Absent by design when it cannot be drawn: a day with no locations, no maps key on the
 * server, or a failed request simply shows no map. The itinerary is complete without it,
 * so an error card here would be noise about a missing bonus.
 *
 * The still image stays the thing on the card -- it is one cheap request and it renders
 * on any device. Tapping it opens the interactive version, which costs a dynamic map
 * load and depends on the WebView being new enough, so it is opt-in per day rather than
 * the default.
 */
@Composable
private fun DayMap(day: DayPlan, dayNumber: Int) {
    val places = day.activities.mapNotNull { it.location?.takeIf(String::isNotBlank) }
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
                // A screen reader gets nothing from the picture, so say what it shows:
                // which day, and how many stops are numbered on it.
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
                    // fill = true, so the price is pushed to the right edge rather than
                    // trailing the title. That is the whole point: costs line up in a
                    // column you can run your eye down.
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

            // The venue gets its own line. Merged into a dotted run-on with the category
            // and price it wrapped mid-word and read as one grey smear.
            activity.location?.let {
                Text(
                    it,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            // The specifics that make a choice worth it -- dishes to order, exhibits
            // worth the queue. Often empty, so it must collapse cleanly.
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
 * Category and indoor/outdoor as one tinted chip.
 *
 * A chip rather than an icon on purpose: the project depends on `material-icons-core`,
 * which carries ~40 common glyphs and none for restaurants, hotels or museums. Pulling
 * in `material-icons-extended` for decoration is not worth the artifact size.
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

private fun categoryLabel(category: String): String = when (category) {
    "sightseeing" -> "sights"
    "food" -> "food"
    "transport" -> "transport"
    "accommodation" -> "stay"
    "activity" -> "activity"
    "rest" -> "rest"
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
