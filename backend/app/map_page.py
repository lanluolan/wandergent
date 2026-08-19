"""The interactive day map, as a self-contained HTML page.

Why a web page rather than the Android Maps SDK: that SDK hard-requires Google Play
services, which the test device does not have. The JavaScript API only needs a
Chromium and a network, both of which a WebView has. This is the one route to a real
pan-and-zoom map on that hardware.

**Everything here is written for an old browser on purpose.** The device's system
WebView is Chrome 62 (2017) and cannot be updated without the Play Store, so:

- the classic `callback=` bootstrap, not `importLibrary` (which is newer and modular);
- `google.maps.Marker`, not `AdvancedMarkerElement` (needs a Map ID and a newer API);
- no Map ID at all, so the map renders raster rather than vector (vector wants WebGL
  and a current browser);
- `v=quarterly`, the most conservative supported channel;
- `crossorigin="anonymous"` on the bootstrap: Google serves the bundle with
  `Access-Control-Allow-Origin: *`, and without this attribute the browser sanitises
  any exception it throws down to a bare "Script error." with no source or line. With
  it, a failure names itself -- which is the difference between "the map broke" and
  "line N uses syntax this engine does not have";
- plain `var`/`function` in our own script, so nothing in *our* code is the thing that
  breaks first.

Points are geocoded server-side and embedded, so the page never calls Geocoding. That
is what lets the key this page carries be restricted to map rendering alone.
"""

import html
import json

from app.tools.maps import MARKER_LABELS, GeocodedPlace

# The brand teal, matching the static map's markers and route line so the two renderings
# of the same day do not look like two different products.
ROUTE_COLOUR = "#00696e"

_PAGE = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=5">
<title>{title}</title>
<style>
  html, body {{ margin: 0; padding: 0; height: 100%; background: #eceff0; }}
  /* Fixed, not `height: 100%`. A percentage height only resolves if every ancestor
     has one, and in a WebView with `useWideViewPort` the body can lay out at content
     height -- which for a page whose only child is the map is zero. The map then
     renders into a 360x0 box: no error, no tiles, just background. Positioning
     against the viewport cannot collapse that way. */
  #map {{ position: fixed; top: 0; left: 0; right: 0; bottom: 0; }}
  #fallback {{
    display: none; padding: 24px; font: 15px/1.6 system-ui, -apple-system, sans-serif;
    color: #3f4849; text-align: center;
  }}
  #fallback.shown {{ display: block; }}
  /* Debug only, and an overlay rather than part of the fallback: the interesting
     case is a map that half-works, which never reaches the fallback at all. */
  #diag {{
    display: none; position: fixed; left: 0; right: 0; bottom: 0; max-height: 45%;
    overflow: auto; padding: 8px 10px; background: rgba(255,255,255,0.92);
    border-top: 1px solid #bec8c9; font: 11px/1.45 monospace; color: #3f4849;
    word-break: break-all; white-space: pre-wrap; z-index: 9999;
  }}
  #diag.shown {{ display: block; }}
</style>
</head>
<body>
<div id="map"></div>
<div id="fallback">The map could not load on this device.<br>
  The itinerary itself is unaffected.</div>
<div id="diag"></div>
<script>
  var STOPS = {stops};
  var ROUTE_COLOUR = "{colour}";
  var DEBUG = {debug};

  // On-screen because the device's ROM suppresses logcat: without this, a failure is
  // indistinguishable from a blank rectangle and there is nothing to debug from.
  function diag(line) {{
    if (!DEBUG) return;
    var box = document.getElementById("diag");
    box.className = "shown";
    // The bootstrap URL carries the key, and a browser error quotes that URL back.
    // Printing it would put the key on screen and into any screenshot of a bug report.
    box.textContent = box.textContent + String(line).replace(/key=[^&\\s]+/g, "key=***") + "\\n";
  }}

  function showFallback(why) {{
    document.getElementById("map").style.display = "none";
    document.getElementById("fallback").className = "shown";
    if (why) diag(why);
  }}

  // Anything the API throws on an old browser lands here rather than as a blank
  // rectangle, which is the difference between "it failed" and "it is still loading".
  window.onerror = function (msg, src, line) {{
    showFallback("JS ERROR: " + msg + "\\n  at " + src + ":" + line);
    return false;
  }};
  window.gm_authFailure = function () {{
    showFallback("AUTH FAILURE: the key was rejected for this referrer/API");
  }};

  // Google reports key and billing problems by name on the console
  // ("ApiNotActivatedMapError", "RefererNotAllowedMapError", "BillingNotEnabled...").
  // That is the only place it says *which* one, and this ROM swallows logcat, so the
  // console has to be mirrored into the page to be readable at all.
  if (DEBUG && window.console) {{
    ["error", "warn"].forEach(function (level) {{
      var original = console[level];
      console[level] = function () {{
        diag(level.toUpperCase() + ": " + Array.prototype.join.call(arguments, " "));
        if (original) original.apply(console, arguments);
      }};
    }});
  }}

  diag("UA: " + navigator.userAgent);
  diag("stops: " + STOPS.length);

  function initMap() {{
    diag("bootstrap OK: google.maps loaded");
    var host = document.getElementById("map");
    // Explicit pixel height, set before the map is constructed and kept on resize.
    // Two earlier layouts both collapsed to zero: a percentage chain resolved to 0 in
    // this WebView, and fixed-position insets died when the API wrote its own inline
    // `position: relative` onto the container -- which, with the API's overflow:
    // hidden, clipped the whole subtree to nothing. That was invisible to the usual
    // probes: rects and computed styles look normal on a clipped tree, and the 360x592
    // measured *before* construction was true only until the constructor ran. Pixels
    // survive both failure modes: no chain to resolve, nothing for relative to break.
    host.style.height = window.innerHeight + "px";
    window.addEventListener("resize", function () {{
      host.style.height = window.innerHeight + "px";
    }});
    diag("container: " + host.offsetWidth + "x" + host.offsetHeight);
    if (!STOPS.length) {{ showFallback("no stops with coordinates"); return; }}

    var map = new google.maps.Map(document.getElementById("map"), {{
      zoom: 13,
      center: {{ lat: STOPS[0].lat, lng: STOPS[0].lng }},
      mapTypeControl: false,
      streetViewControl: false,
      fullscreenControl: false,
      gestureHandling: "greedy"
    }});

    var bounds = new google.maps.LatLngBounds();
    var path = [];
    var info = new google.maps.InfoWindow();

    for (var i = 0; i < STOPS.length; i++) {{
      var stop = STOPS[i];
      var point = {{ lat: stop.lat, lng: stop.lng }};
      path.push(point);
      bounds.extend(point);

      var marker = new google.maps.Marker({{
        position: point,
        map: map,
        title: stop.name,
        label: {{ text: stop.label, color: "#ffffff", fontSize: "12px", fontWeight: "600" }},
        icon: {{
          path: google.maps.SymbolPath.CIRCLE,
          scale: 13,
          fillColor: ROUTE_COLOUR,
          fillOpacity: 1,
          strokeColor: "#ffffff",
          strokeWeight: 2
        }}
      }});

      // A closure per marker: `stop` is a `var` and would otherwise be the last one
      // by the time anybody taps.
      google.maps.event.addListener(marker, "click", (function (which) {{
        return function () {{
          info.setContent(
            '<div style="font:14px/1.5 sans-serif;max-width:220px">' +
            '<b>' + which.label + '. ' + which.name + '</b>' +
            (which.address ? '<br>' + which.address : '') + '</div>'
          );
          info.open(map, this);
        }};
      }})(stop));
    }}

    if (path.length > 1) {{
      new google.maps.Polyline({{
        path: path,
        map: map,
        strokeColor: ROUTE_COLOUR,
        strokeOpacity: 0.8,
        strokeWeight: 4
      }});
      map.fitBounds(bounds, 48);
    }}

    google.maps.event.addListenerOnce(map, "tilesloaded", function () {{
      diag("tilesloaded: the map is drawn");
    }});
    // Post-construction, because the constructor rewrites the container's inline
    // style: this is the measurement that actually describes what gets painted.
    setTimeout(function () {{
      diag("post-Map container: " + host.offsetWidth + "x" + host.offsetHeight
        + " position=" + getComputedStyle(host).position);
    }}, 1500);

    // Tiles come from different hosts than the bundle, and a network that reaches
    // maps.googleapis.com does not necessarily reach gstatic. Probing each one says
    // whether "no tiles" is a rendering problem or a reachability problem.
    if (DEBUG) {{
      [
        ["gstatic", "https://maps.gstatic.com/mapfiles/api-3/images/spotlight-poi2.png"],
        ["tile", "https://maps.googleapis.com/maps/vt?pb=!1m4!1m3!1i2!2i1!3i1"]
      ].forEach(function (pair) {{
        var probe = new Image();
        probe.onload = function () {{ diag("probe " + pair[0] + ": OK"); }};
        probe.onerror = function () {{ diag("probe " + pair[0] + ": UNREACHABLE"); }};
        probe.src = pair[1];
      }});

      // The question that decides everything downstream: did tile elements enter the
      // DOM? Zero images/canvases means the map never *asked* for pixels (likely a
      // renderer the engine cannot provide, e.g. WebGL); a populated DOM that shows
      // nothing means the pixels exist but compositing failed (a WebView layer
      // problem on the Android side). Same blank rectangle, opposite fixes.
      setTimeout(function () {{
        var host = document.getElementById("map");
        diag("map DOM: " + host.querySelectorAll("*").length + " nodes, "
          + host.querySelectorAll("img").length + " img, "
          + host.querySelectorAll("canvas").length + " canvas");
        var canvases = host.querySelectorAll("canvas");
        for (var c = 0; c < Math.min(canvases.length, 3); c++) {{
          diag("  canvas[" + c + "]: " + canvases[c].width + "x" + canvases[c].height);
        }}
        var imgs = host.querySelectorAll("img");
        var loaded = 0;
        for (var m = 0; m < imgs.length; m++) {{
          if (imgs[m].complete && imgs[m].naturalWidth > 0) loaded++;
        }}
        if (imgs.length) diag("  img loaded: " + loaded + "/" + imgs.length);
        // Whether this engine can hand Maps a GL context at all.
        var gl2 = null, gl1 = null;
        try {{ gl2 = document.createElement("canvas").getContext("webgl2"); }} catch (e) {{}}
        try {{ gl1 = document.createElement("canvas").getContext("webgl"); }} catch (e) {{}}
        diag("webgl2: " + (gl2 ? "yes" : "NO") + ", webgl: " + (gl1 ? "yes" : "NO"));
        if (gl1) {{
          var dbgInfo = gl1.getExtension("WEBGL_debug_renderer_info");
          if (dbgInfo) diag("gl renderer: " + gl1.getParameter(dbgInfo.UNMASKED_RENDERER_WEBGL));
        }}
        diag("mapTypeId: " + map.getMapTypeId() + ", renderingType: "
          + (map.getRenderingType ? map.getRenderingType() : "n/a"));

        // Which *kind* of content fails to paint? Maps draws into canvases inside
        // translate3d-composited layers. Painting a bare canvas and a translate3d div
        // side by side splits the failure: if both show, compositing works and the
        // fault is inside Maps; if the canvas shows but the 3d layer does not (or
        // neither does), the WebView is dropping accelerated layers and the fix is on
        // the Android side, not in this page.
        var probeBox = document.createElement("div");
        probeBox.style.cssText = "position:fixed;top:4px;left:4px;z-index:10000;";
        var c2d = document.createElement("canvas");
        c2d.width = 48; c2d.height = 48;
        c2d.style.cssText = "display:block;border:1px solid #000";
        var ctx = c2d.getContext("2d");
        ctx.fillStyle = "#d22"; ctx.fillRect(0, 0, 48, 48);
        probeBox.appendChild(c2d);
        var t3d = document.createElement("div");
        t3d.style.cssText = "width:48px;height:48px;background:#26c;border:1px solid #000;"
          + "transform:translate3d(0,0,0);margin-top:2px";
        probeBox.appendChild(t3d);
        document.body.appendChild(probeBox);
        diag("paint probes: red canvas + blue translate3d, top-left");

        // The probes paint, the map does not -- so the question is what hides the
        // map's own layers. Geometry says whether they are on screen at all; opacity
        // catches a fade-in that never ran; visibilityState and a rAF count catch a
        // page the engine believes is hidden, which stalls exactly the animation
        // machinery Maps reveals its tiles with.
        diag("visibilityState: " + document.visibilityState);
        var ticks = 0;
        var tick = function () {{ ticks++; if (ticks < 60) requestAnimationFrame(tick); }};
        requestAnimationFrame(tick);
        setTimeout(function () {{ diag("rAF ticks in 1s: " + ticks); }}, 1000);

        for (var q = 0; q < Math.min(canvases.length, 2); q++) {{
          var el = canvases[q];
          var r = el.getBoundingClientRect();
          var line = "canvas[" + q + "] rect " + Math.round(r.left) + ","
            + Math.round(r.top) + " " + Math.round(r.width) + "x" + Math.round(r.height);
          var node = el, depth = 0;
          while (node && node !== host && depth < 12) {{
            var cs = getComputedStyle(node);
            if (cs.opacity !== "1" || cs.visibility !== "visible" || cs.display === "none") {{
              line += " | " + node.tagName + " op=" + cs.opacity
                + " vis=" + cs.visibility + " disp=" + cs.display;
            }}
            node = node.parentElement; depth++;
          }}
          diag(line);
        }}

        // The screenshot sampling showed the body background where the map should
        // be -- the whole #map subtree paints nothing, its own background included.
        // Four probes split what is left: does the div itself paint when forced to;
        // does a child of it; has canvas control been transferred to a worker
        // (getContext throws InvalidStateError); and was anything cross-origin ever
        // drawn into a tile canvas (toDataURL throws SecurityError -- taint as proof
        // of drawing).
        host.style.backgroundColor = "#f0f";
        var childProbe = document.createElement("div");
        childProbe.style.cssText = "position:absolute;top:60px;left:4px;width:48px;"
          + "height:48px;background:#fd0;border:1px solid #000;z-index:9999";
        host.appendChild(childProbe);
        diag("forced #map magenta + yellow child inside it");
        if (canvases.length) {{
          try {{
            var got = canvases[0].getContext("2d");
            diag("canvas[0].getContext(2d): " + (got ? "ok (not transferred)" : "null"));
          }} catch (e) {{ diag("canvas[0].getContext(2d) THREW: " + e.name); }}
          try {{
            canvases[0].toDataURL();
            diag("canvas[0].toDataURL: ok -> canvas is CLEAN (nothing drawn)");
          }} catch (e) {{ diag("canvas[0].toDataURL THREW " + e.name + " -> tiles WERE drawn"); }}
        }}

        // The 48px probe canvas painted; Maps' 512px ones stay blank and undrawn.
        // Chromium only GPU-accelerates canvases above a size threshold, so if a big
        // canvas fails where a small one worked, the accelerated-canvas path is what
        // this GPU driver breaks on. The big fixed div separates "large compositor
        // layer" from "canvas" as the failing ingredient.
        var big = document.createElement("canvas");
        big.width = 512; big.height = 512;
        big.style.cssText = "position:fixed;top:120px;left:4px;width:128px;height:128px;"
          + "z-index:10001;border:1px solid #000";
        var bctx = big.getContext("2d");
        bctx.fillStyle = "#0a4"; bctx.fillRect(0, 0, 512, 512);
        document.body.appendChild(big);
        var wide = document.createElement("div");
        wide.style.cssText = "position:fixed;left:0;right:0;bottom:0;height:24px;"
          + "background:#a3f;z-index:10001";
        document.body.appendChild(wide);
        diag("big probes: green 512px canvas (shown 128px) + purple full-width bar");
      }}, 6000);
    }}
    diag("initMap finished, " + STOPS.length + " markers placed");
  }}
</script>
<script src="https://maps.googleapis.com/maps/api/js?key={key}&callback=initMap&language={lang}&v=quarterly"
        crossorigin="anonymous"
        onerror="showFallback('SCRIPT LOAD FAILED: could not fetch the bundle')"></script>
<script>
  // If neither the callback nor an error has fired by now, the bundle loaded but never
  // called back -- which is what a bundle that cannot parse on this engine looks like.
  setTimeout(function () {{
    if (typeof google === "undefined" || !google.maps) {{
      showFallback("TIMEOUT: bundle did not initialise (google.maps undefined after 10s)");
    }}
  }}, 10000);
</script>
</body>
</html>
"""


def day_map_page(
    points: list[GeocodedPlace],
    key: str,
    language: str = "zh-CN",
    debug: bool = False,
) -> str:
    """Render one day's stops as an interactive map page.

    Stops that failed to geocode are dropped from the map but keep their number, so
    marker "4" is the fourth activity of the day whether or not stop 3 resolved.
    """
    stops = [
        {
            "label": label,
            # The itinerary's own words lead, because that is what the traveller is
            # looking for on the screen; Google's formatted address is the sub-line.
            "name": point.query,
            "address": point.formatted or "",
            "lat": point.latitude,
            "lng": point.longitude,
        }
        for label, point in zip(MARKER_LABELS, points, strict=False)
        if point.ok and point.latitude is not None and point.longitude is not None
    ]
    return _PAGE.format(
        # The key goes into an attribute and the stops into a script literal, so each
        # gets the escaping of the place it lands in. Never swap these.
        key=html.escape(key, quote=True),
        lang=html.escape(language, quote=True),
        title="Trip map",
        colour=ROUTE_COLOUR,
        debug="true" if debug else "false",
        stops=json.dumps(stops, ensure_ascii=False).replace("</", "<\\/"),
    )
