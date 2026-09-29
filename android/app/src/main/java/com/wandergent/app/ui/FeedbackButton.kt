package com.wandergent.app.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.wandergent.app.data.FeedbackRequest
import com.wandergent.app.data.Network
import com.wandergent.app.data.PlanResponse
import java.io.IOException
import kotlinx.coroutines.launch
import retrofit2.HttpException

@Composable
internal fun FeedbackButton(response: PlanResponse) {
    val runId = response.runId ?: return
    if (!response.feedbackAvailable) return
    var open by remember(runId) { mutableStateOf(false) }
    var selected by remember(runId) { mutableStateOf<Pair<Int, Int>?>(null) }
    var helpful by remember(runId) { mutableStateOf(true) }
    var category by remember(runId) { mutableStateOf("other") }
    var sending by remember(runId) { mutableStateOf(false) }
    var message by remember(runId) { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    Column {
        TextButton(onClick = { open = true; message = null }) { Text("Give feedback") }
        message?.let { Text(it) }
    }
    if (open) AlertDialog(
        onDismissRequest = { if (!sending) open = false },
        title = { Text("How did this plan work for you?") },
        text = {
            Column(Modifier.heightIn(max = 360.dp).verticalScroll(rememberScrollState())) {
                Text("Choose the whole trip or one activity.")
                TextButton(onClick = { selected = null }, enabled = !sending) {
                    Text(if (selected == null) "✓ Whole trip" else "Whole trip")
                }
                response.itinerary?.days?.forEachIndexed { day, plan ->
                    plan.activities.forEachIndexed { activity, item ->
                        TextButton(onClick = { selected = day to activity }, enabled = !sending) {
                            Text("${if (selected == day to activity) "✓ " else ""}Day ${day + 1}: ${item.title}")
                        }
                    }
                }
                TextButton(onClick = { helpful = true }, enabled = !sending) {
                    Text(if (helpful) "✓ Helpful" else "Helpful")
                }
                TextButton(onClick = { helpful = false }, enabled = !sending) {
                    Text(if (!helpful) "✓ Needs improvement" else "Needs improvement")
                }
                if (!helpful) listOf(
                    "model_error" to "The plan misunderstood my request",
                    "tool_error" to "Travel information was unavailable",
                    "stale_data" to "Information was out of date",
                    "validator_miss" to "The plan missed a constraint",
                    "client_error" to "The app displayed something incorrectly",
                    "other" to "Another issue",
                ).forEach { (code, label) ->
                    TextButton(onClick = { category = code }, enabled = !sending) {
                        Text("${if (category == code) "✓ " else ""}$label")
                    }
                }
                Text("We retain timing, cost and issue codes for 30 days. Your message and place names are not included.")
                message?.let { Text(it) }
            }
        },
        confirmButton = {
            Button(enabled = !sending, onClick = {
                sending = true
                scope.launch {
                    try {
                        Network.feedbackApi.submit(FeedbackRequest(
                            runId, helpful, if (helpful) "other" else category,
                            selected?.first, selected?.second,
                        ))
                        message = "Thank you. Your feedback was saved."
                        open = false
                    } catch (e: HttpException) {
                        message = if (e.code() == 404) "This plan is no longer available for feedback."
                                  else "Could not save feedback. Please try again."
                    } catch (e: IOException) {
                        message = "Could not connect. Please try again."
                    } finally {
                        sending = false
                    }
                }
            }) { Text(if (sending) "Sending…" else "Send feedback") }
        },
        dismissButton = { TextButton(onClick = { open = false }, enabled = !sending) { Text("Cancel") } },
    )
}
