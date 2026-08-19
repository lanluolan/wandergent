package com.wandergent.app.ui

import android.content.Intent
import android.graphics.Bitmap
import android.util.Log
import android.webkit.ConsoleMessage
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.Image
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.rememberTransformableState
import androidx.compose.foundation.gestures.transformable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Clear
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
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
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.core.net.toUri
import com.wandergent.app.data.DayMapClient
import kotlinx.coroutines.delay

private const val TAG = "WandergentMap"

/**
 * How long to let the Maps bundle boot before deciding it never will.
 *
 * The page itself gives up at 10s; this waits a little longer so the two do not race,
 * and so a slow-but-working connection is not called a failure.
 */
private const val PROBE_DELAY_MS = 12_000L

/** Past this the static image has no more pixels to give and only looks broken. */
private const val MAX_ZOOM = 4f

private const val DOUBLE_TAP_ZOOM = 2.5f

/**
 * One day's stops as a real map, in a WebView.
 *
 * **Why a WebView and not the Maps SDK for Android**: that SDK hard-requires Google Play
 * services, which the test device does not have. The JavaScript API needs only a
 * Chromium, so this is the one route to pan-and-zoom on that hardware. The page comes
 * from our backend, which injects the key -- nothing map-related ships in the APK.
 *
 * The device's WebView is old (Chrome 62 on the test phone, un-updatable without the
 * Play Store), so failures are expected on some hardware and are handled rather than
 * assumed away: JS console output goes to logcat under [TAG], and a page that cannot
 * load says so instead of showing an empty rectangle. The static image remains the
 * thing the day card shows; this is the tap-through.
 */
@Composable
fun InteractiveMapDialog(
    places: List<String>,
    url: String,
    title: String,
    externalUrl: String?,
    onDismiss: () -> Unit,
) {
    val context = LocalContext.current
    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) {
            var loading by remember { mutableStateOf(true) }
            var failure by remember { mutableStateOf<String?>(null) }
            var webView by remember { mutableStateOf<WebView?>(null) }
            // null while the answer is still unknown; false once the embedded map has
            // been proven not to work on this engine.
            var embeddedWorks by remember { mutableStateOf<Boolean?>(null) }

            // Ask the page itself whether the API is alive, rather than trusting that a
            // loaded page means a working map. On an old WebView the bundle downloads,
            // throws inside itself, and leaves `google.maps` undefined -- an HTTP-level
            // success and a visual failure. Probing is what turns that into a fallback
            // instead of a dead end.
            LaunchedEffect(loading, webView) {
                val view = webView ?: return@LaunchedEffect
                if (loading) return@LaunchedEffect
                delay(PROBE_DELAY_MS)
                view.evaluateJavascript(
                    "(typeof google !== 'undefined' && !!(google && google.maps))",
                ) { result ->
                    embeddedWorks = result == "true"
                    if (result != "true") Log.w(TAG, "embedded map unavailable; using the image")
                }
            }

            val openExternally = openExternally@{
                val target = externalUrl ?: return@openExternally
                val intent = Intent(Intent.ACTION_VIEW, target.toUri())
                // Nothing on the phone has to be able to handle it: a device with no
                // browser and no map app is a real configuration, and crashing on it
                // would be worse than the map simply not opening.
                runCatching { context.startActivity(intent) }
                    .onFailure { Log.w(TAG, "no app could open $target") }
            }

            Column(Modifier.fillMaxSize()) {
                Row(
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Text(
                        title,
                        style = MaterialTheme.typography.titleSmall,
                        modifier = Modifier.padding(start = 8.dp),
                    )
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        if (externalUrl != null) {
                            TextButton(onClick = openExternally) { Text("Directions") }
                        }
                        IconButton(onClick = onDismiss) {
                            Icon(Icons.Default.Clear, contentDescription = "Close map")
                        }
                    }
                }

                Box(Modifier.fillMaxSize()) {
                    if (embeddedWorks == false) {
                        // Proven not to work on this engine. A still map you can pinch
                        // and drag is a poorer map than a live one, and a far better
                        // screen than an apology -- the stops, the order and the shape
                        // of the day are all still legible, just not live.
                        ZoomableStaticMap(places)
                    }
                    AndroidView(
                        modifier = Modifier
                            .fillMaxSize()
                            // Kept in the tree rather than removed: tearing the WebView
                            // down mid-probe would race the very check that decided this.
                            .alpha(if (embeddedWorks == false) 0f else 1f),
                        factory = { context ->
                            WebView(context).apply {
                                webView = this
                                settings.javaScriptEnabled = true
                                settings.domStorageEnabled = true
                                // The page sets its own viewport; honour it rather than
                                // rendering at desktop width and scaling down.
                                settings.useWideViewPort = true
                                settings.loadWithOverviewMode = true
                                settings.builtInZoomControls = true
                                settings.displayZoomControls = false

                                webViewClient = object : WebViewClient() {
                                    override fun onPageFinished(view: WebView?, url: String?) {
                                        loading = false
                                    }

                                    override fun onReceivedError(
                                        view: WebView?,
                                        request: WebResourceRequest?,
                                        error: WebResourceError?,
                                    ) {
                                        // Only the main document counts. A failed tile
                                        // request is not a failed map.
                                        if (request?.isForMainFrame == true) {
                                            loading = false
                                            failure = "The map page failed to load"
                                            Log.w(TAG, "main frame failed: ${error?.description}")
                                        }
                                    }
                                }

                                // The whole point of the spike: on an old WebView the
                                // Maps API fails in the console, not in the HTTP layer.
                                webChromeClient = object : WebChromeClient() {
                                    override fun onConsoleMessage(
                                        message: ConsoleMessage,
                                    ): Boolean {
                                        Log.d(
                                            TAG,
                                            "[${message.messageLevel()}] ${message.message()} " +
                                                "(${message.sourceId()}:${message.lineNumber()})",
                                        )
                                        return true
                                    }
                                }
                            }
                        },
                        update = { it.loadUrl(url) },
                    )

                    if (loading && embeddedWorks == null) {
                        CircularProgressIndicator(
                            Modifier.align(Alignment.Center).size(32.dp),
                        )
                    }
                    if (embeddedWorks == false) {
                        Surface(
                            color = MaterialTheme.colorScheme.surfaceContainerHigh,
                            shape = RoundedCornerShape(8.dp),
                            modifier = Modifier.align(Alignment.BottomCenter).padding(12.dp),
                        ) {
                            Text(
                                "This device has an old browser engine, so the map is a still image. Pinch to zoom, or tap Directions for the live map.",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                            )
                        }
                    }
                    failure?.let {
                        Column(
                            modifier = Modifier.align(Alignment.Center).padding(24.dp),
                            horizontalAlignment = Alignment.CenterHorizontally,
                        ) {
                            Text(
                                it,
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            // The embedded map is the thing that failed, not the map
                            // data. On an old WebView this button is the whole feature.
                            if (externalUrl != null) {
                                TextButton(onClick = openExternally) {
                                    Text("Open the map in a browser")
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

/**
 * The day's static map, but pinchable.
 *
 * The image is requested square and large (the backend renders at scale 2, so 640
 * becomes 1280 real pixels) precisely so there is detail to zoom into -- the card's
 * 640x360 would turn to mush at 3x. Zoom is capped where the source runs out of pixels
 * rather than where the gesture runs out of fingers.
 *
 * Not a substitute for a live map: it cannot re-tile, so street names do not appear as
 * you go in. It is the most a device can show without a working Maps JavaScript API.
 */
@Composable
private fun ZoomableStaticMap(places: List<String>) {
    var bitmap by remember(places) { mutableStateOf<Bitmap?>(null) }
    LaunchedEffect(places) {
        bitmap = DayMapClient.load(places, widthPx = 640, heightPx = 640)
    }

    val image = bitmap
    if (image == null) {
        Box(Modifier.fillMaxSize()) {
            CircularProgressIndicator(Modifier.align(Alignment.Center).size(32.dp))
        }
        return
    }

    BoxWithConstraints(Modifier.fillMaxSize()) {
        var scale by remember { mutableStateOf(1f) }
        var offset by remember { mutableStateOf(Offset.Zero) }
        val width = constraints.maxWidth.toFloat()
        val height = constraints.maxHeight.toFloat()

        // Pan has to be clamped to what the zoom actually exposes, or the map can be
        // flung off screen and the only way back is to close the dialog.
        fun clamp(candidate: Offset, atScale: Float): Offset {
            val maxX = (width * (atScale - 1f)) / 2f
            val maxY = (height * (atScale - 1f)) / 2f
            return Offset(candidate.x.coerceIn(-maxX, maxX), candidate.y.coerceIn(-maxY, maxY))
        }

        val transform = rememberTransformableState { zoomChange, panChange, _ ->
            val next = (scale * zoomChange).coerceIn(1f, MAX_ZOOM)
            offset = clamp(offset + panChange, next)
            scale = next
        }

        Image(
            bitmap = image.asImageBitmap(),
            contentDescription = "Trip map, pinch to zoom",
            contentScale = ContentScale.Fit,
            modifier = Modifier
                .fillMaxSize()
                .graphicsLayer {
                    scaleX = scale
                    scaleY = scale
                    translationX = offset.x
                    translationY = offset.y
                }
                .transformable(transform)
                .pointerInput(Unit) {
                    detectTapGestures(
                        onDoubleTap = {
                            // One gesture back to the whole day, and one into detail.
                            if (scale > 1f) {
                                scale = 1f
                                offset = Offset.Zero
                            } else {
                                scale = DOUBLE_TAP_ZOOM
                                offset = Offset.Zero
                            }
                        },
                    )
                },
        )
    }
}
