package com.wandergent.app.ui

import android.graphics.Bitmap
import android.graphics.Color
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.pinch
import androidx.compose.ui.test.swipe
import com.wandergent.app.data.SavedPreference
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/** Isolated composables only: no app accounts, Room stores, preferences or external APIs. */
class ExperienceTest {
    @get:Rule val compose = createComposeRule()

    @Test fun deletion_requires_confirmation_and_cancel_keeps_the_preference() {
        var deleted: SavedPreference? = null
        val preference = SavedPreference("id", "enjoys museums", "2026-09-06T12:00:00Z")
        compose.setContent {
            MaterialTheme {
                PreferencesContent(listOf(preference), false, true, null, {}, { deleted = it }, {})
            }
        }
        compose.onNodeWithText("Delete").performClick()
        assertEquals(null, deleted)
        compose.onNodeWithText("Cancel").performClick()
        compose.onNodeWithText("enjoys museums").assertIsDisplayed()
        compose.onNodeWithText("Delete").performClick()
        compose.onNodeWithText("Delete preference").performClick()
        compose.runOnIdle { assertEquals(preference, deleted) }
    }

    @Test fun unavailable_preferences_are_not_presented_as_empty() {
        var refreshed = false
        compose.setContent {
            MaterialTheme {
                PreferencesContent(emptyList(), false, false, "Connection failed", { refreshed = true }, {}, {})
            }
        }
        compose.onNodeWithText("Connection failed").assertIsDisplayed()
        compose.onNodeWithText("Refresh").performClick()
        compose.runOnIdle { assertEquals(true, refreshed) }
    }

    @Test fun failed_static_map_can_retry_then_zoom_and_reset() {
        var calls = 0
        val image = Bitmap.createBitmap(256, 256, Bitmap.Config.ARGB_8888).apply {
            for (x in 0 until width) for (y in 0 until height) {
                setPixel(x, y, if (x < width / 2) Color.CYAN else Color.RED)
            }
        }
        compose.setContent {
            MaterialTheme {
                ZoomableStaticMap(listOf("Los Angeles")) { if (++calls == 1) null else image }
            }
        }
        compose.onNodeWithText("Could not load this map.").assertIsDisplayed()
        compose.onNodeWithText("Retry map").performClick()
        compose.onNodeWithText("Zoom out").assertIsNotEnabled()
        compose.onNodeWithText("Zoom in").performClick()
        assertEquals("150% zoom", zoom())
        compose.onNodeWithText("Reset map").performClick()
        assertEquals("100% zoom", zoom())
        compose.onNodeWithContentDescription("Trip map").performTouchInput {
            pinch(
                center + Offset(-30f, 0f), center + Offset(30f, 0f),
                center + Offset(-120f, 0f), center + Offset(120f, 0f),
            )
        }
        assertNotEquals("100% zoom", zoom())
        val beforePan = compose.onNodeWithContentDescription("Trip map").captureToImage().asAndroidBitmap()
        compose.onNodeWithContentDescription("Trip map").performTouchInput {
            swipe(center, center + Offset(160f, 0f))
        }
        val afterPan = compose.onNodeWithContentDescription("Trip map").captureToImage().asAndroidBitmap()
        assertTrue("Panning must change the visible map", !beforePan.sameAs(afterPan))
        compose.onNodeWithText("Reset map").performClick()
        assertEquals("100% zoom", zoom())
    }

    private fun zoom(): String = compose.onNodeWithContentDescription("Trip map")
        .fetchSemanticsNode().config[SemanticsProperties.StateDescription]

    @Test fun unavailable_browser_falls_back_to_a_touchable_map() {
        val image = Bitmap.createBitmap(256, 256, Bitmap.Config.ARGB_8888).apply { eraseColor(Color.CYAN) }
        compose.setContent {
            MaterialTheme {
                InteractiveMapDialog(
                    places = listOf("Los Angeles"),
                    url = "data:text/html,<html><body>Map unavailable</body></html>",
                    title = "Day 1",
                    externalUrl = null,
                    onDismiss = {},
                    loadStillMap = { image },
                )
            }
        }
        compose.waitUntil(timeoutMillis = 30_000) {
            compose.onAllNodesWithText("Zoom in").fetchSemanticsNodes().isNotEmpty()
        }
        compose.onNodeWithContentDescription("Trip map").performTouchInput {
            pinch(
                center + Offset(-30f, 0f), center + Offset(30f, 0f),
                center + Offset(-120f, 0f), center + Offset(120f, 0f),
            )
        }
        assertNotEquals("100% zoom", zoom())
        compose.onNodeWithText("Reset map").performClick()
        assertEquals("100% zoom", zoom())
    }

}
