package com.wandergent.app.ui

import android.content.Intent
import android.content.ActivityNotFoundException
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
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.stateDescription
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
 * **Not the Maps SDK for Android**: that hard-requires Play services, which the test
 * device does not have. The JavaScript API needs only a Chromium, and the page comes from
 * our backend, which injects the key -- nothing map-related ships in the APK.
 *
 * An old WebView (Chrome 62 on the test phone) is expected, so failures are handled rather
 * than assumed away: JS console output goes to logcat under [TAG], and a page that cannot
 * load falls back to the static image instead of showing an empty rectangle.
 */
@Composable
fun InteractiveMapDialog(
    places: List<String>,
    url: String,
    title: String,
    externalUrl: String?,
    onDismiss: () -> Unit,
    loadStillMap: suspend (List<String>) -> Bitmap? = {
        DayMapClient.load(it, widthPx = 640, heightPx = 640)
    },
) {
    val context = LocalContext.current
    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) {
            var loading by remember(url) { mutableStateOf(true) }
            var failure by remember(url) { mutableStateOf<String?>(null) }
            var webView by remember { mutableStateOf<WebView?>(null) }
            // null while the answer is still unknown; false once the embedded map has
            // been proven not to work on this engine.
            var embeddedWorks by remember(url) { mutableStateOf<Boolean?>(null) }

            // Bound a stalled main document too: onPageFinished may never arrive.
            LaunchedEffect(url) {
                delay(25_000L)
                if (embeddedWorks == null) {
                    loading = false
                    embeddedWorks = false
                }
            }

            // Ask the page whether the API is alive rather than trusting that a loaded
            // page means a working map: on an old WebView the bundle downloads, throws
            // inside itself, and leaves `google.maps` undefined.
            LaunchedEffect(loading, webView) {
                val view = webView ?: return@LaunchedEffect
                if (loading || embeddedWorks == false) return@LaunchedEffect
                delay(PROBE_DELAY_MS)
                view.evaluateJavascript(
                    "window.wandergentMapReady === true",
                ) { result ->
                    embeddedWorks = result == "true"
                    if (result != "true") Log.w(TAG, "embedded map unavailable; using the image")
                }
            }

            val openExternally = openExternally@{
                val target = externalUrl ?: return@openExternally
                val intent = Intent(Intent.ACTION_VIEW, target.toUri())
                // A device with no browser and no map app is a real configuration.
                try {
                    context.startActivity(intent)
                } catch (e: ActivityNotFoundException) {
                    failure = "No browser or maps app is available to open directions."
                }
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
                                            embeddedWorks = false
                                            Log.w(TAG, "main frame failed: ${error?.description}")
                                        }
                                    }
                                }

                                // On an old WebView the Maps API fails in the console,
                                // not in the HTTP layer.
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
                        // State changes recompose this view; they must not restart the page.
                        update = {
                            if (it.tag != url) {
                                it.tag = url
                                it.loadUrl(url)
                            }
                        },
                        onRelease = {
                            it.stopLoading()
                            it.destroy()
                        },
                    )

                    // Above the hidden WebView so it receives gestures instead of the browser.
                    if (embeddedWorks == false) {
                        ZoomableStaticMap(places, loadStillMap)
                    }

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
                                "Showing a still map. Pinch or use the zoom buttons, or tap Directions for the live map.",
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
 * Requested square and large (the backend renders at scale 2, so 640 becomes 1280 real
 * pixels) precisely so there is detail to zoom into; [MAX_ZOOM] is where the source runs
 * out of pixels. It cannot re-tile, so street names never appear -- this is the most a
 * device can show without a working Maps JavaScript API.
 */
@Composable
internal fun ZoomableStaticMap(
    places: List<String>,
    load: suspend (List<String>) -> Bitmap? = { DayMapClient.load(it, widthPx = 640, heightPx = 640) },
) {
    var bitmap by remember(places) { mutableStateOf<Bitmap?>(null) }
    var loading by remember(places) { mutableStateOf(true) }
    var attempt by remember(places) { mutableStateOf(0) }
    LaunchedEffect(places, attempt) {
        loading = true
        bitmap = load(places)
        loading = false
    }

    val image = bitmap
    if (image == null) {
        Box(Modifier.fillMaxSize()) {
            if (loading) {
                CircularProgressIndicator(Modifier.align(Alignment.Center).size(32.dp))
            } else {
                Column(Modifier.align(Alignment.Center), horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("Could not load this map.")
                    TextButton(onClick = { attempt++ }) { Text("Retry map") }
                }
            }
        }
        return
    }

    ZoomableMapImage(image)
}

@Composable
internal fun ZoomableMapImage(image: Bitmap) {
    BoxWithConstraints(Modifier.fillMaxSize().clipToBounds()) {
        var scale by remember { mutableStateOf(1f) }
        var offset by remember { mutableStateOf(Offset.Zero) }
        val width = constraints.maxWidth.toFloat()
        val height = constraints.maxHeight.toFloat()

        // Pan has to be clamped to what the zoom actually exposes, or the map can be
        // flung off screen and the only way back is to close the dialog.
        fun clamp(candidate: Offset, atScale: Float): Offset {
            val fit = minOf(width / image.width, height / image.height)
            val maxX = ((image.width * fit * atScale - width) / 2f).coerceAtLeast(0f)
            val maxY = ((image.height * fit * atScale - height) / 2f).coerceAtLeast(0f)
            return Offset(candidate.x.coerceIn(-maxX, maxX), candidate.y.coerceIn(-maxY, maxY))
        }

        val transform = rememberTransformableState { _, zoomChange, panChange, _ ->
            val next = (scale * zoomChange).coerceIn(1f, MAX_ZOOM)
            offset = clamp(offset + panChange, next)
            scale = next
        }

        Box(
            Modifier.fillMaxSize()
                .semantics {
                    contentDescription = "Trip map"
                    stateDescription = "${(scale * 100).toInt()}% zoom"
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
        ) {
            Image(
                bitmap = image.asImageBitmap(),
                contentDescription = null,
                contentScale = ContentScale.Fit,
                modifier = Modifier.fillMaxSize().graphicsLayer {
                    scaleX = scale
                    scaleY = scale
                    translationX = offset.x
                    translationY = offset.y
                },
            )
        }
        Surface(Modifier.align(Alignment.TopEnd).padding(8.dp), shape = RoundedCornerShape(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                TextButton(
                    onClick = {
                        scale = (scale / 1.5f).coerceAtLeast(1f)
                        offset = clamp(offset, scale)
                    },
                    enabled = scale > 1f,
                ) { Text("Zoom out") }
                TextButton(
                    onClick = { scale = (scale * 1.5f).coerceAtMost(MAX_ZOOM) },
                    enabled = scale < MAX_ZOOM,
                ) { Text("Zoom in") }
                TextButton(onClick = {
                    scale = 1f
                    offset = Offset.Zero
                }) { Text("Reset map") }
            }
        }
    }
}
