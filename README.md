# Wandergent

A travel-planning agent that turns a sentence into a feasible itinerary — and checks its own work.

> *"3 days in Los Angeles next week, budget $900, I like food and museums, no hiking"*
> → resolves the dates → checks the forecast → puts the Getty on the day it rains and the pier on
> the day it does not → validates budget, time conflicts and travel time between places →
> streams the whole process to an Android app.

Python + FastAPI + LangGraph backend, Kotlin + Jetpack Compose client, tools published over MCP.

In the Android app, **You → Saved preferences** lists what the planner remembers and lets
you delete individual preferences. Changes apply to future planning runs.

<!-- TODO: add a demo GIF of the streaming UI here -- it is the single most persuasive thing on this page. -->

---

## Why it is interesting

**The model does not get the last word.** Generated itineraries are checked by code — budget arithmetic, overlapping activities, travel time between places — and violations are fed back for repair. **The repair is then re-validated**, because a model saying it fixed something is not evidence.

**The request remains the authority.** Budget, ISO dates, duration, party size and allowed travel modes are kept outside the generated itinerary and checked after every generation and repair. A model cannot make a plan pass by raising its own budget field or switching a walking leg to a car.

**Costs cannot be faked.** Day and trip totals are Pydantic computed fields derived from the activities, so the model cannot make a plan look in-budget by mis-adding.

**Failure is data, not an exception.** Tools never raise; they return a typed degraded result the agent can plan around. A dead weather API produces a plan with a caveat, not a 500.

**Feedback becomes reviewed regression data.** Signed-in travellers can rate a whole plan or one activity. The server retains only an allowlisted, text-free structural snapshot for 30 days; exported reports remain untrusted until a person reproduces the issue as a test or eval case.

**Portable by construction.** The itinerary is plain JSON validated with Pydantic rather than provider-specific structured outputs — switching the whole system from OpenAI to Xiaomi MiMo took two env vars and zero code.

## Measured results

Each row is one change and what it did, with the date it was measured. They are deltas, not a
description of today's totals: the eval set has grown since (a revision case now runs two plans
per case), so the absolute figures describe the subset as it stood on that date.

| Change | Effect | Measured |
|---|---|---|
| Moved the itinerary schema into the system prompt — streaming is what exposed the stall | latency **57.6 s → 42.2 s (-27%)**, first place name at 22.8 s instead of 40.2 s | 2026-08-05 |
| Pruned the JSON schema shipped on every call | **13 → 9** LLM calls, **54k → 31k** tokens (-42%), eval checks unchanged at 16/16 | 2026-08-05 |
| Fast path: parse the turn that ends the tool loop as a candidate itinerary, instead of always running a separate emit stage | **3 → 2** LLM calls on the common path | 2026-08-05 |
| Taught the transfer rule that "X" and "a place near X" are the same place | 6 route false positives → **1**, and that one was genuine | 2026-08-05 |
| Built dynamic model routing, then measured it | **left off** — the effect was not separable from run-to-run noise | 2026-08-05 |

Every number came from the eval harness or a traced live run, not an estimate; the reasoning and
the rejected alternatives behind each are in [`docs/decisions.md`](docs/decisions.md).

**Today**: 9 live LLM eval cases cover single plans, memory, impossible budgets, one-off edits,
five chained edits and switching to a new trip. Each report records source and suite hashes,
pass/failure reasons, calls, tokens, elapsed time and operator-supplied cost rates.
The current tree passes 513 offline backend tests (one opt-in MCP test deselected), Ruff,
95 Android JVM tests and the debug APK build. The full live release gate remains at its last 7/9
run; the newer tool-planning smoke is 3/5 and remains red for two measured-transfer failures, so
the tool-efficiency acceptance claim has not been made.

## Architecture

```mermaid
flowchart LR
    A["Android · Compose"] -->|SSE| B["FastAPI"]
    B --> C{"LangGraph"}
    C -->|tool loop| D["Tools"]
    C -->|validate / repair| E["Constraint rules"]
    D --> F["Open-Meteo"]
    D --> G["Google Maps · Places / Routes / Static"]
    D --> H["Preference memory · SQLite"]
    E -.measure real-stop hops.-> G
    D -.republished.-> I["MCP server"]
```

The graph has seven nodes and three cycles: `gather → run_tools → gather` (bounded), `parse → emit → emit` (one retry), and `validate → repair → validate` (at most three repairs). Routing lives in small predicate functions; the nodes only do work.

The dotted edge back to Routes is the part worth noticing: the constraint layer's string heuristic only *proposes*. It also creates advisory candidates for the other real-stop hops, then measures a bounded set at the hour the traveller leaves, using the declared and permitted mode. A fast car route cannot clear a walking plan, and a failed measurement cannot silently declare the hop feasible.

MCP is a **second surface, not a replacement** — the agent keeps calling tools in-process, so no JSON-RPC round trip is added inside one process and per-request identity survives. The memory tool is deliberately not published, because that surface has no authenticated user.

## Quickstart

Requires Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/). No interpreter is pinned; uv reuses the one you have.

```bash
cd backend
uv sync
cp .env.example .env        # then put your key in OPENAI_API_KEY
uv run uvicorn app.main:app --reload
```

`GET /health` answers without a key. Planning needs the server's: `OPENAI_API_KEY`,
`OPENAI_BASE_URL` and `OPENAI_MODEL` in `backend/.env`. The endpoint is any
OpenAI-compatible one -- switching providers is those three lines and no code -- and the
account is the operator's alone: the app has no field for a key and the server accepts
none per request. See `docs/decisions.md` for why the bring-your-own-key version was
removed.

The model bill is not the whole bill: a plan also spends Google Places and Routes quota
(~9 + 7 calls of expensive SKU), so `MAX_PLANS_PER_DAY` is the ceiling that matters
before any public deploy.

```bash
uv run pytest -q                        # offline tests, no key needed
uv run python -m scripts.smoke_plan     # live end-to-end run (costs tokens)
uv run python -m evals.run              # LLM regression eval (costs tokens)
uv run python -m scripts.export_feedback --json .eval/feedback-candidates.json
```

Android:

```powershell
cd android
# Point JAVA_HOME at a JDK 17+; Android Studio's bundled JBR works and needs no install.
$env:JAVA_HOME="<your Android Studio>/jbr"
.\gradlew.bat installDebug
adb reverse tcp:8000 tcp:8000           # re-run after every unplug
```

Put your SDK path in `android/local.properties` (gitignored) as `sdk.dir=...`. The USB
tunnel is why the app targets `127.0.0.1` — no shared Wi-Fi and no inbound firewall rule.

Client checks (inside `android/`):

```powershell
.\gradlew.bat testDebugUnitTest assembleDebug assembleDebugAndroidTest
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb install -r app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
adb shell am instrument -w -r -e class com.wandergent.app.ui.ExperienceTest com.wandergent.app.test/androidx.test.runner.AndroidJUnitRunner
```

The Compose tests use isolated content and fake map images, without opening account or
Room stores or calling external APIs. In-place installation preserves existing app data.

## API

`POST /plan/stream` (SSE) is the primary path; `POST /plan` is the non-streaming fallback, and the
client degrades to it if the stream produces nothing. Full contract, dumped from the running app rather than
written from memory: [`docs/api.md`](docs/api.md).

## Status

Phases 1–3 are done and verified on a physical device: weather, Google Places and Routes tools,
streaming, constraint validation, preference memory, MCP, accounts and a shared-trip feed. Phase 4
(observability, caching, deploy) is in progress — Docker and compose have landed.

## Docs

| | |
|---|---|
| [`docs/decisions.md`](docs/decisions.md) | Every design decision, with the reasoning and the gotchas |
| [`docs/api.md`](docs/api.md) | The client contract, dumped from the running app |

## License

MIT — see [LICENSE](LICENSE).
