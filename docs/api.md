# Backend API contract

The App's research calls use the project's MCP server over stdio, started automatically
by the backend. Its tools are `get_weather_forecast`, `search_places`, `get_travel_time`
and `search_web`. The HTTP/SSE contract below is unchanged by this transport change.
Google Maps and Brave keys stay on the backend; there is no Wanderlog connection.
Route checks send timezone-aware `depart_at` values. MCP tools expose no account context,
memory writes or HTTP-client injection parameters.

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

### Clarification and continuation (2026-10-04)

A normal terminal result may contain `clarification` instead of an itinerary. It carries
`questions` and a `reason` (`inputs`, `weather` or `transport`); this is not a failed plan
and does not carry `no_itinerary`. The accompanying `continuation` contains the original
request, pending questions and any uncovered `weather_dates`.

To answer, send the user's reply as `message` and echo `continuation`, retaining the
previous itinerary and request-owned constraints when editing. For weather fallback,
send `weather_fallback_confirmed=true` only after explicit user consent. Consent covers
only the dates in that continuation; newly uncovered dates require confirmation again.
Missing consent defaults to false. A `New trip`/`新旅行` request discards continuation.
Confirmed dates are retained in `constraints.weather_fallback_dates`; echo the returned
constraints as `previous_constraints` so another clarification or revision of the same
trip does not ask again for already approved dates.

Weather is checked for indoor-only trips too. Confirmed seasonal assumptions are labelled
in itinerary notes and affected days' weather fields. The Android client displays pending
questions, retains them across restarts and offers an explicit weather confirmation button.

### Additive observability metadata (2026-09-30)

Activities include nullable `place_summary`, attached server-side from an exact,
unambiguous Google Places match. It carries the unchanged `editorialSummary`
text in `text`, `source="editorialSummary"`, nullable `language_code` and nullable
`collected_at`. The UI shows this introduction instead of legacy `highlights`,
with Google Maps attribution. Missing summaries remain absent; neither the server
nor the LLM synthesizes a fallback. Previously saved generative summaries without
an editorial source marker still decode but are not displayed.

Restaurant budgets now use the midpoint of Google `priceRange` times party size;
`priceLevel` is no longer requested or used. A midpoint requires both bounds in the
same currency. Missing bounds and currency mismatches remain unverified estimates;
the backend does not invent exchange rates or infer hotel/admission quotes from a
place's price range.

Google Routes transit fares are multiplied by party size (one quoted fare per traveller).
Driving tolls are added once for an assumed single vehicle. Transport activities carry
nullable `transport_base_cost` in trip currency, excluding tolls, so revisions do not
add tolls twice. Clients must retain it when sending a previous itinerary. Confirmed
route costs are applied before the final budget check; locked revision days are preserved.

Both response paths now include nullable `trace_id` and `activity_evidence` (default `[]`).
Evidence entries use zero-based `day_index` and `activity_index` into the final itinerary.
They include nullable `source`/`collected_at`, boolean `venue_verified`, `hours_available`
and `price_range_available`, `price_confidence="estimate"` and
`recheck_before_departure=true`. Nullable route fields are `route_source`,
`route_collected_at`, `route_departure`, `route_mode` and integer `route_seconds`.
Tool call records also add nullable `collected_at`; internal `fact_payload` is excluded.

These are coverage metadata, not whole-activity validation. Original timestamps survive
cache reuse, ambiguous locations remain unverified, and prices are always estimates.
Older responses/saved plans decode with empty evidence. The historical schema dumps
below predate this additive extension; see the current OpenAPI endpoint for full schemas
and [observability documentation](../observability/README.md) for semantics and privacy.

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

## The LLM account

The server's, from `backend/.env` (`OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL`).
There is no header, body field or query parameter that supplies one per request: a caller
cannot choose the key, the endpoint or the model.

A bring-your-own-key path shipped briefly and was removed on 2026-08-19 -- see
`docs/decisions.md`. Two reasons, either sufficient: a caller-named endpoint is an address
this server is then made to call, which is SSRF, and "OpenAI-compatible" is a family of
dialects rather than one contract, so no form field can make an arbitrary provider work.

Consequences for a client: nothing to configure, and `MAX_PLANS_PER_DAY` is the ceiling
that bounds what any caller can spend of the operator's money -- model tokens, and the
Google Places and Routes quota a run spends alongside them.

---

## `GET /health`

Liveness check. No auth, no body.

```json
{ "status": "ok", "app": "Wandergent" }
```

It answers without touching the model, the maps APIs or a database, so a healthy reply
means the process is up and its config parsed -- not that planning works. It deliberately
says nothing about the LLM account: anyone who can reach the port can read this.

---

## `POST /plan`

Natural-language trip request -> validated itinerary. Synchronous and slow: typically 2 LLM
calls plus tools, up to 4 with a repair round. **Budget 15–60 s** and raise the OkHttp read
timeout — the 10 s default cuts it off mid-flight.

### Request

```json
{ "message": "3 days in Los Angeles next week, budget 900 USD, I like food and museums, no hiking" }
```

| Field | Type | Rules |
|---|---|---|
| `message` | string | required, 1–2000 chars |
| `currency` | string | optional, ISO 4217 (`"USD"`) or empty; anything else is a 422. The plan is **estimated** in it, never converted into it — a conversion would need an FX source, and a stale rate makes an estimate look precise. Empty lets the model use the destination's local currency, which is what callers got before this existed. |
| `previous` | itinerary or null | The plan to revise. Omit for a new trip. A message beginning with `New trip` starts fresh even if an older client sends one. |
| `previous_constraints` | `TripConstraints` or null | The request-owned constraint snapshot returned with `previous`. This is the budget/date/party/mode authority across revisions; the itinerary is not. |
| `constraints` | `TripConstraints` or null | Optional structured confirmation from a trusted UI: `budget`, `currency`, `start_date`, `end_date`, `days`, `travelers`, `allowed_modes`, `lodging_arranged`, `arrival`, `hotel_stays`, `journeys`. Explicit values override the narrow parser; unknown fields are a 422. |
| ~~`user_id`~~ | — | **Removed.** Identity comes from the `Authorization: Bearer <token>` header, never from the body: a body field is a claim the client makes, and anyone could claim anyone else's id. A request with no token is anonymous — nothing is recalled and nothing is stored. |

### Response `200`

```json
{
  "run_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "feedback_available": true,
  "constraints": {
    "budget": 900.0,
    "currency": "USD",
    "start_date": "2026-09-14",
    "end_date": "2026-09-15",
    "days": 2,
    "travelers": 2,
    "allowed_modes": ["WALK", "TRANSIT"]
  },
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
            "travel_mode": null,
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
            "travel_mode": null,
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
            "travel_mode": null,
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
            "travel_mode": null,
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
      "code": null,
      "cache_status": "miss",
      "cache_age_seconds": null,
      "duration_ms": 412,
      "attempts": 1,
      "contributed": true
    }
  ],
  "tool_usage": {
    "requested_calls": 1,
    "executed_calls": 1,
    "cache_hits": 0,
    "run_cache_hits": 0,
    "shared_cache_hits": 0,
    "failed_calls": 0,
    "retried_calls": 0,
    "contributed_calls": 1,
    "dropped_calls": 0,
    "duration_ms": 412,
    "calls_by_tool": {"get_weather_forecast": 1},
    "cache_hit_rate": 0.0
  },
  "warnings": [],
  "raw_reply": null,
  "validation": {
    "violations": []
  }
}
```

### Field notes for the client

- **`itinerary` can be `null` on a `200`** — the model could not produce a valid plan. A `no_itinerary` warning says so, `raw_reply` carries what it said. Render as a failure state; do not crash on the null.
- **`constraints` is request-owned state.** Send it back as `previous_constraints` with a revision. The validator checks its budget, currency, dates, day count, party size and allowed travel modes after every generation and repair. The model cannot loosen it by changing fields in `itinerary`.
  - Only request-owned `lodging_arranged: true` exempts overnight plans from choosing accommodation. A model-generated note claiming a booking cannot establish it. The narrow parser accepts explicit confirmations such as `lodging is already arranged`; revisions inherit that confirmation, while a new trip resets it. `null`/`false` require lodging for overnight plans.
  - The prose parser only confirms narrow, explicit forms such as `budget 900 USD`, `3 days`, `2 travelers`, ISO dates and `no driving`. A trusted client can send other confirmed values in `constraints`. Contradictory `days` and date ranges are a `422`, not competing instructions for the model.
  - Optional timing facts are `arrival`, `hotel_stays` and `journeys`. Every timestamp requires an ISO 8601 UTC offset. `arrival` has `at`, `location` and `buffer_minutes` (default 60). A hotel stay has a unique `id`, `hotel`, `check_in` (earliest room access), `check_out` (latest departure), `check_in_minutes` (default 30) and `check_out_minutes` (default 15). Early check-in requires changing the request-owned window; a model note cannot establish it.
  - A journey has a unique `id`, `origin`, `destination`, `mode` (`FLIGHT`, `TRAIN`, `DRIVE` or `TRANSIT`), `departure`, `arrival`, departure/arrival buffer minutes (default 0), and `timing_confidence` (`estimate` by default, or `confirmed` for user-supplied booking times). Supply realistic buffers explicitly. UTC arrival must follow departure, even when the destination's local date is earlier. These times are not independently verified against flight or train timetables.
  - Narrow text templates are `Arrival: Tokyo, 2026-11-02T10:00+09:00`, `Hotel h1: Tokyo, check-in 2026-11-02T15:00+09:00, check-out 2026-11-03T11:00+09:00` and `Journey j1: FLIGHT, Tokyo -> Los Angeles, 2026-11-02T01:00+09:00 -> 2026-11-01T18:00-08:00`. Structured constraints support buffers and confidence. Invalid or incomplete text is not a confirmed timing fact.
  - Timed activities include paired `start_at`/`end_at`, with `start_time`/`end_time` matching their local clocks. Group each activity on its local start date. Transport references `journey_id`; accommodation references `hotel_stay_id` plus `lodging_action` (`check_in`, `check_out`, `stay`). Booked journey endpoints cannot be moved by repair. Hotel windows, handling durations, overnight coverage, UTC overlaps and journey buffers are checked after generation and repair. Legacy activities without timing facts retain the same-day clock contract.
  - The server writes `itinerary.timing` from request-owned facts so saved/shared plans retain them. When `previous_constraints` is absent, revisions restore this snapshot. Explicit constraints remain authoritative; explicit null/empty lists clear facts and a new trip resets them. Client display shows both endpoint dates and offsets. New blocking codes are `arrival_conflict`, `hotel_window_conflict`, `journey_conflict` and `temporal_unverified`; `journey_time_estimated` is advisory. Transfer findings may also carry `departure_instant` and `arrival_deadline`.
- **`run_id`** is an opaque 32-character id for feedback. Show feedback controls only when `feedback_available=true`; anonymous runs and storage failures return false.
- **Costs are computed server-side** from the activities. Always present in responses, ignored if sent inbound.
- **`tool_calls` is an audit trail** in call order, with `ok=false` + a `code` when a tool degraded. A thin-looking plan is usually explained here.
  - `cache_status` is `miss` (executed), `run` (same planning run) or `shared` (successful read reused inside its TTL). Shared hits carry `cache_age_seconds`; `attempts` shows the bounded retry count and `duration_ms` measures actual execution. `contributed=true` means a returned date/place/route can be matched deterministically to the shipped itinerary; false means no use was provable, not that the model certainly ignored it.
  - Codes: `not_configured`, `timed_out`, `rate_limited`, `unavailable`, `no_match`, `no_coverage`, `bad_request`, `unknown_tool`. **The code is what crosses; the tool's own reason does not.** `no_match` is a valid empty search; `no_coverage` means the requested route mode is unavailable. Render your own wording from the code.
  - `error` still appears on trips saved before this change. Treat it as a legacy fallback, not a field to depend on.
- **`tool_usage` makes research efficiency comparable** without retaining arguments or upstream payloads: requested versus actually executed calls, per-run/shared cache hits, retry/failure/drop counts, deterministically observed contribution, total tool wall time and call composition. `cache_hit_rate` uses handled calls as its denominator; dropped calls remain visible separately.
- **`warnings`** are notes about the **run**, distinct from `itinerary.notes` (travel advice) and from `validation` (what is wrong with the *plan*). Those three lists do not overlap: until 2026-08-18 the validation findings were restated here as well, and every client had to suppress the duplicates by comparing sentences.
  - Each entry is `{"code", "detail", "budget", "dropped_calls", "dropped_tools"}`. **Render from `code`; `detail` is English aimed at whoever is debugging** — `reached the 16-call tool budget; skipped 3 further call(s) to search_places` is not something to put in front of a traveller. Fall back to `detail` only for a code you do not recognise.
  - Codes: `tool_calls_dropped` (the model asked for more calls than the budget had left, so some were never made — `dropped_calls` and `dropped_tools` say how many and to what), `tool_calls_spent` and `tool_rounds_spent` (a ceiling ended the research phase; `budget` is the ceiling), `no_itinerary`. All parameters are present on every entry, `null` or `[]` where they do not apply.
  - A client older than the server meets unknown codes, so treat `code` as an open string, never an enum that throws.
- **`highlights`** is a list of concrete specifics on an activity — dishes to order, exhibits worth the queue, the room type. **Often empty**; render it as an optional detail block, never assume it is populated.
- **`validation`** is the hard-constraint check on the plan as shipped: `{"violations": [{"code", "message", "day", ...}]}`. Empty = feasible.
  - Transfer findings carry `origin`, `destination`, `gap_minutes`, `needed_minutes`, `depart_at_minute` and `travel_mode`. `transfer_unverified` is advisory: the route service could not establish feasibility. `insufficient_transfer` is blocking. Measurements use the leg's declared mode, or walking when no mode was declared; a fast car route cannot clear a walking-only trip. Non-empty blocking findings mean repair did **not** fully succeed, so the client must not present the plan as sound. Localize off `code` (`constraint_mismatch`, `disallowed_transport`, `over_budget`, `time_conflict`, `insufficient_transfer`, `transfer_unverified`, `day_out_of_range`, `duplicate_day`, `unsociable_hours`, `overlong_day`, `empty_day`, `missing_accommodation`, `vague_venue`, `outside_opening_hours`, `understated_cost`); `message` is English text meant for the model.
  - `outside_opening_hours` is checked against the opening hours the run already fetched from Google during `search_places`, not against anything the model reported, so a plan cannot satisfy it by asserting. A venue the run never looked up gets no opinion rather than a closure.
- **Nullable in every response**: `itinerary`, `raw_reply`, `budget`, `weather`, `location`, `travel_mode`, `indoor`, activity `notes`. A non-null `Boolean` for `indoor` will throw — "unknown" is a real value.
- Dates are ISO `YYYY-MM-DD`. `start_time` / `end_time` are local wall-clock `HH:MM`, 24-hour, no timezone — **not instants**.
- `category` ∈ `sightseeing` `food` `transport` `accommodation` `activity` `rest` `other`. Unrecognised values are coerced to `other`, so the set is closed.
- `travel_mode` is null or one of `WALK`, `TRANSIT`, `DRIVE`. Transport activities should set it; it is preserved when the client sends a plan back for revision.

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
| `tool_result` | `name`, `ok`, `code` | That tool returned; `ok=false` means degraded, not fatal. `code` is from the closed vocabulary above |
| `composing` | `message` | A place name recognised in the partially-written itinerary |
| `validation` | `violations` | The hard-constraint check ran. Empty list = passed. May appear twice: once failing, once passing after repair |
| `result` | `result` | **Terminal.** Same `PlanResult` shape `/plan` returns |
| `error` | `message` | **Terminal.** The run failed |

```
event: tool_call
data: {"type":"tool_call","name":"get_weather_forecast","arguments":{"city":"Los Angeles","start_date":"2026-09-14","end_date":"2026-09-15"},"message":null,"ok":null,"error":null,"result":null}

event: tool_result
data: {"type":"tool_result","name":"get_weather_forecast","ok":true,"code":null,"message":null,"arguments":null,"result":null}
```

### Client notes

- **Errors arrive as an `error` event, not a status code** — the response committed to `200` before the tool failed. Treat it as fatal.
- **Exactly one terminal event** (`result` or `error`) ends a run. A stream closing without one means the connection dropped.
- **Ignore unknown `type` values**, do not error — later phases add event kinds and older clients must keep working.
- The same tool can appear twice (a later round). Match a `tool_result` to the oldest still-pending call with that `name`. An identical repeat is answered from a per-run cache but still reported, so the trail stays honest.
- `search_web` streams like the Maps/weather tools. It returns source URLs, excerpts, optional page dates and a collection timestamp. Missing `BRAVE_SEARCH_API_KEY` returns `not_configured`; failed searches never count as verified research. The planning tool registry no longer exposes `remember_preference`.
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

`PlanRequest` has no `user_id`. Planning is stateless with respect to durable preferences;
the current request and explicit trip/continuation context supply preferences instead.
A request with no token, or a lapsed one, plans anonymously rather than failing.

## Saved preferences

`GET /preferences` requires a bearer token and returns all saved preferences for that
account (up to 50), newest first. An empty list means none are saved; `401` means no valid
session, `503` means the store is unavailable. Success responses use `Cache-Control: no-store`.

`DELETE /preferences/{preference_key}` takes the `id` from the list and returns `204`.
Deletion is scoped to the authenticated account and idempotent, including an unknown id.
The id is a SHA-256 digest of the exact text, keeping preference text out of access-log URLs;
it is not authorization. Requests never accept a user id. Storage failure returns `503`.
These endpoints manage legacy records only. The skill-based planner neither recalls nor
writes them; deletion does not change existing itineraries or active runs.

## Travel Planner guide

`itinerary.travel_guide` is nullable for compatibility with older plans. A new proposal
uses it for `trip_summary`, `assumptions`, `budget_notes`, `ticket_notes`,
`transportation_notes`, `food_notes`, `free_paid_notes`, `packing_checklist`,
`practical_cautions`, `preparation_timeline`, `review_notes` and `source_urls`.
All fields except the summary are string lists. Dynamic notes cite supporting URLs
and collection dates; unsupported facts must be labeled as estimates or unknown.
`source_urls` is filtered server-side to successful observed tool sources in the current
run. Search excerpts do not prove complete page review or current booking inventory.

Each day also has `fallback_options`, a string list of rain/closure/overrun/low-energy
alternatives. These are not scheduled activities and do not contribute to cost totals.
Both fields survive saved/shared plan serialization and revision requests. Older clients
may ignore them; current Android renders every populated section. Wanderlog is disabled.

## Feedback

`POST /feedback` requires a bearer token and returns `204`. It accepts feedback on the
whole run:

```json
{
  "run_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "helpful": false,
  "category": "validator_miss"
}
```

For one activity, add the zero-based pair `day_index` and `activity_index`; they must
appear together and must name an activity in that run. Categories are `model_error`,
`tool_error`, `stale_data`, `validator_miss`, `client_error`, and `other`. Unknown runs,
expired runs, cross-account ids and invalid activity indices all return `404`; malformed
input is `422`, storage failure is `503`, and a missing session is `401`.

The server keeps an allowlisted diagnostic snapshot for 30 days, capped at 100 runs per
account. It includes activity times, categories, estimated costs, travel modes, constraint
values, violation codes and the number of failed tools. It excludes the request, destination,
venue names, addresses, coordinates, tool arguments, upstream errors and model prose. Feedback
exports are unreviewed triage candidates; a person must reproduce and encode a regression test
or eval case before they enter the release gate.

Export the review queue locally with
`uv run python -m scripts.export_feedback --json .eval/feedback-candidates.json`. The export
contains no account id, run id or free text and stays outside the evaluator until it has been
reviewed and reproduced.

`SavedPreference`, extracted from the running app's `/openapi.json` on 2026-09-06:

```json
{
  "properties": {
    "id": {
      "type": "string",
      "title": "Id"
    },
    "text": {
      "type": "string",
      "title": "Text"
    },
    "created_at": {
      "type": "string",
      "format": "date-time",
      "title": "Created At"
    }
  },
  "type": "object",
  "required": [
    "id",
    "text",
    "created_at"
  ],
  "title": "SavedPreference"
}
```
