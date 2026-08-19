# Backend API contract

> The contract the Android client codes against. Every shape here was dumped from the running
> app's OpenAPI schema and real responses, not written from memory. Regenerate after changing
> any Pydantic model; interactive docs are at `/docs` while the backend runs.
>
> ```
> cd backend && uv run python -c "import json; from app.main import app; print(json.dumps(app.openapi(), ensure_ascii=False, indent=2))"
> ```

`POST /plan/stream` (SSE) is the primary path. `POST /plan` is the non-streaming fallback --
the client retries against it if the stream produces nothing, so a proxy that breaks SSE
degrades instead of failing. Both run the same orchestrator; only delivery differs.

---

## Base URL

| Where the client runs | Base URL |
|---|---|
| **Physical device over USB (what we actually use)** | `http://127.0.0.1:8000` — bridged by `adb reverse tcp:8000 tcp:8000`, so the backend can stay on loopback. The tunnel does not survive an unplug; re-run it. |
| Android emulator (AVD) | `http://10.0.2.2:8000` — `10.0.2.2` is the emulator's alias for the host's loopback |
| Physical device on the same LAN | `http://<host-LAN-IP>:8000` — needs `--host 0.0.0.0` and an open firewall; superseded by `adb reverse` |
| Backend host itself | `http://127.0.0.1:8000` |

The dev command binds loopback only. With `adb reverse` that is all you need; the emulator and LAN cases require the wider bind:

```
cd backend && uv run uvicorn app.main:app --reload                    # host only (+ adb reverse)
cd backend && uv run uvicorn app.main:app --reload --host 0.0.0.0     # reachable from emulator / LAN
```

Android 9+ blocks cleartext HTTP by default. Dev builds need a
`network_security_config.xml` that permits cleartext to the dev host, or the app will
fail with `CLEARTEXT communication not permitted` before any request is sent.

---

## `GET /health`

Liveness check. No auth, no body.

```json
{ "status": "ok", "app": "Wandergent" }
```

---

## `POST /plan`

Natural-language trip request -> validated itinerary. Synchronous and slow: typically 2 LLM
calls plus tools, up to 4 with a repair round. **Budget 15–60 s** and raise the OkHttp read
timeout — the 10 s default cuts it off mid-flight.

### Request

```json
{ "message": "3 days in Los Angeles next month, budget 900 USD, I like food and museums, no hiking" }
```

| Field | Type | Rules |
|---|---|---|
| `message` | string | required, 1–2000 chars |
| `currency` | string | optional, ISO 4217 (`"USD"`) or empty; anything else is a 422. The plan is **estimated** in it, never converted into it — a conversion would need an FX source, and a stale rate makes an estimate look precise. Empty lets the model use the destination's local currency, which is what callers got before this existed. |
| ~~`user_id`~~ | — | **Removed.** Identity comes from the `Authorization: Bearer <token>` header, never from the body: a body field is a claim the client makes, and anyone could claim anyone else's id. A request with no token is anonymous — nothing is recalled and nothing is stored. |

### Response `200`

```json
{
  "itinerary": {
    "destination": "Los Angeles",
    "start_date": "2026-09-14",
    "end_date": "2026-09-15",
    "travelers": 2,
    "currency": "USD",
    "budget": 900.0,
    "days": [
      {
        "date": "2026-09-14",
        "summary": "Santa Monica and the coast",
        "weather": "Sunny, 24C",
        "activities": [
          {
            "start_time": "10:00",
            "end_time": "12:00",
            "title": "Santa Monica Pier",
            "category": "sightseeing",
            "location": "200 Santa Monica Pier, Santa Monica, CA 90401",
            "indoor": false,
            "estimated_cost": 0.0,
            "highlights": [
              "Pacific Park ferris wheel",
              "The original Muscle Beach"
            ],
            "notes": null
          },
          {
            "start_time": "12:30",
            "end_time": "13:30",
            "title": "Lunch on Ocean Ave",
            "category": "food",
            "location": "1401 Ocean Ave, Santa Monica, CA 90401",
            "indoor": true,
            "estimated_cost": 48.0,
            "highlights": [
              "Fish tacos"
            ],
            "notes": null
          },
          {
            "start_time": "16:00",
            "end_time": "16:30",
            "title": "Check in",
            "category": "accommodation",
            "location": "1740 Ocean Ave, Santa Monica, CA 90401",
            "indoor": true,
            "estimated_cost": 0.0,
            "highlights": [],
            "notes": null
          }
        ],
        "estimated_cost": 48.0
      },
      {
        "date": "2026-09-15",
        "summary": "Museums, indoors while it rains",
        "weather": "Rain, 19C",
        "activities": [
          {
            "start_time": "10:00",
            "end_time": "13:00",
            "title": "The Getty Center",
            "category": "sightseeing",
            "location": "1200 Getty Center Dr, Los Angeles, CA 90049",
            "indoor": true,
            "estimated_cost": 0.0,
            "highlights": [
              "Free entry, parking is the cost",
              "Van Gogh's Irises"
            ],
            "notes": "Closed Mondays."
          }
        ],
        "estimated_cost": 0.0
      }
    ],
    "notes": [
      "Rain on the 15th -- the museum day is second on purpose."
    ],
    "total_estimated_cost": 48.0
  },
  "tool_calls": [
    {
      "name": "get_weather_forecast",
      "arguments": {
        "city": "Los Angeles",
        "start_date": "2026-09-14",
        "end_date": "2026-09-15"
      },
      "ok": true,
      "error": null
    }
  ],
  "warnings": [],
  "raw_reply": null,
  "validation": {
    "violations": []
  }
}
```

### Field notes for the client

- **`itinerary` can be `null` on a `200`** — the model could not produce a valid plan. A `no_itinerary` warning says so, `raw_reply` carries what it said. Render as a failure state; do not crash on the null.
- **Costs are computed server-side** from the activities. Always present in responses, ignored if sent inbound.
- **`tool_calls` is an audit trail** in call order, with `ok=false` + `error` when a tool degraded (weather API down, dates beyond the 16-day horizon). A thin-looking plan is usually explained here.
- **`warnings`** are notes about the **run**, distinct from `itinerary.notes` (travel advice) and from `validation` (what is wrong with the *plan*). Those three lists do not overlap: until 2026-08-18 the validation findings were restated here as well, and every client had to suppress the duplicates by comparing sentences.
  - Each entry is `{"code", "detail", "budget", "dropped_calls", "dropped_tools"}`. **Render from `code`; `detail` is English aimed at whoever is debugging** — `reached the 16-call tool budget; skipped 3 further call(s) to search_places` is not something to put in front of a traveller. Fall back to `detail` only for a code you do not recognise.
  - Codes: `tool_calls_dropped` (the model asked for more calls than the budget had left, so some were never made — `dropped_calls` and `dropped_tools` say how many and to what), `tool_calls_spent` and `tool_rounds_spent` (a ceiling ended the research phase; `budget` is the ceiling), `no_itinerary`. All parameters are present on every entry, `null` or `[]` where they do not apply.
  - A client older than the server meets unknown codes, so treat `code` as an open string, never an enum that throws.
- **`highlights`** is a list of concrete specifics on an activity — dishes to order, exhibits worth the queue, the room type. **Often empty**; render it as an optional detail block, never assume it is populated.
- **`validation`** is the hard-constraint check on the plan as shipped: `{"violations": [{"code", "message", "day", ...}]}`. Empty = feasible.
  - On `insufficient_transfer` only, four extra fields carry the detail: `origin`, `destination`, `gap_minutes`, and `needed_minutes`. `needed_minutes` is present when the hop was **measured** against real travel times rather than guessed from the location strings — a client can say "needs 25 min, you left 10" instead of a generic warning. All four are absent on other codes. Non-empty means repair was attempted and did **not** fully succeed, so the client must not present the plan as sound. Localize off `code` (`over_budget`, `time_conflict`, `insufficient_transfer`, `day_out_of_range`, `duplicate_day`, `unsociable_hours`, `overlong_day`, `empty_day`, `missing_accommodation`, `vague_venue`, `outside_opening_hours`); `message` is English text meant for the model.
  - `outside_opening_hours` is checked against the opening hours the run already fetched from Google during `search_places`, not against anything the model reported, so a plan cannot satisfy it by asserting. A venue the run never looked up gets no opinion rather than a closure.
- **Nullable in every response**: `itinerary`, `raw_reply`, `budget`, `weather`, `location`, `indoor`, activity `notes`. A non-null `Boolean` for `indoor` will throw — "unknown" is a real value.
- Dates are ISO `YYYY-MM-DD`. `start_time` / `end_time` are local wall-clock `HH:MM`, 24-hour, no timezone — **not instants**.
- `category` ∈ `sightseeing` `food` `transport` `accommodation` `activity` `rest` `other`. Unrecognised values are coerced to `other`, so the set is closed.

---

## `GET /day-map`

One day's stops drawn as a PNG: numbered markers in visiting order, joined by a route line.

```
GET /day-map?place=Santa%20Monica%20Pier&place=The%20Getty%20Center,%20Los%20Angeles&width=640&height=360
-> 200 image/png, Cache-Control: public, max-age=86400
```

| Param | Rules |
|---|---|
| `place` | Repeat once per stop, in order. 1-19; extras are dropped rather than mislabelled (marker labels are one character). Pass the activity's `location`. |
| `width` / `height` | Pixels, clamped to 64-640. Default 640x360. |

- **The client asks this backend, never Google.** The Maps key stays server-side, so nothing ships in the APK and the app works on devices without Google Play services.
- **Straight segments, not road geometry** — this is orientation, not navigation.
- **502 when the map cannot be drawn** (no key, upstream timeout, unroutable input). The plan itself is unaffected, so a client should hide the map rather than surface an error.
- Coordinates are not needed: Static Maps geocodes the place strings itself.

---

## `POST /plan/stream`

Same input and same final payload as `/plan`, delivered as Server-Sent Events. Response is
`text/event-stream` with `Cache-Control: no-cache` and `X-Accel-Buffering: no` (the latter
stops nginx-style proxies buffering the stream into one lump).

Each event is `event: <type>` + `data: <json>`. The JSON is one flat object; `type` says
which fields are meaningful and the rest are null:

| `type` | Fields | Meaning |
|---|---|---|
| `stage` | `name`, `message` | Coarse phase: `understanding` / `composing` / `repairing`. `name` is stable, `message` is a default Chinese label — localize off `name` if needed |
| `tool_call` | `name`, `arguments` | The model asked for a tool, with the arguments it chose |
| `tool_result` | `name`, `ok`, `error` | That tool returned; `ok=false` means degraded, not fatal |
| `composing` | `message` | A place name recognised in the partially-written itinerary |
| `validation` | `violations` | The hard-constraint check ran. Empty list = passed. May appear twice: once failing, once passing after repair |
| `result` | `result` | **Terminal.** Same `PlanResult` shape `/plan` returns |
| `error` | `message` | **Terminal.** The run failed |

```
event: tool_call
data: {"type":"tool_call","name":"get_weather_forecast","arguments":{"city":"Los Angeles","start_date":"2026-09-14","end_date":"2026-09-15"},"message":null,"ok":null,"error":null,"result":null}

event: tool_result
data: {"type":"tool_result","name":"get_weather_forecast","ok":true,"error":null,"message":null,"arguments":null,"result":null}
```

### Client notes

- **Errors arrive as an `error` event, not a status code** — the response committed to `200` before the tool failed. Treat it as fatal.
- **Exactly one terminal event** (`result` or `error`) ends a run. A stream closing without one means the connection dropped.
- **Ignore unknown `type` values**, do not error — later phases add event kinds and older clients must keep working.
- The same tool can appear twice (a later round). Match a `tool_result` to the oldest still-pending call with that `name`. An identical repeat is answered from a per-run cache but still reported, so the trail stays honest.
- `remember_preference` streams like any other tool; `arguments.preferences` is what the agent chose to remember.
- **Timeouts apply *between* events**, not to the whole stream. 180 s is comfortable; gaps while composing reach ~20 s.

### Error responses

All errors return FastAPI's `{"detail": "..."}` shape.

| Status | Meaning | What the client should do |
|---|---|---|
| `422` | `message` missing, empty, or over 2000 chars | Fix input; validate before sending |
| `500` | Server misconfigured (e.g. `OPENAI_API_KEY` not set) | Not retryable — surface as a server problem |
| `504` | The LLM timed out | Retryable |
| `502` | The LLM is unavailable | Retryable with backoff |

`500` is deliberately distinct from `502`/`504` so the client can tell "the backend is
set up wrong" from "the model is having a bad day".

---

## Community feed

Shared itineraries. Ordinary CRUD over one SQLite table -- none of it touches the model,
so these answer in milliseconds rather than the minutes a planning run takes.

> **Writes require a bearer token; reads do not.** Identity is never taken from the
> request body -- those fields no longer exist. **Nothing here is moderated or rate
> limited**, which is the remaining reason not to expose this to strangers.

| Verb | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/community/plans` | required | Publish an itinerary. 201 with the full record. |
| `GET` | `/community/plans` | optional | Feed, newest first. `limit` clamped to 100. |
| `GET` | `/community/plans/{id}` | optional | One plan with its itinerary. |
| `POST` | `/community/plans/{id}/save` | required | Save or unsave. Body `{saved}`. |
| `DELETE` | `/community/plans/{id}` | required | Withdraw your own. 204, or 404. |

**Publish body** carries `request`, `note` and `itinerary` -- and nothing else. The author
comes from the token. `destination`, `day_count`, `total_cost`, `currency` and the dates
are derived server-side from the itinerary, so a caller cannot advertise a trip as
somewhere or something it is not.

**Feed cards omit `itinerary`**; the detail endpoint carries it. Thirty cards should not
pull thirty itineraries over a phone connection.

**`saved_by_viewer` is tri-state.** `null` means the request named no viewer, which is a
different claim from `false` ("you have not saved this") and must not draw an empty heart.

**Saving is idempotent in both directions.** `(plan_id, user_id)` is the primary key of
the saves table, so a retry on a flaky connection cannot double-count.

**404 covers both "gone" and "not yours"** on withdraw, deliberately: the client cannot
tell them apart either, and the message says the part that is true in both cases.

---

## Accounts

| Verb | Path | Purpose |
|---|---|---|
| `POST` | `/auth/register` | Create an account. 201 with a token, or 409 if the name is taken. |
| `POST` | `/auth/login` | Start a session. 200 with a token, or 401. |
| `POST` | `/auth/logout` | End this session. Always 204. |
| `GET` | `/auth/me` | Who this token belongs to. 401 if it is not live. |

The token goes in `Authorization: Bearer <token>` and **never in a query string** -- that
would put it in the access log, which is exactly the bug found here on 2026-08-17 when
httpx logged the Maps key it had to pass as a parameter.

**Register returns a token**, so there is no window where the account exists and cannot be
used. **Login answers the same way for an unknown user and a wrong password**, because
telling them apart hands out a list of which accounts exist. **Logout is 204 even for a
token that was not live**, for the same reason.

Two dependencies express the whole authorization model: an optional one that yields the
account if a token was sent, and a required one that 401s (with `WWW-Authenticate: Bearer`)
if it cannot. Planning and reading the feed use the first; everything that writes something
other people see uses the second.

`PlanRequest` has no `user_id`. Which preferences a run recalls and updates comes from the
token, so a caller cannot aim a memory write at somebody else's account -- which is what
that field allowed while it existed. A request with no token, or a lapsed one, plans
anonymously rather than failing.
