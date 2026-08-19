# Decision Log (ADR)

> Why things are the way they are. **Append only** -- superseded entries get marked, never rewritten.
> Keep entries tight: decision, the reason, the gotcha worth remembering. Nothing else.
> This file is the reasoning. The project's current state and its working rules are tracked
> separately and are not published.

---

## 2026-07-07 · Backend package manager: uv

Virtualenv + deps + lockfile all through uv; commands unified as `uv run ...` under `backend/`. All-in-one and fast.

## 2026-07-07 · Pin Python 3.12 — **superseded 2026-08-10**

Pinned because the local machine was on 3.14.4 (too new for some wheels). See "Drop the interpreter pin" below.

## 2026-07-07 · LLM via OpenAI SDK direct

- **Decision**: OpenAI SDK + Function Calling directly, no full LangChain stack. LangGraph refactor deferred to Phase 3.
- **Why**: multi-step stateful work needs controllable orchestration; high-level abstractions hide the prompt and are hard to debug.
- Config is env-only: `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL`.

## 2026-07-07 · Code comments in English; log highlights as you go

ASCII punctuation only, to avoid encoding surprises across editors/terminals/CI. Highlights go into `resume-highlights.md` **as they happen** -- the detail (numbers, what was rejected) is always lost when reconstructing later.

## 2026-08-05 · Weather is the first Phase 1 tool

Weather is a single lookup with a flat argument list, so it exercises the whole function-calling path -- schema, dispatch, failure feedback -- without dragging in geocoding, POI search and routing. Maps is the bigger surface and lands second, onto a loop that already works.

## 2026-08-05 · Weather API: Open-Meteo

No API key, so Phase 1 runs end to end without a signup step; resolves Chinese city names. Swapping in QWeather/OpenWeather later touches only `app/tools/weather.py`. Forecast horizon is 16 days -- which matters, see next.

## 2026-08-05 · Out-of-range dates degrade, they do not error

- **Decision**: beyond the 16-day horizon the tool returns `ok=false` with a readable reason and skips the network call. Past dates and reversed ranges behave the same; ranges straddling today are clamped forward.
- **Why**: "next month" is the common request, so an out-of-range date is normal input, not a fault. The prompt falls back to seasonal norms and records the gap in the plan's notes.

## 2026-08-05 · Portable JSON output over provider-specific structured outputs

- **Decision**: itinerary is plain JSON validated by Pydantic, with errors fed back for one repair round -- not OpenAI strict structured outputs.
- **Why**: `OPENAI_BASE_URL` is configurable, and compatible endpoints often lack strict mode. Validate-and-repair works everywhere and also catches semantic errors a schema cannot express (an activity ending before it starts).
- Costs are `computed_field`s derived from activities, so the model cannot make a plan look in-budget by mis-adding.

## 2026-08-05 · Fast path: accept the tool-loop's final turn as the itinerary

The system prompt already asks for JSON, so the turn that ends the tool loop is parsed as a candidate itinerary; the explicit emit stage runs only if it fails validation. **3 LLM calls -> 2** on the common path, with the emit stage still there as fallback.

## 2026-08-05 · Android toolchain: Studio's bundled JBR 21, no system installs

- **Decision**: build against Studio's bundled **JBR 21**, set per-command via `JAVA_HOME`; SDK path in the gitignored `local.properties`.
- **Why**: the global `JAVA_HOME` is JDK 8 and other projects depend on it. Per-command `JAVA_HOME` leaves no trace.
- A first check of default install paths missed the D: drive and wrongly reported "Android is blocked".

## 2026-08-05 · AGP 9.3.1 + Gradle 9.6.1, and Kotlin is built into AGP now

Three gotchas, all found as build failures rather than guessed:

1. Cached Gradle 9.3.1 is too old for AGP 9.3.1 (`NoClassDefFoundError: ProjectTypeBinding`); 9.6.1 fixes it.
2. **AGP 9.0+ ships Kotlin support built in** and hard-fails if `org.jetbrains.kotlin.android` is also applied -- the standalone plugin must be removed.
3. The Compose compiler is still a separate plugin (`kotlin.plugin.compose`) -- built-in Kotlin does not imply built-in Compose.

Generating the wrapper needed `include(":app")` temporarily commented out, since `gradle wrapper` loads AGP before the wrapper exists.

## 2026-08-05 · Hilt deferred

One screen, one ViewModel, one repository -- a DI container would be scaffolding around a single construction site, which the "no over-engineering" rule forbids. Constructors take defaults (`PlanRepository(api = Network.planApi)`), so adding Hilt later is additive. Still deferred as of Phase 2: both ViewModels are `AndroidViewModel`s built by the default factory.

## 2026-08-05 · Android DTOs are tested against a real backend payload

A Kotlin data class that compiles proves nothing about whether it parses what the server sends -- naming, nullability and computed fields are exactly where the two sides drift. Three traps pinned by tests: `itinerary` null on a 200, `indoor` tri-state, unknown fields from a newer backend not breaking an older client.

## 2026-08-05 · LLM endpoint: Xiaomi MiMo v2.5-pro

`https://api.xiaomimimo.com/v1`, model `mimo-v2.5-pro` (V2 series retired 2026-06-30). **Only two env vars changed, no code** -- the payoff from the portable-JSON decision. First live run: function calling worked, relative dates resolved correctly, fast path caught the itinerary directly.

## 2026-08-05 · First live end-to-end run

Request: `下周去成都玩3天，预算3000，喜欢美食和历史，不想爬山`. "下周" resolved to 2026-08-10..12; one weather call; days 1-2 forecast rain so indoor museums were front-loaded with the reason written into each day; "不想爬山" produced no hiking; costs computed by our code as 965 / 3000 CNY.

## 2026-08-05 · Client product shell before any account system

- **Decision**: tabs (规划/收藏/我的), Room saved plans, brand theme, timeline rendering -- all before accounts.
- **Why**: offline favourites need no backend, so this delivers the visible product jump without pulling PostgreSQL and auth forward. The tabs are the skeleton later features slot into.
- **Community feed deprioritised indefinitely**: CRUD + image storage + moderation, demonstrating none of the agent work this project exists to show.

## 2026-08-05 · Saved plans stored as documents, not normalised rows

One Room row holds the response JSON, plus denormalised columns (destination, dates, day count, cost) for the list view. An itinerary is always read whole and never queried by its parts, so shredding it would buy nothing while coupling the local schema to every backend model change. `decode()` returns null instead of throwing, so one bad row cannot take down the list.

## 2026-08-05 · Android toolchain follow-ups

- **KSP works under AGP 9's built-in Kotlin** -- the open risk after the standalone plugin was removed. KSP 2.3.11 + Room 2.8.4 build clean, confirmed by the database file appearing on the device.
- **`material-icons-core` is not managed by the Compose BOM** and needs an explicit version (1.7.8, that artifact's final release). Found as `Unresolved reference 'Icons'`.

## 2026-08-05 · Local placeholder accounts, built so the real thing drops in

- **Decision**: sign-in / sign-up against a local Room table, no server.
- **Why**: settles the parts that are hard to change later -- routing, session-as-source-of-truth, where the auth boundary sits -- while the throwaway part (a local user table) is ~60 lines.
- **This is not security, and the UI says so.** Passwords are salted + hashed so the repository's shape is right when swapped for a real backend, not because it protects a device someone is holding. Server-side verification, rate limiting, tokens, transport security: all missing by design.
- Login failure does not distinguish "no such user" from "wrong password" -- no reason to build the habit of leaking which accounts exist.
- **Session is the single source of truth for routing**: screens only sign in/out, `AppRoot` navigates in response. One code path for sign-in, sign-out and already-signed-in-at-launch, and the back stack cannot leak a signed-in screen. Routing waits for the persisted session, so a cold start does not flash the login screen.

## 2026-08-05 · Room migration v1 -> v2 done properly

A real `Migration(1, 2)` rather than `fallbackToDestructiveMigration()` -- saved itineraries are the one thing a user would be annoyed to lose, and the habit is cheaper to build now than to retrofit. Verified by dumping the migrated database on the device.

## 2026-08-05 · Streaming: one code path, not two

- **Decision**: `stream_plan` is the implementation and yields events; `plan_trip` drains it and returns the last `result`. `/plan` and `/plan/stream` share one orchestrator.
- **Why**: a parallel non-streaming loop guarantees drift, with bugs landing in whichever path has fewer tests. All 45 existing tests passed unchanged after the rewrite -- itself evidence behaviour was preserved.
- **Consequence**: the test fakes now model the real wire shape -- a turn arrives as many chunks, a tool call split across them (id and name first, arguments a few characters at a time, keyed by index). Reassembly is easy to get wrong and impossible to notice with a fake that hands over whole messages.

## 2026-08-05 · Streaming found a 27% latency bug that non-streaming hid

- **Symptom**: a second `composing` stage at 37.8 s -- the fast path failing, so the itinerary was generated twice.
- **Cause**: the JSON Schema was only attached in the emit-stage fallback, so on the first attempt the model had to guess field names. The Phase 1 "save an LLM call" optimisation was never actually firing.
- **Fix**: put the schema in the system prompt. **57.6 s -> 42.2 s (-27%)**, first place name at 22.8 s instead of 40.2 s.
- **Worth remembering**: the bug was invisible to `/plan`, which returned a correct itinerary either way. Instrumentation exposed a defect in the thing being instrumented.

## 2026-08-05 · Stream failures ride the stream

`stream_plan` raises `PlanningError` subclasses; the SSE endpoint catches them and emits a terminal `error` event, while `/plan` keeps mapping the same exceptions to 500/502/504. SSE commits to `200` before any work happens, so a status code is not available later -- keeping exceptions in the generator leaves status-code policy with the HTTP layer.

## 2026-08-05 · The client falls back to the non-streaming endpoint

If the stream dies before delivering a single event, the app retries once against `POST /plan`. SSE is the fragile part -- proxies buffer `text/event-stream`, captive portals mangle it -- and a late plan beats no plan. Verified accidentally with the adb tunnel down: it degraded through exactly this path.

## 2026-08-05 · Progress copy is observed, not simulated

The Phase 1.5 loading card cycled plausible stage text on a 6-second timer. It looked informative and was fiction. Every line is now an event: the stage entered, the tool called and how it came back, place names pulled from the partially-written JSON. A degraded tool shows as degraded rather than as a reassuring lie.

## 2026-08-05 · Constraint validation is code, not a prompt

- **Decision**: pure functions over the finished itinerary (`app/agent/validation.py`), no LLM. Violations feed back as repair instructions and **the repair is re-validated**, not accepted on the model's word.
- **Why**: "is this plan feasible" is arithmetic and interval logic -- deterministic in code, approximate in a model, and untestable if the model checks itself.
- **Shape**: `code` is the stable machine-readable contract (the client localises it); `message` is English diagnostic text written to be fed straight back. Nothing raises -- a violation is data.
- **One repair round, then honesty**: surviving violations still ship, but stay in `validation.violations` and surface as "仍有 N 处未解决" rather than implying the plan is sound.

## 2026-08-05 · The transfer rule is deliberately forgiving

- **Background**: the first live run flagged **six** `insufficient_transfer` false positives, all of the form "宽窄巷子" -> "宽窄巷子周边小餐馆". String equality cannot tell "next door" from "across town".
- **Decision**: same place if either name contains the other, either carries a proximity marker (周边/附近/内/nearby), or they share a two-character prefix. Biased towards false negatives.
- **Why**: a missed transfer yields a slightly optimistic schedule; a false one sends the agent to "repair" a fine plan, costing a round trip and usually making it worse. The six are now regression tests.
- **Stopgap** until the maps tool exists. After the fix the same request produced exactly one violation, a true positive, which the model repaired.

## 2026-08-05 · A repair prompt must not break the conversation

- **Symptom**: the first repair round returned the JSON *Schema*, field titles and all.
- **Cause**: the itinerary was already the last assistant message and the repair code appended a second copy -- two assistant turns back to back, a malformed transcript.
- **Fix**: append only the user instruction, and append the assistant turn *after* the repair streams.
- The malformed-repair fallback behaved as designed under a real failure: unparseable repair discarded, original plan shipped, violations surfaced as warnings.

## 2026-08-05 · Orchestration refactored to a LangGraph state graph

- **Why then, not in Phase 1**: the hand-written loop was right while the shape was unknown. Constraint repair added a **second cycle** -- with gather/tools *and* validate/repair, control flow became nested loops interleaved with the work, which is where an explicit graph earns its keep.
- **The graph**: `gather -> run_tools -> gather` (bounded), `parse -> emit -> emit` (one retry), `validate -> repair -> validate` (re-validation is an edge, not an afterthought). Routing lives in seven predicate functions; nodes contain only work.
- **LangGraph, not LangChain**: pulls in `langchain-core` for base types and nothing else. Prompts stay in the orchestrator in plain sight.
- **Event contract untouched** -- nodes emit through `get_stream_writer()`, `stream_plan` forwards. SSE framing, client and tests saw no change.
- **Criterion: all 74 pre-existing tests pass unmodified.** That is what makes a refactor checkable rather than hopeful -- and it caught a regression where the round-cap warning named the global default instead of the run's actual budget.
- `recursion_limit` is set from the round budgets, so a routing bug fails fast instead of spinning.

## 2026-08-05 · Memory: recall is free, remembering is the agent's job

- **Decision**: preferences are **read** by system-prompt injection (no LLM call) and **written** by a `remember_preference` tool called inside the existing loop (no extra call either).
- **Why**: a separate extraction pass would cost one more model call on every request forever. As a tool, *what is worth remembering* becomes agent judgement rather than a regex.
- **Verified live**: run 1 (成都, "不想爬山，喜欢逛博物馆") had the agent store both unprompted; run 2 (重庆, preference **not** mentioned) came back with three museums and no hiking.

## 2026-08-05 · The user id is injected, never a tool argument

- **Decision**: `call_tool` passes a `context` dict to tools whose signature accepts one; `user_id` lives there, not in any tool schema.
- **Why**: a `user_id` in the schema is a parameter the *model* fills in -- and a model that can name whose memory it writes can write into someone else's. Acceptance is by signature inspection at call time, so a monkeypatched test double behaves like the real tool.
- **Not authentication**: `user_id` arrives unverified. Honest for on-device accounts behind a dev tunnel, documented in `api.md`; a real token replaces it later.

## 2026-08-05 · Identical tool calls are replayed, not re-run

- **Symptom**: the agent called `get_weather_forecast` with identical arguments in three consecutive rounds and wrote three near-duplicate preferences.
- **Fix**: per-run cache keyed on tool name + sorted arguments. The reply says it is a replay -- silently returning the same payload invites another identical call.
- **Result**: three tool rounds collapsed to one; stored preferences went from three messy entries to two clean ones. The repeat is still reported to the client, so the trail does not lie.
- **Storage-level dedup**: primary key is `(user_id, text)`, so remembering the same thing twice is a no-op rather than a check-then-insert race. Semantic near-duplicates need embeddings -- Phase 5.

## 2026-08-05 · MCP is a second surface, not a replacement

- **The choice**: (a) the agent calls its own tools through an MCP client, or (b) the same implementations are additionally exposed as an MCP server. **(b).**
- **Why**: routing the agent's own calls through JSON-RPC would add a serialisation round trip *inside a single process* and break `user_id` context injection -- MCP has no channel for "who is this request for".
- **What it buys**: any MCP client (Claude Desktop, another agent, a notebook) gets the weather tool. One implementation, two surfaces.
- **Memory is deliberately not exposed** -- the MCP surface has no authenticated user, so a client could claim any id and read or pollute someone else's preferences. A test asserts it is absent.
- Only public parameters are exposed (`city / start_date / end_date`), keeping the injectable HTTP client and clock out of the published schema.
- **Verified with a real client session**, not an import check: a subprocess handshake, list, and call over stdio. Marked `mcp`, excluded from the default run.
- **Cost, stated plainly**: `mcp` pulls in 18 packages including `cryptography` and `opentelemetry-api` -- real weight for a capability the app itself does not consume, justified because tool reuse over an open standard is a declared goal.

## 2026-08-05 · Evals first in Phase 4

The headline Phase 4 item is "semantic cache + model routing", and both *change model behaviour*. Without a way to tell whether a cheaper path still produces good plans, that is not optimisation but guessing. Reading one smoke run found three real bugs, but it cannot answer "better or worse than last time" -- the only question that matters when tuning prompts.

## 2026-08-05 · Deterministic checks, no LLM judge

- **Decision**: assertions are plain functions of the result -- constraint report clean, budget respected, day count, which tools ran, excluded keywords absent from the schedule.
- **Why**: most harnesses reach for an LLM judge because "is this good?" has no objective answer. This project has a hard-constraint validator, a typed itinerary and a tool-call record, so the answer *is* objective -- and deterministic checks cannot themselves hallucinate.
- Token usage is captured (`stream_options={"include_usage": True}`), so each case reports calls and tokens. **A cost regression is a test failure**: `at_most_llm_calls(4)` is a check like any other.

## 2026-08-05 · The eval's first catch was a bug in the eval

- **Symptom**: two cases failed for scheduling excluded hiking and boat trips.
- **Reality**: the model had obeyed and *said so* -- "已避免爬山（如南山、歌乐山）". The keyword check scanned commentary too, so a plan explaining its compliance was marked non-compliant.
- **Fix**: scope keyword checks to the **schedule** -- titles, locations, categories -- not summaries or notes. Failures now quote surrounding text.
- **Worth keeping**: an assertion that cannot distinguish "did X" from "explained why it avoided X" is worse than no assertion.

## 2026-08-05 · CI is free; the eval is manual

- **Decision**: `ci.yml` on every push/PR contains nothing that costs money (lint, offline tests, MCP round trip, Android tests + debug APK). `eval.yml` is `workflow_dispatch` only.
- **Why**: the eval spends real tokens per run; on `push` it would bill for every typo fix. It is the right gate for prompt/tool/model changes -- a decision a human makes, so a human triggers it.
- **Details worth keeping**: `uv sync --locked` fails on a stale lockfile, so an uncommitted dependency change cannot pass CI and then break someone else's checkout. The MCP test gets its own job so a network failure cannot redden the offline suite. The eval report uploads `if: always()` -- the numbers matter most when the job fails.
- **Never executed on GitHub**, and as of 2026-08-10 the workflow files no longer exist and must be rewritten. The Android job's SDK package ids are the likeliest thing to need adjusting on a runner image.

## 2026-08-05 · First cost optimisation: prune the schema shipped on every call

- **Where the data pointed**: the itinerary JSON Schema was 65% of the system prompt (2784 of 4260 chars) and goes out on *every* call.
- **Decision**: drop `title` (the key already says it) and `default` (irrelevant when generating); **keep `description`**, which carries real constraints like "HH:MM, 24-hour". Losing those trades prompt tokens for repair rounds, which cost more.
- **Result**: schema 28% smaller; eval measured **13 -> 9 LLM calls, 54k -> ~31k tokens (-42%)**, quality unchanged at 16/16.
- **Honest caveat**: one baseline run and two after, so part of the call-count drop could be variance. The size reduction is certain; the call-count effect is plausible, not proven.
- **The bug this nearly shipped**: the first attempt stripped metadata by key name everywhere, deleting `title` from Activity's properties while leaving it in `required` -- because `title` is both a JSON Schema keyword and one of our field names. Pruning is now structural, with a regression test asserting every required field is described.

## 2026-08-05 · Model routing: built, measured, and left off

- **Design**: the first turn only reads the request and picks tool arguments, so it can run on a cheaper model. `tool_choice="required"` makes the swap safe -- the cheap model is *only able* to emit a tool call. Every turn that writes or repairs the itinerary stays on the strong model.
- **Endpoint reality, checked not assumed**: `mimo-v2.5-flash` / `-lite` do not exist on this account (400). `mimo-v2.5` is the base model and honours forced tool calls.
- **Measured, answer was "cannot tell"**:

  | | run 1 | run 2 | checks |
  |---|---|---|---|
  | routing off | 9 calls / 29.9k | 9 calls / 32.2k | 16/16, 16/16 |
  | routing on | 12 calls / 47.4k | 8 calls / 27.2k | 16/16, 16/16 |

  Routing moves 11-20% of tokens to the base model -- real. But routed runs varied more (8-12) than unrouted (9, 9), so the effect on round count is **not separable from noise at two runs per arm**.
- **Decision: keep the mechanism, default it off.** `FAST_MODEL` is empty by default; the code and its four tests stay, so turning it on is one line. Settling it needs 5+ runs per arm -- a deliberate spend at ~30k tokens and 5 minutes each.
- The eval's job here was to stop a plausible optimisation from shipping on vibes. "Built it, measured it, turned it off" beats a 20% claim the data does not support.

## 2026-08-05 · The device reaches the backend over `adb reverse`

App targets `127.0.0.1:8000`, bridged by `adb reverse tcp:8000 tcp:8000` over USB. No shared Wi-Fi, no firewall hole, backend stays on loopback -- and it removes a whole class of "works on my network" failures. **The tunnel does not survive an unplug.**

## 2026-08-10 · Work is confined to the project folder

- **Decision**: only the repository root may be touched; sibling directories and the rest of the drive are off limits **including reads**. Out-of-bounds items go to `PENDING-APPROVAL.md` (local only) for per-item approval while in-boundary work continues.
- **Why**: this machine hosts other projects whose toolchains are in use (a global `JAVA_HOME` on JDK 8, other Gradle consumers). A blast radius of one folder makes the rest safe by construction.
- **Standing exception**: build-tool caches under `%USERPROFILE%` written as a side effect of a documented command. Installing an SDK/JDK or writing elsewhere still needs approval.
- **Never done at all**: registry, system settings, services, PATH / global toolchain, other projects' data.

## 2026-08-10 · Drop the interpreter pin

- **Background**: the environment had been rebuilt -- `uv`, `.venv` and `uv.lock` gone, machine Python now 3.13.15. The 2026-07-07 premise no longer held, and with no lockfile present re-deciding cost nothing.
- **Decision**: delete `.python-version`; `requires-python = ">=3.11"` is the single contract; add `python-downloads = "never"`.
- **Why**: a pin file **asserts** a supported version, a CI matrix **proves** one (3.11 + 3.13 -- declared floor and actual dev version). The place a version must be nailed down is the Docker base image, not a dev machine. `never` stops uv quietly fetching a ~30 MB managed CPython outside the project folder.
- **Verified**: no 3.12+ syntax in the codebase, so `>=3.11` is accurate. Then live -- `uv sync` built on the machine's own 3.13.15 with no download; **102 offline tests + 1 MCP test pass, ruff clean**; every dependency had 3.13 wheels.

## 2026-08-10 · CLAUDE.md carries only what cannot be read off the codebase

- **Background**: `CLAUDE.md` had grown to ~190 lines, absorbing a progress log, a mirror of this file, and a 45-line directory tree. That tree alone had **three factual errors**, including a CI block describing files that no longer exist.
- **Decision**: `CLAUDE.md` keeps commands, invariants, conventions and prohibitions. Progress moved to `progress.md`; decisions live here with no summary mirrored back; the tree was deleted for a short "only the non-obvious" list.
- **Why**: every line is re-read on **every turn**, so content `ls` would answer earns nothing. And a hand-maintained mirror of the filesystem **drifts silently -- drifted content is worse than absent content**, because it is asserted with the same authority as the rules.
- **Result**: 190 -> 141 lines, commands and hard rules intact, plus a new "Invariants" section that previously existed only as tribal knowledge in this file.

## 2026-08-10 · Docs are terse by default

- **Decision**: `status.md` + `roadmap.md` merged into `progress.md`; every doc trimmed to its load-bearing content. Going forward, markdown is written short -- claim, reason, gotcha, nothing else.
- **Why**: the same failure mode as CLAUDE.md. Long docs are skimmed, and skimmed docs drift; every restated premise and rhetorical flourish is a line someone must read to find the one that matters.
- **Result**: `docs/` 88 KB -> 58 KB (-35%) across one fewer file -- `decisions.md` 41 -> 27 KB, `resume-highlights.md` 25 -> 17 KB, `status.md` + `roadmap.md` 12 KB -> `progress.md` 4 KB. No decision, number or gotcha dropped.

## 2026-08-10 · Make the plan commit to real places, and pay for it

- **Complaint, from a real device run**: a three-day Los Angeles plan with **no hotel at all**, whose breakfast then read "the hotel or a nearby cafe" -- referring to lodging the plan had never chosen. Meals were "晚餐@美食广场 / Grand Central Market 或类似美食广场". Not one dish named, despite the traveller saying they came for the food.
- **Root cause**: nothing asked for any of it. `accommodation` was a valid category no rule required; the prompt never forbade hedging; and there was nowhere structured to put a dish recommendation, so `notes` carried ticket prices instead.
- **Decision**, four parts:
  - **Schema**: one new field, `Activity.highlights: list[str]` -- 2-4 named specifics. One field, not four, because it ships in every system prompt.
  - **Prompt**: name the actual venue; never hedge ("or similar", "or nearby"); a trip with nights needs an accommodation activity; give highlights to whatever the traveller said they care about.
  - **Validation**: `missing_accommodation` and `vague_venue`, both enforced in code. The accommodation rule has an escape hatch -- saying in notes that lodging is arranged also satisfies it, because some travellers have a bed already. **Silence does not.**
  - **Eval**: `has_accommodation()` and `highlights_on("food")`, plus a `specifics` case built from the complaint.
- **Why highlights are measured but not enforced**: a plan without dish recommendations is still *feasible*, so failing validation over it would spend repair rounds on a matter of degree. Hard constraints stay hard; quality of detail belongs in the eval.
- **The hedge list is deliberately short**, the same bias as the transfer rule: "杜甫草堂附近川菜馆" is a hedge and is **not** caught, because 附近 alone legitimately places a named venue, and a false alarm sends the agent to repair a plan that was fine.
- **Measured cost, and it is not small.** Same three cases as the recorded baseline:

  | | baseline | after | |
  |---|---|---|---|
  | budget-tight | 3 calls / 9.5k | 4 calls / 18.3k | |
  | exclusions | 3 calls / 12.2k | 3 calls / 11.5k | |
  | memory-recall | 3 calls / 10.6k | 4 calls / 15.7k | |
  | **total** | **9 calls / 32.2k** | **11 calls / 45.6k** | **+41% tokens** |

  Quality held: 4/4 cases, 22/22 checks, including the new `specifics` case.
- **What is certain and what is not**: the schema grew 2007 -> 2235 chars (+11%) and the prompt gained four rules -- both deterministic. The +2 calls are **one run per arm**, and this project has already documented run-to-run swings of 8-12 calls on the same input. The mechanism is plausible (two cases gained exactly one round, which is what a constraint repair looks like) but is **not proven** by a single run.
- **Accepted anyway.** This undoes roughly half of the 2026-08-05 schema-pruning win, and it is still the right trade: that optimisation bought tokens at no quality cost, and this spends tokens for the thing the product exists to do. Worth watching: `budget-tight` now hits its `at_most_llm_calls(4)` ceiling exactly, so one more round turns a cost regression into a test failure -- which is the check working.
- **What this does not fix**: specificity is not accuracy. "小龙坎火锅（春熙路店）" is still generated from model memory with nothing verifying the place exists. Making the model more specific makes it **more confidently wrong** when it is wrong. The map/POI tool is the real answer and is still blocked on the provider choice -- that decision is now the ceiling on plan quality, not just on map rendering.

## 2026-08-10 · A half-declared colour scheme silently ships Material's purple

- **Symptom**: the app looked incoherent on device -- teal timeline dots and amber costs sitting on **lavender cards** inside a **pink-grey nav bar**. It read as bad taste, and the first instinct was to restyle.
- **Cause**: `lightColorScheme()` / `darkColorScheme()` keep Material's **baseline (purple) values for every role you do not pass**, and the roles that were missing are the ones covering the most pixels. `Card` paints `surfaceContainerLow`; `NavigationBar` paints `surfaceContainer`. The theme set `surface`, `primary`, `secondary`, `tertiary` and `surfaceVariant` -- and nothing that large surfaces actually use.
- **Decision**: declare every neutral role explicitly in both schemes -- the five `surfaceContainer*` steps, `surfaceDim` / `surfaceBright`, `outline` / `outlineVariant`, `inverseSurface` / `inverseOnSurface` / `inversePrimary`, `surfaceTint`, `scrim` -- tinted towards the teal so bars and cards belong to the same palette.
- **Worth remembering**: a partially declared design token set does not fail, it **falls back**, and the fallback is a different brand. Nothing in the build or the tests can see this; only running the app can. It survived from Phase 1.5 to Phase 4 because every check that existed was green.
- **Also**: an earlier read of the same screenshot called the amber cost figure "a colour used nowhere else". It is `tertiary`, deliberately reserved for money everywhere it appears. Reading the palette before criticising it would have caught that -- and did catch the real bug.

## 2026-08-10 · Maps: Google, server-side only

- **Decision**: Google Maps Platform, with **one server-side key** for Geocoding, Places (New), Routes and Maps Static. The Android client never talks to Google; it receives what the backend produces.
- **Why one key and not the two originally planned** -- measured before deciding:
  - `maps.googleapis.com` answers from this machine in **0.47 s**, so the server-side Web Service APIs are fine.
  - The test device (`QK1809`, Android 8.1) has **no Google Play services**, so the Android Maps SDK cannot run on it at all. Not a configuration problem, a missing dependency.
  - Rendering server-side sidesteps both: no GMS needed, nothing in the APK, and the key never leaves the server. What started as a workaround is the better architecture.
- **Probe before building**: `scripts/probe_maps.py` hits each API once. It found Routes returning **200 with no route for TRANSIT** even with a departure time, while WALK (5144 s) and DRIVE (730 s) work. Finding that in a 3-second probe rather than in tool code was the whole point of writing it first.
- **So transit is not offered.** The tool's `enum` is WALK/DRIVE and an explicit refusal explains why. An unreliable capability is worse than an absent one: a client reading the empty transit answer would conclude "no route exists".
- **`search_places` is the point.** Before it, every restaurant and hotel in a plan came from model memory -- sometimes right, never checked. The 2026-08-10 specificity work made plans *more* specific, which made a wrong guess *more* confident. This is what turns a guess into a fact. (The probe's first hit, `Kushikatsu Daruma`, is exactly the restaurant a live plan had guessed the day before. It was right -- and there was no way to know that.)
- **An empty search result is `ok=True`.** "There is no such place here" is what the planner needs to hear, and retrying will not change it. Only failures the caller could recover from are `ok=False`.
- **No new cache.** The orchestrator's per-run replay cache (keyed on tool name + sorted arguments) covers the new tools for free. Places and Routes are the expensive SKUs, so a **cross-run** cache is worth building -- with Redis in Phase 4, not now.

## 2026-08-10 · Scope: international trips only

- **Decision**: the product plans trips **outside mainland China**. Domestic Chinese destinations are out of scope, and the eval case set was re-pointed accordingly (Osaka, Bangkok, Lisbon, Istanbul, Singapore, Seoul, Kyoto).
- **Why it had to be decided rather than drifted into**: the maps tools are Google-backed, and Google's POI data inside mainland China is thin. A live `search_places` for 串串香 in 成都 returned three real but obscure venues with **3, 1 and 1 reviews**; the same tool in Osaka returned Kushikatsu Daruma with **5200**. Five of the seven eval cases were Chinese cities, so the suite was grading a market the product does not serve, using the data source weakest there.
- **Requests stay in Chinese** where they were. The user is a Chinese speaker planning Kyoto, not an English speaker -- the language of the request and the location of the trip are independent, and the plan's language follows the request.
- **What this closes**: the standing question of whether to add Amap for domestic coverage. Not needed under this scope. The tool interface would make it a drop-in if the scope ever widens.

## 2026-08-10 · Transfer checks: the heuristic proposes, measurement disposes

- **Background**: `_same_place` decides whether two activities need travel time by comparing location *strings* (containment, proximity words, a shared two-character prefix). It is deliberately forgiving because a live run produced six false positives in one plan. Forgiving also means it misses real ones, and it was the last stopgap left over from having no maps.
- **Decision**: keep the heuristic as a **pre-filter**, and confirm each `insufficient_transfer` it raises against a real travel time (`app/agent/transfers.py`). Cleared violations are dropped; surviving ones carry the measured number into the repair instruction.
- **Why not measure every pair**: one Routes call per consecutive pair would be dozens per plan on the expensive SKU. Only the flagged pairs get measured -- historically one to six per plan.
- **Walking and driving, shortest wins.** A hop that is a 90-minute walk and a 5-minute taxi is not an infeasible schedule, it is a schedule with a taxi in it.
- **Everything degrades to the old behaviour**: no key, a timeout, an unroutable pair, or one lookup raising while others succeed -- the original heuristic violation stands. Silence from the router is not evidence the schedule is fine.
- **The violation now carries its evidence**: `origin`, `destination`, `gap_minutes`, `needed_minutes`. That is what lets the measurement pass work at all, and it also fixed a diagnostic hole -- an eval failure used to read `insufficient_transfer, insufficient_transfer, insufficient_transfer`, which names a rule three times and says nothing about which hops. Same lesson the keyword checks learned on 2026-08-05, applied to `feasible()`.
- **Left for later, deliberately**: the heuristic could now be made *stricter*, since false positives get filtered downstream. Not changed in the same step as adding the filter -- one variable at a time.

## 2026-08-10 · I wrote an eval case that could not be passed

- **What happened**: re-pointing the cases at international destinations, `budget-tight` went from "成都 3 days, 800 CNY" to "大阪 3 days, **12000 JPY**" -- roughly 80 USD. The currency changed; the order of magnitude did not. Two hours earlier the same session had made **accommodation mandatory**, and one night in Osaka costs most of that budget. No plan could pass.
- **Why it matters more than the typo**: the case was still *reporting* something -- 12400 vs 12000, over by 3% -- so it read like a marginal agent failure rather than an impossible task. A red test that looks plausible is worse than one that obviously breaks, because it gets believed and then tuned around.
- **Fix**: 35000 JPY, tight but achievable. `exclusions` was left at 200 EUR: 218 vs 200 in Lisbon **is** the agent failing to trim, which is a real weakness and stays red.
- **The line held**: the temptation was to relax both budgets until the suite went green, which is fitting the test to the model. A budget case should be satisfiable by a competent planner and no looser.
- **Also from this run**: `at_most_llm_calls` raised 4 -> 7. Four was set before the maps tools existed; two runs since measured 5-7 (5,5,6,6 and 7,5,5,5). Seven accommodates observed behaviour while still catching a doubling.
- **Worth noting as evidence the other changes worked**: `memory-recall` went FAIL (three `insufficient_transfer`) to PASS after the measurement pass landed, and the new diagnostic turned an unactionable "insufficient_transfer x3" into `P18 Hotel Bangkok -> 991 Rama 1 Rd, 0 min gap, needs 11` -- a measured, genuine violation. Single runs, so evidence rather than proof.

## 2026-08-10 · Settlement currency: estimate in it, never convert into it

- **Decision**: the traveller picks a currency in the profile tab; new accounts default to **USD**. The chosen code goes to the backend on every request and the model estimates every cost in it, whatever the destination uses locally.
- **The alternative was conversion**, and it is worse: it needs an exchange-rate source, a refresh policy, and a decision about what to do when the rate is stale. All of that to turn one estimate into another estimate that *looks* precise. Asking the model for dollars costs nothing and is honest about what it is.
- **The rule is only sent when set.** It is prompt tokens on every call in a run, so an unset currency ships nothing and leaves the old behaviour intact -- which is also what any non-app caller of `POST /plan` gets.
- **Validated at the edge**: three letters or empty, else 422. The model should never be handed `"dollars"` or `"$"`.
- **Stored per account**, not globally: a second local account starts at the default rather than inheriting whatever the last person picked. It lives in DataStore rather than the Room user table -- a schema migration for one string is not worth it.
- **Symbols, not codes, in the UI** (`$120`, `¥12000`, `฿15000`), with the raw code kept as the fallback for anything outside the list -- a plan saved before this existed, or a backend that chose the local currency. `1200 MXN` is honest; a guessed symbol is not.
- **The picker says what it does**: "新行程的花费会按这个货币估算，已生成的行程不受影响". Users reasonably assume a currency setting converts, and it does not.

## 2026-08-16 · A cut-off reply was being reported as a syntax error

- **What happened**: two consecutive live plans failed with malformed JSON, both stopping inside `highlights` -- once at `"cost":6,"highlights[`, once mid-array at column 4678. The repair round was sent the JSON parser's message, so the model was asked to fix punctuation in a document whose real problem was that it did not fit.
- **Root cause**: `stream_turn` read `delta` off each chunk and never `finish_reason`. `finish_reason == "length"` is the endpoint saying *I stopped you*, and that signal was on the wire the whole time, discarded.
- **Two fixes, and the order matters.** The cap is the fix: `max_tokens` was never sent, so the endpoint applied its own 4k default, which a multi-day plan with highlights overruns. `LLM_MAX_OUTPUT_TOKENS=16384` is now sent on every turn. Detection is the backstop: `parse_turn` swaps the parser's message for one that names length as the cause and gives the levers (fewer highlights, venue name instead of street address, drop notes).
- **The first emit attempt gets the hint too, not just the repair round.** A truncated tool-stage turn used to fall into "here is the schema, write the itinerary" with no mention of length -- which buys an identical reply cut in the same place, and the emit budget is two attempts.
- **Why the diagnosis was worth the time**: every visible symptom said "the model writes bad JSON". Three plausible fixes follow from that reading -- stricter prompt, JSON mode, more repair rounds -- and all three would have cost tokens and fixed nothing.
- **Absence is not truncation**: endpoints that never send `finish_reason` leave it `None`, and `truncated` is false. A missing signal must not be read as a bad one.

## 2026-08-16 · The planner is a chat, and it says so

- **Decision**: the plan tab is a transcript with a pinned composer, not a form above a result. Each round is an `Exchange` (request + `TurnState`), so history stays on screen and an earlier plan is a scroll away instead of overwritten.
- **The honesty problem, and the answer.** A chat UI invites "把第二天换成室内" and the backend takes one request, not a conversation -- so a follow-up is a fresh plan, not an edit. Rather than silently discard the implied context, the composer says `下一条会重新规划一份新行程，不会在上面那份上修改`, shown only once there is a plan above that a follow-up could be aimed at. Multi-turn revision was considered and deferred: it needs conversation history in the request, the orchestrator seeded from it, and an eval run to prove a rewrite cannot quietly break the budget or schedule constraints.
- **Retry and save moved onto the exchange.** They used to be screen-level singletons; with several plans on screen "the current plan" stopped being a thing. Retry re-runs in place, keeping its position in the conversation.
- **One run at a time.** Two concurrent plans would interleave their progress into the same transcript.
- **The user bubble is `primary`, not `primaryContainer`.** That role is `#6FF6FF` in this palette -- near neon, fine as a one-off accent, wrong as a block on every message. Seen on device, not guessed: the screenshot is why it changed.
- **Bubbles cap at 86% with `weight(fraction, fill = false)`**, so a short message stays short and no `dp` maximum is hardcoded. The plan itself stays full-width cards -- an itinerary squeezed into a speech bubble is worse than one that is not.
- **Still one free-text box.** No date picker, no budget field: prose is the input the agent is built for, and structured fields would just be a form again.

## 2026-08-16 · Settings, and a library that belongs to someone

First of several batches closing the gaps listed in the app audit.

- **Saved plans are per account** (Room v2 -> v3, `userId` column). Pre-account rows migrate to `userId = 0` and are **adopted by the first account that opens the library**, not hidden: a migration has no session, so it cannot assign a real owner, and making someone's saved trips silently vanish is worse than one device owner inheriting their own old plans.
- **Delete gets both a confirmation and an undo**, which is not redundant: the dialog catches the mis-tap before it happens, the snackbar catches the confirmed one regretted a second later. Neither covers the other case. `restore` re-inserts with the original id -- Room binds a non-zero PK as given even under `autoGenerate` -- so undo returns the row rather than a copy.
- **Theme and server address are device settings, currency stays per account.** The split is what the setting is *about*: currency belongs to the traveller, but the theme and the address have to be in force on the login screen, where no account exists yet. Both are read in `MainActivity`.
- **The backend address is a runtime setting, applied by an OkHttp interceptor.** Retrofit fixes its base URL at construction, so every request is retargeted in flight instead of rebuilding Retrofit on each change. Only scheme/host/port are replaced -- a path prefix is not honoured, which is fine for "which machine is the dev server on". A bare `192.168.1.10:8000` is read as `http://`: this talks to a dev box on a LAN, where https would be the surprising guess.
- **Verified on device against the real database, not just the UI.** The saved table was empty, so a `userId = 0` row was injected into the app's own SQLite file to exercise adoption -- and the copy had to include the `-wal`, since Room runs in WAL mode and a plain `cat` of the `.db` shows a stale snapshot. That first read said "0 rows" for a database that had one.
- **Still open from the same audit**: structured trip inputs, plan editing/reordering, share/export/calendar/booking links, map interaction, and app language. An embedded interactive map is not on that list -- the test device has no Google Play services, so the Maps SDK cannot run at all; the reachable version is full-screen zoom plus a hand-off to an external map app.

## 2026-08-16 · Interactive maps: built, measured, and shipped behind a fallback

- **The Android Maps SDK is out** (needs Play services) but the **Maps JavaScript API in a WebView is not** -- a WebView is a Chromium and needs no Play services. Built: `GET /day-map/interactive` renders a page, `InteractiveMapDialog` loads it when the day card's static image is tapped.
- **Stops are geocoded server-side and embedded**, so the page never calls Geocoding. That is what allows the key it carries to be restricted to map rendering alone.
- **This is the first Google key that reaches a device.** Everything else stays server-side. `GOOGLE_MAPS_BROWSER_KEY` exists to hold a Maps-JavaScript-only key so a leak cannot be spent on Places or Routes; unset falls back to the server key and logs a warning per request, which is a dev-only posture. JS API keys restrict by HTTP referrer, which a WebView can spoof -- a genuine step down from server-only, taken knowingly.
- **Verdict on the test device: it does not work, and now we know exactly why.** The bundle downloads (so TLS and network are fine) and then throws inside itself; `google.maps` is never defined. The WebView is **Chrome 62.0.3202.84** (Oct 2017), un-updatable without the Play Store. Any device with a Play Store has a current WebView, so this phone is the outlier, not the norm.
  - The first run only produced `Script error.` with no source or line -- the sanitisation browsers apply to cross-origin scripts, which at least proved the exception came from Google's code and not ours. Adding `crossorigin="anonymous"` to the bootstrap (Google serves the bundle with `Access-Control-Allow-Origin: *`) made the error name itself: an **`Uncaught SyntaxError`**. That upgrades the finding from "it fails on an old engine" to "the bundle uses syntax this engine cannot parse", which is not something a fallback or a pinned version can fix.
- **When the embedded map cannot run, the dialog degrades rather than apologises.** The app probes `google.maps` from Kotlin via `evaluateJavascript` after the page settles -- an HTTP-level success is not evidence of a working map -- and on `false` swaps in the static image with pinch-zoom, pan and double-tap. No JavaScript bridge was added to carry that signal: `addJavascriptInterface` would expose an app object to any page the WebView ever loads, and a one-line probe needs no such surface.
- **So the day gets three renderings, in order of what the hardware allows**: the static PNG on the card (works anywhere, one cheap request), the embedded JS map on tap (works on a current WebView), and a hand-off to `google.com/maps` in the phone's own browser (works here -- verified -- needs no key, no Play services, and is the only one offering turn-by-turn). The browser is newer than the system WebView on this device, which is why the third works where the second does not.
- **Diagnostics live in the page, not in logcat.** This ROM emits 13 lines of logcat total, so `?debug=1` prints the user agent and the failure reason on screen. Without it, the failure was indistinguishable from a blank rectangle. Debug builds set it automatically.
- **Not done: Map Tiles API + a non-Google renderer** (MapLibre/Leaflet). It would work on Chrome 62, but Google's terms restrict displaying Maps content outside Google's own renderers. A licensing risk, not a technical one.

## 2026-08-16 · The Maps key was being written to the server log

- **What happened**: `httpx` logs every request line at INFO, URL included. Places and Routes send the key as an `X-Goog-Api-Key` header and were never exposed -- but the Geocoding API only accepts it as a query parameter, so adding `geocode_places` put `key=AIza...` into the log on every call. Spotted in the backend's own task output.
- **Fix at the logger, not the call site**: `logging.getLogger("httpx").setLevel(logging.WARNING)` in `app/main.py`. A future query-string secret is covered by the same line and no caller has to remember. Guarded by a test.
- **Second leak, same root cause, different surface**: the debug panel echoes the failing script's URL, and browsers quote that URL in error messages -- so the key appeared on screen and would appear in any screenshot of a bug report. `diag()` now strips `key=...` on the way in.
- **The key in `.env` should be rotated.** It reached a plaintext log file on this machine. Rotating is a Google Cloud Console action, outside this repo.
- **Lesson worth keeping**: "the key never leaves the server" was true of the design and false of the logs. A secret's blast radius includes everywhere it is *written*, not just everywhere it is *sent*.

## 2026-08-16 · `uvicorn --reload` does not reload on this machine

- **Symptom, twice**: a route that exists in the file 404s, and an edited template keeps serving its old text. The second time, `/openapi.json` listed a `/day-map/points` route that exists nowhere in the repository -- a ghost from an in-memory module the worker had loaded earlier.
- **Compounding it**: two uvicorn processes were bound to `127.0.0.1:8000` at once. Windows allows the second bind, and requests went to the older one, so every device request for ~90 minutes hit a stale build.
- **Rule**: restart the backend explicitly after changing it; do not trust `--reload` here. And when behaviour disagrees with the source, check `netstat -ano | grep :8000` for more than one listener **before** debugging the code.
- **What this did and did not invalidate**: the truncation fix was verified through `scripts.smoke_stream`, which runs the orchestrator in-process, so that result stands. The on-device plan runs during the chat-UI work went to the stale server -- they exercised old backend code, though what they were verifying (client-side rendering) was unaffected.

## 2026-08-17 · The embedded map works. The blank rectangle was three stacked causes

Continues the 2026-08-16 interactive-map entry. **Verified on device: tiles, numbered markers, route line, pan, marker tap -> info window.**

- **Cause 1, the engine (fixed yesterday)**: WebView was Chrome 62; the Maps bundle uses `?.` (Chrome 80+) and died in the *parser* -- which is why no polyfill could ever help. Fixed by the verified-signature WebView 138 update.
- **Cause 2, my CSS**: `height: 100%` resolved to 0 in this WebView, so the map drew into a 360x0 box. Patched with `position: fixed` insets -- which worked only until cause 3.
- **Cause 3, the API fights the patch**: the Maps constructor writes inline `position: relative` onto the container. That killed the fixed insets, height collapsed to 0 again, and the API's own `overflow: hidden` clipped the whole subtree -- forced backgrounds, plain children, everything. **Final fix: explicit pixel height** (`window.innerHeight`, kept on resize). No percentage chain to resolve, nothing for `relative` to break.
- **Why it took a probe chain**: a clipped-to-zero subtree looks *healthy* to every ordinary check -- `getBoundingClientRect` returns laid-out coords, computed styles say visible/opacity-1, and my one size measurement ran *before* the constructor rewrote the container. The chain that cornered it: tiles loaded but canvases clean (`toDataURL` not tainted = never drawn) -> every probe I injected paints (small canvas, 512px canvas, translate3d, full-width fixed) -> so painting works and only `#map` contributes nothing -> forced magenta on `#map` itself invisible = zero painted area = zero size at paint time.
- **Dead ends, documented so nobody re-walks them**: GPU/accelerated-canvas suspicion (killed by the 512px canvas probe), hidden-page suspicion (`visibilityState: visible`, rAF at 60/s), worker-transfer suspicion (`getContext` did not throw), tile-host reachability (image probes OK). The 360 browser was no control group -- it bundles its own Chrome 62 core and reproduced the *old* SyntaxError.
- **Screenshot pixel-sampling as an instrument**: the decisive clue was that the blank area was `#eceff0` (our body) and not `#e5e3df` (Maps' own ground) -- proving the subtree painted nothing at all, not "painted but empty". Read off the PNG with a hand-rolled decoder because the ROM suppresses logcat and PIL was not installed.
- `debug=1` diagnostics stay in the page (opt-in); the app no longer auto-appends it.

## 2026-08-17 · Multi-turn revision: send the plan, not the transcript

Reverses the deferral in the 2026-08-16 chat-transcript entry. **Verified live on device: "change the lunch to a ramen shop" swapped one activity, preserved the rest verbatim, and re-validated.**

- **The unit of context is the itinerary, not the conversation.** `PlanRequest.previous` carries the plan being edited; the orchestrator wraps it into the user turn and appends `REVISION_RULE` to the system prompt. Replaying the original run's transcript would have cost far more tokens and dragged along tool calls and superseded drafts -- none of which is what the traveller is pointing at.
- **The service stays stateless.** The client already holds the plan it wants changed, so it sends it back. No session store, no plan ids, no expiry.
- **A revision takes the identical path**: tools, parse, validate, `confirm_transfers`, repair, re-validate. That is the entire point -- an edit that busts the budget or leaves ten minutes to cross the city is caught by the same code that caught it the first time. A chat window cannot do this, which is why it is the feature worth having over one.
- **Totals cannot be laundered through the round trip.** Day and trip costs are `computed_field`s, so the numbers the client sends back are ignored on the way in and derived again on the way out. Pinned by a test, because "the client posts a plan" is exactly the shape where a trusted total would sneak in.
- **Retry of a revision re-runs against the plan that preceded *it***, not the newest one -- otherwise retrying an edit applies it on top of its own output. Cheap to get wrong, invisible until someone taps retry twice.
- **The non-streaming fallback carries `previous` too**: degrading the transport must not silently downgrade an edit into a from-scratch replan.
- **"A different trip" is left to the model**, via one clause in the rule, rather than a UI toggle for something people would rarely find and often set wrong.
- **Observed cost shape**: the revision spent 2 tool calls (`search_places`, `get_travel_time`) against the first plan's 10 -- it looked up the replacement and re-checked the hop, rather than re-researching the city.
- **Owed**: the eval has no revision case, and adding one needs the harness to run two chained steps, not just a new case entry. Until then this is covered by unit tests plus one live run.

## 2026-08-17 · Grading an edit needs both plans

Closes the debt opened by the revision entry above, the same day.

- **Why a new check type at all**: every existing check is `PlanResult -> str | None`, and a revision cannot be graded from the result alone. A model told to swap one lunch can return a completely different, completely valid trip that contains ramen -- passing `produced_a_plan`, `feasible`, `within_budget`, `days` and a keyword check, while being the wrong answer. `RevisionCheck` takes `(before, after)`, which is the smallest change that can tell those apart.
- **`after(check)` composes rather than duplicates.** Instead of a parallel set of "revised plan" checks, one adapter lifts any existing check onto the edited plan. `after(feasible())` *is* the claim the feature exists to make: an edit is still constraint-checked.
- **`kept_most_activities` is the anti-regenerate check**, keyed on `(start_time, title)` -- either half alone is too loose, since a rewritten day keeps the times and a re-timed day keeps the titles. Floor is 0.6: a live swap left 7 of 8 untouched, so that leaves room for a knock-on retime without leaving room for a rewrite.
- **`trip_frame_unchanged` guards the grader itself.** Without it a revision that quietly widened the budget would make `after(within_budget())` pass for the wrong reason -- the check would be measuring against a goalpost the model moved.
- **The checks have their own tests.** A check that returns None on a good run is indistinguishable from one that always returns None, so each red case is pinned offline against a synthetic rewrite. Cheap, and it means the eval cannot quietly lose its teeth between token-spending runs.
- **Cost, measured not guessed**: the case is 10 LLM calls / ~72k tokens / 261 s for both plans. That roughly doubles the smoke subset, which is the price of covering the feature at all -- and it invalidates the recorded baseline, now flagged in `CLAUDE.md` and `progress.md` rather than silently compared against.

## 2026-08-17 · The revision base is any plan, not the live transcript

Closes the two gaps that made revision a demo rather than a feature: the plan vanished on restart, and a saved trip could not be edited at all.

- **One mechanism, both problems.** Rather than a "current plan" concept alongside the transcript, both paths *seed the transcript*: a restored round and a plan pulled from the library both become ordinary `Exchange`es. `latestItinerary()` then picks them up and send / retry / save / the revision label all work unchanged. The alternative -- a separate `reviseBase` field -- would have needed every one of those paths to know about it.
- **Only finished rounds persist** (Room v3 -> v4, `chat_turns`). A run that was streaming when the process died is not resumable -- there is no server-side session to reattach to -- so restoring it would restore a spinner that never stops. Errors are not stored either: the request that caused one is still on screen if the user wants to retry.
- **Bounded at 10 rounds per account.** Each row is a whole itinerary; unbounded, a heavy user restores a hundred plans into a lazy list on launch and decodes them all to draw the first.
- **Separate table from saved plans, deliberately.** Saving is "keep this trip"; the transcript is the working state of an edit in progress. Conflating them would either fill the library with drafts or lose the draft being edited.
- **The library -> planner handoff is hoisted state, not a nav argument.** The tab routes carry no arguments and an itinerary is far too large to put in one; `WandergentApp` already threads `currency` the same way.
- **"新对话" had to come with it.** Once every message edits the plan above, there must be an obvious way *not* to -- so the escape hatch sits next to the hint that creates the need for it.
- **Verified on device, including the part that only fails for real**: plan -> `am force-stop` -> relaunch restored the full itinerary with the revision hint live; the failed run from minutes earlier correctly did **not** come back. Migration checked against the real database (`user_version: 4`, `chat_turns` created, `users` and `saved_plans` intact).
- **A failed run proved the guard by accident**: the first attempt produced no itinerary, and `chat_turns` stayed empty -- which is exactly right, and is why the happy path needed a second live run rather than being assumed.

## 2026-08-17 · English throughout, and the two bugs that translating exposed

Interface, prompts, tool language defaults, eval cases and test fixtures are now English.

- **Translation was the easy half.** The interesting part is that several behaviours were *keyed on Chinese text* without saying so, and switching languages is what made them visible.
- **Bug 1 -- the transfer heuristic was silently script-dependent.** `_same_place` compared the first **two characters** of two location strings. Two characters is a meaningful root in Chinese ("锦里古街" / "锦里小吃街") and almost nothing in English: "Nara Park" and "Namba Parks" both start "Na", so the check would have declared them the same place and skipped the transfer check between cities an hour apart. Now `_place_root` measures per script -- two characters for CJK, the first whole word (min 4 chars) for Latin.
- **Bug 2 -- English hedges wear articles.** `HEDGE_MARKERS` was a substring list, which is enough for Chinese ("或类似" has no article) and not for English: the live phrase "Grand Central Market **or a** similar food hall" walked straight past a list containing "or similar". Caught by an existing test the moment its fixture was translated. Hedges are now a regex covering the article variants, with the false-positive cases ("Similan Islands", "Anywhere Cafe") pinned too.
- **The validators stay bilingual on purpose.** The model follows the language of the request, so a Chinese request still produces Chinese text that `vague_venue` and `missing_accommodation` have to be able to read. Dropping the Chinese markers would have made the checks silently stop firing for those users.
- **`DEFAULT_LANGUAGE` for Places/Geocoding is now `en`**, but stays a *parameter*: the right language for an address is not always the interface language -- one you have to show a taxi driver is more useful in the local script.
- **Test fixtures moved off 成都**, which was a latent inconsistency: mainland China has been explicitly out of scope since 2026-08-10, yet the shared fixture planned a trip there.
- **What English does *not* override: memory.** A live English request still returned a Chinese plan, and the cause was a remembered preference -- `旅行时使用中文` -- injected into the system prompt from earlier sessions. That is memory working exactly as designed (an explicit preference beats a default), not a translation miss. Worth knowing before anyone debugs it as one: to see the English path on an account with history, clear that preference or use a fresh `user_id`.

## 2026-08-17 · Memory dedup, and why the threshold is high rather than clever

- **The problem was restatement, not repetition.** The primary key already made a verbatim repeat a no-op. What accumulated was the same fact in different words -- "loves museums" after "loves museum", and across languages, "不想爬山" alongside "avoids hiking".
- **Jaccard overlap on normalised tokens, threshold 0.8.** No embeddings, no LLM judge, no new dependency -- which matters because this runs inside a tool call on the planning path.
- **The threshold is high because the errors are asymmetric.** A missed duplicate costs a few tokens against an already-capped prompt. A false one silently discards an *update*, and `"avoids hiking"` -> `"loves hiking"` is precisely the pair a looser threshold merges: one word apart, opposite meaning. At 0.8 only near-identical wording collapses. Both members of that pair are pinned in tests, along with vegetarian/vegan and morning/late starts.
- **Contradictions are stored, not resolved.** Two opposing preferences both survive; `recall` returns newest first and the prompt already says a new request wins. Deciding which of two beliefs is true is not something a string comparison should attempt.
- **Storage is now capped at 50 per account**, distinct from the recall cap of 20. Without it the table grew for the life of an account even though only the newest 20 were ever read. Trimming is oldest-first.
- **The check cannot see across scripts.** CJK text has no word boundaries for `\W` to split on, so a Chinese preference collapses to one token and only ever matches itself. That is a real limit, and it is why the cross-language pairs had to be resolved by hand once -- `scripts/consolidate_memory.py`, dry-run by default, mapping reviewable in the file. Now that preferences are written in English, the automatic check keeps up.
- **Verified live, not just in tests**: a run stating "I love food and museums, and I never hike" against an account that already knew all three called `remember_preference` and stored **nothing**. Before this, that was two or three more rows.

## 2026-08-17 · Three Google Maps gaps, one of them a wrong conclusion we had believed for months

Everything below was probed against the live API before it was written; none of it is from the docs.

- **Opening hours were the largest thing the agent could not know.** `regularOpeningHours` was simply not in the field mask, so a plan could schedule a museum on a day it is shut and *nothing downstream noticed* -- the constraint layer checks budget, timing and routing, none of which see a locked door. Added, with `businessStatus` alongside it. **Verified live**: asked for a Monday in Chicago, the agent put the Field Museum at 09:00 (opens 09:00) and the Art Institute at 14:10-17:00 (opens **11:00**, closes 17:00). It did not put the Art Institute in the morning, which is exactly what it could not have known before.
- **Permanently closed venues are filtered in code, not requested in the prompt.** Google keeps them in results because they are still real places; for planning they are traps. A prompt rule is a request the model can overlook, and a filter is a guarantee it cannot.
- **"Transit is not available" was wrong, and had been for months.** The module docstring said the Routes API "returned no route for transit even with a departure time". Re-probed: transit works in New York, San Francisco, Chicago, Boston, Seattle, New Orleans, Austin, Seoul, Bangkok, Singapore, Lisbon, Istanbul and Paris -- and returns nothing in Osaka, Kyoto and Tokyo. **Japan has no Google transit coverage through this API.** The original probe happened to use Osaka, so one regional gap was recorded as a global limitation, and the constraint layer spent months measuring city hops without the mode most travellers use. Callers now treat an empty transit answer as "not that way here" and fall back.
- **`computeRouteMatrix` was considered and rejected.** It looked like "one call instead of N", but it bills **per cell**: for k specific ordered pairs a matrix costs k² elements against k route calls. Fewer requests, more billing. Matrix wins for all-pairs problems; ours is strictly pairwise, so it would have been a pessimisation dressed as an optimisation. Cross-run caching remains the real cost lever.
- **Driving was free-flow fantasy.** Adding transit exposed it: every measured hop still resolved to DRIVE, because without `routingPreference: TRAFFIC_AWARE` the API answers as if the roads were empty. A "measurement" that is optimistic just launders the model's optimism. Now traffic-aware.
- **Known limitation, found by testing rather than assumed away.** The departure reference is a fixed 11:00 UTC, which is noon in London, **05:00 in Chicago** and 20:00 in Tokyo. For the Americas both transit and traffic land at their most flattering hour -- a Chicago crosstown hop measures 10 minutes by car, which nobody will experience when they actually travel. The honest fix is the destination's local time via the Time Zone API (one call per plan). Documented at the constant; the estimates are a lower bound until then.
- **Test and eval destinations moved to US cities** (Chicago, New Orleans, Boston), all with confirmed transit coverage, so the constraint layer is exercised on its real path rather than permanently on the fallback. The Japanese fixtures had the opposite property, which is how the transit gap stayed invisible.

## 2026-08-17 · The opening-hours check reads Google, not the model

Completes the half-feature from earlier the same day: hours were reaching the planner but nothing verified they were respected.

- **The check reads data the run already paid for.** `search_places` results are harvested into run state and handed to the validator, so `outside_opening_hours` is decided against Google's hours rather than the model's account of them. A plan cannot satisfy it by asserting, and it costs no extra API calls -- the alternative designs were a schema field the model fills (bookkeeping it would often omit, leaving a check that looks active and never fires) or a second lookup pass per venue (correct, and pays twice for the same fact).
- **Absence is never a closure.** Unparseable hours, a weekday the payload omits, a venue the run never searched, no maps key at all -- every one of those is "no opinion". The check exists to stop a plan arriving at a locked door; inventing closures would do the opposite while looking rigorous.
- **Venue matching needs six characters.** Names rarely match exactly -- "Lunch at Lou Malnati's" against "Lou Malnati's Pizzeria" -- so it matches on containment either way, longest wins. Below six characters a name like "Bar" would attach one venue's hours to every activity containing the word, and a wrong closure is worse than no check.
- **Parsing Google's human-readable hours had one genuinely subtle case.** "5:00 - 9:00 PM" is 17:00 and "11:00 - 2:00 PM" is 11:00, so an opening time with no meridiem cannot simply copy the closing one. The reading that works for both is the latest interpretation still before closing. It also has to happen *before* parsing, because "5:00" is valid 24-hour text on its own and would silently become a morning window that shuts nine hours early.
- **Verified live on the case it exists for**: asked for art museums in Chicago on a **Tuesday**, the day the Art Institute is closed. The plan chose the Museum of Contemporary Art instead and said so -- "The Art Institute of Chicago is the city's top art museum but is CLOSED on Tuesdays". The harvest seam was checked separately against a real serialised `PlacesResult`, because every hand-built test would keep passing if a field were renamed while the constraint quietly lost its data.

## 2026-08-17 · The fast path was defeated by a sentence

- **Observed, not theorised**: a live run answered "I have everything I need to build the itinerary." followed by a fenced JSON block. `strip_fences` only stripped fences when the text *started* with one, so parsing failed and the run paid for a whole second generation -- which is precisely what the fast path exists to avoid.
- **Now positional**: first `{` to last `}`, after any leading fence. Deliberately not a JSON scanner; that is the object for every shape seen in practice and anything cleverer would be guessing at malformed input the parser is about to reject anyway.
- **No shortcut for text already starting with `{`** -- a reply can be valid JSON followed by "let me know if you want changes", and returning it whole would fail to parse for the sake of skipping two string scans. That was a bug in the first version of this fix, caught by its own test.
- **An unbalanced object is left alone**, so a truncated reply still reports an error about its real content -- which is what the truncation check then reads.
- Seven wrapper shapes are pinned in `tests/test_fast_path.py`, including the one that cost the second call.

---

## Open (promote to a dated entry once decided)

- [ ] **Map SDK**: Google Maps chosen in principle (global trips, developing from the US), setup deferred. Needs a GCP project with billing, then two restricted keys (server + Android). Blocks the maps tool and map plotting.
- [ ] **User accounts**: whether to build PostgreSQL + auth + cloud-synced favourites, and when.

## 2026-08-17 -- Transfers are measured at the destination's local departure hour

The Routes API prices a route for a `departureTime`. Ours was a constant 11:00 UTC,
chosen for determinism. It is also 06:00 in Chicago and 20:00 in Tokyo: for the Americas
every drive came back free-flow and every transit answer off a skeleton timetable.
Measured: Art Institute -> Wrigley Field is 13 min at 05:00 and 29 min at 17:30. A plan
leaving a 20-minute gap was being *cleared* by a measurement, which is worse than not
measuring -- the constraint layer's whole claim is that a measurement beats a guess.

**Decision.** `Violation.depart_at_minute` carries the wall-clock departure from the
validator; `confirm_transfers` resolves the destination's UTC offset once per report
(`maps.local_utc_offset` = geocode + Time Zone API, two calls) and prices each hop at
that instant.

- **Offset resolution failing returns `None`, never a guess.** A wrong offset moves every
  measurement to the wrong hour *while looking fixed*; `None` falls back to the old
  reference, which is documented as optimistic.
- **Past dates slide forward in whole weeks**, not days. Google will not price a past
  departure, and the weekday is load-bearing -- a Sunday timetable is not a Tuesday one.
- **One offset per report, not per hop.** Every hop in a plan is in the same city.

## 2026-08-17 -- Two budgets bound the tool loop, not one

`max_tool_rounds` bounds *turns*. One turn can carry any number of tool calls, so a
confused model asking for fourteen searches at once stayed inside a four-round cap while
spending the latency and money of a much bigger run. `MAX_TOOL_CALLS = 16` bounds the
work; exhausting either ends the loop.

Dropped calls are named in a warning rather than silently truncated -- a plan built on
fewer answers than were asked for is a plan with a caveat on it.

**Separately: the model is now told when it is on its last round.** Twice in live runs
the loop simply went quiet mid-research and the run ended returning no plan at all. From
the model's side the tools just stopped answering. `LAST_ROUND_NOTICE` turns that into a
normal instruction -- ask for what is still missing, then write the plan from what you
have -- which is something it can actually follow.

## 2026-08-17 -- Price bands prove exactly one thing

Places returns `priceLevel`; it was fetched and discarded. It is far too coarse to price
an activity ("MODERATE" is not a number, and it means different things in Chicago and
Lisbon), so the temptation is to either over-read it or drop it.

**Decision.** Draw the single inference that needs no scale: *a venue Google prices at
all does not cost nothing*. `understated_cost` fires only on a paid band against
`estimated_cost == 0`.

- **That is the dangerous direction.** Every cost in a plan is the model's invention, and
  the budget check is only as sound as those inventions -- a dinner entered at 0 lets an
  over-budget trip validate cleanly.
- **The reverse is deliberately not checked.** FREE against a non-zero cost looks like a
  contradiction and is not: a picnic in a free park still costs what the picnic costs.
  Flagging it would manufacture violations out of sensible plans.
- **Transport, accommodation and rest are exempt.** In those categories a zero *is* the
  normal way to write a real cost -- a four-night hotel is billed once and the other three
  nights entered at 0.
- **Coverage, probed rather than assumed (2026-08-17).** Chicago restaurants come back
  banded across all four tiers (Alinea VERY_EXPENSIVE, Giordano's INEXPENSIVE); Chicago
  *museums and hotels came back with no band at all*. So the check fires on food and
  effectively nothing else, and the accommodation exemption above is belt-and-braces
  rather than load-bearing. Admission fees remain unverifiable -- Google does not band
  them, and no cheap API does.
- A prompt rule states the same thing, so the common case is prevented rather than
  repaired.

## 2026-08-17 -- The eval went red, and it was not the tier-2 work

Smoke subset after the departure-hour / tool-budget / price-band changes: **1/5 cases,
29/35 checks, 23 LLM calls, 221k tokens**. Recorded in `docs/eval-2026-08-17.json`.
Deliberately *not* written to `eval-baseline.json` -- a baseline is a state worth
holding, and this is not one.

**Four of the six failing checks were `insufficient_transfer`, which the new local-hour
measurement would plausibly have caused. It did not.** Re-measured each failing hop at
both the old fixed 11:00 UTC reference and the new local one:

| case | gap | old reference | new reference |
|---|---|---|---|
| memory-recall (Seoul) | 0 min | 24 min transit -> kept | 24 min transit -> kept |
| revision (New Orleans) | 8 min | 6 min drive -> kept | 8 min drive -> kept |
| revision after edit | 0 min | 3 min drive -> kept | 5 min drive -> kept |
| exclusions (Lisbon) | 1 min | 1 min drive -> kept | 1 min drive -> kept |

Every one fails under both. The real fault is upstream: **the model schedules activities
in different places 0-8 minutes apart**, and one repair round does not fix it. The
constraint layer is doing its job; the plan is wrong before it gets there.

**One failure was the checker's fault, not the plan's.** `HIKING` contained bare "climb",
which failed a Lisbon plan for "Arco da Rua Augusta -- climb for rooftop views" -- a lift
and a staircase inside a monument. Narrowed to "mountain climb" / "rock climb". A checker
that manufactures failures is worse than one that misses some.

**Not comparable to the 2026-08-10 baseline** (4/4 cases, 14 calls, 57k), and it never
will be: since then the subset gained the revision case, the product moved to English,
and opening hours, transit and traffic-aware driving all landed. That baseline describes
different work and should be treated as retired, not as a target.

## 2026-08-17 -- Naming the number the validator already knew

The prompt said "leave realistic travel time between locations". The validator required
`MIN_TRANSFER_MINUTES = 15`. The model was guessing at a threshold the codebase already
held, and it guessed 0-8 minutes.

Replaced with the number itself, **interpolated from the constant** rather than typed
into the prompt, so the rule and the instruction cannot drift apart. Pinned by a test.

**Result, same 5-case subset:**

| | before | after |
|---|---|---|
| cases | 1/5 | 2/5 |
| checks | 29/35 | 32/35 |
| `insufficient_transfer` failures | **4** | **0** |
| LLM calls / tokens | 23 / 221k | 25 / 274k |

Transfer violations went to zero and stayed there. `revision` went 11/13 -> **13/13**,
`exclusions` 4/6 -> 6/6. The extra tokens are the cost of the model actually doing the
spacing work, and worth it.

**The lesson generalises, and immediately did.** Every one of the four remaining failures
was `outside_opening_hours`, and every one had the same shape: a venue *open that day*,
scheduled *before it opened* (Art Institute 09:30 against 11:00; RONGROS 10:30 against
11:00; Bogwangjung 13:15 against 16:00). The prompt rule was written entirely about
**days** -- "shut on the day you wanted it", "many museums close one day a week" -- and
said nothing about the hour. Same failure mode as the transfer rule: the check was
stricter than the instruction. Rule rewritten to cover both mistakes explicitly.

## 2026-08-17 -- Two ways to send a malformed conversation, both found by a live eval

An eval case died on `400 ... messages[20] assistant must provide content,
reasoning_content or tool_calls`, and looking for the cause turned up two distinct
faults. Neither was reachable by the offline suite as written.

1. **The call budget trimmed execution but not the history (mine, same day).** The
   assistant turn was written into `messages` from the *full* tool-call list while only
   the first `MAX_TOOL_CALLS` were executed. Every declared tool call must come back with
   a matching tool reply, so this is a malformed conversation rather than a smaller one --
   and it fails on the *next* request, far from the cause. Fixed by trimming
   `turn.tool_calls` before the message is built, so one list drives both.
2. **An empty assistant turn was sent back verbatim (pre-existing).** A turn with no
   content and no tool calls rendered as `{"role": "assistant", "content": null}`, which
   the endpoint rejects outright. It also carries no information, so `llm.is_empty` now
   drops it from the history rather than inventing placeholder content.

Both are pinned by tests that were **checked against the old code first** -- reintroduced
the faults, watched the two tests go red, restored. A regression test that does not fail
on the regression is decoration.

## 2026-08-17 -- Four eval runs in one afternoon, and what they can and cannot say

Raw records in `docs/eval-runs/`. Same 5-case smoke subset, same model, one run each.

| run | change under test | cases | checks | transfer | hours | budget |
|---|---|---|---|---|---|---|
| a | tier-2 work complete | 1/5 | 29/35 | 4 | 1 | 0 |
| b | + transfer minimum in the prompt | 2/5 | 32/35 | **0** | 4 | 0 |
| c | + opening-hours rule covers times | 1/5 | 26/31* | 4 | **0** | 1 |
| d | + malformed-conversation fixes | **3/5** | 31/35 | 4 | 1 | 2 |

\* run c had a case die on the 400 described above, so it measured 31 checks not 35.

**The honest conclusion is about method, not about the prompt.** The transfer rule did not
change between b and c, and transfer failures went 0 -> 4. Whatever moved them, it was not
that rule. **A single run cannot attribute a change of this size**, and the "transfer
failures went to zero" reading recorded earlier in this file was drawn too early -- kept
above rather than deleted, because the mistake is the point.

What survives across all four runs:

- **Case pass rate trends up** (1 -> 2 -> 1 -> 3) and run d is the best measured state.
- **The crash fix is not ambiguous.** `memory-recall` went ERROR -> PASS 4/4.
- **`over_budget` is new in run d, twice** (956/900 USD, 312/200 EUR). Plausibly the price
  work doing its job: "only genuinely free things cost 0" raises the invented costs, and a
  trip that was always over budget stops hiding behind zeros. **Not proven** -- it is one
  run, and the alternative reading is that the model simply overspent.

Attributing any of this properly needs repeated runs per configuration, which is several
hundred thousand tokens. Until that is spent, treat single-run deltas as weather.

**`eval-baseline.json` stays retired.** 3/5 is not a state worth holding as a target.

## 2026-08-17 -- Three fixes aimed at the 0-minute gap

Diagnosis first: the model was not careless. `get_travel_time`'s own description said
*"use it when two activities are far apart and the gap between them looks tight"* --
**circular**, since knowing the gap is tight is what the measurement is for. The system
prompt never mentioned the tool at all. So at the moment the model chose start and end
times it had no spatial information, and a 14-minute walk got a 0-minute gap.

That is the third instance of one shape in a single day: **the check was stricter than
the instruction** (transfer minimum, opening hours to the hour, and now this).

**1. Distances are given before the schedule is written** (`app/agent/proximity.py`).
`search_places` returns coordinates and the run threw them away. Straight-line distance
is arithmetic, so every venue already looked up can be described for **zero extra API
calls**: "Art Institute <-> Wrigley Field: 8.1 km apart -- too far to walk". Injected
after the tool round, so it lands in the turn that composes the itinerary rather than in
a repair round afterwards. Re-sent only when the venue set grows (it is O(n^2) in the
prompt), capped, and truncation is announced.

Explicitly framed as an estimate. It knows nothing about rivers or one-way systems;
Routes stays the authority and `transfers.py` still confirms the schedule afterwards.

**2. The tool description no longer requires the model to already know the answer.**

**3. Findings split into blocking and advisory** (`ADVISORY_CODES`). Most codes describe
a plan contradicting itself or the world -- two activities at once, a locked door, no
time to cross town -- and the traveller never asked for those. But `overlong_day` and
`unsociable_hours` are judgements about **pace**, and pace is the traveller's to choose.
Someone who says "pack it in" and gets a 13-hour day got what they asked for; a late jazz
set is the reason they came.

Advisory findings are reported and never enforced: they do not make `report.ok` false,
and `as_instructions()` leaves them out so a repair generation is not spent undoing an
explicit wish. The app shows them as "Note:" lines under the verdict rather than in red.
`empty_day` stays blocking -- a day with nothing in it is the model failing, not a choice.

## 2026-08-17 -- The facts were never missing, only unusable

Follow-on from the distance block. A `search_places` reply is ~350 characters of JSON per
venue: `"ok":true,"error":null` on every object, seven separate weekday lines, thirteen
decimal places of latitude. A model composing three days holds ten of those across
several tool replies and cross-references them while also choosing times. The information
was always sufficient and close to unusable.

`app/agent/brief.py` restates it -- **no new data, no new API calls** -- as one line per
venue plus the distances:

    - The Art Institute of Chicago: Mon 11:00-17:00; Tue closed; Wed 11:00-17:00;
      Thu 11:00-20:00; Fri-Sun 11:00-17:00
    - Alinea: hours not published | very expensive

Consecutive days with identical hours collapse into runs, which is how a week reads and
which leaves the day that differs standing out -- the closed day being the single most
consequential fact about a venue, and previously line two of seven inside a blob.

Design notes worth keeping:

- **One block, not two.** The distances were folded in rather than sent separately; the
  model should not have to join them itself.
- **Venues known only by their hours still appear.** The three harvests are independent,
  so listing only the ones with coordinates would silently lose venues.
- **It says what it does not cover.** Read as a complete list it would license "I checked
  everything"; truncation is announced for the same reason.
- **Unreadable hours produce no claim**, same rule as the constraint check: absence of
  hours is never a closure.

**What this does not do**: measure every consecutive pair with Routes. Straight-line
distance already catches the gross errors (a 14-minute walk given 0 minutes); Routes only
matters for the borderline, which `transfers.py` already confirms after the draft. Blanket
measurement would roughly double Routes spend to mostly re-confirm the free estimate.

## 2026-08-17 -- Advisory findings verified on device, and two display bugs it exposed

Driven on the physical device with a request built to trip both advisory codes: *"1 day
in Chicago on 20 September, start 5am and keep going past midnight, budget 400 USD, pack
in as much as possible."* The plan came back with breakfast at 05:00, jazz to 23:59 and a
00:15 diner stop -- exactly what was asked for.

**The verdict card stayed green**: "Checked: budget, timing, routing" with a tick, and the
pace findings below it as "Note:" lines. Not red, not "3 issues unresolved". That is the
whole point of the split, and it now has a screenshot behind it rather than only unit
tests.

Two defects the live screen showed that the tests could not:

1. **Everything was displayed twice.** `finish` puts every violation message into
   `warnings` (right -- it is the plain-text channel for non-app clients), and the app
   also renders the report. So the same three findings appeared as calm "Note:" lines on
   the green card *and* as `⚠` lines in the warnings card below. Self-contradictory. The
   app now filters warnings that already appear as violations, leaving that card for what
   the verdict cannot say -- how the *run* went, e.g. "reached the 16-call tool budget".
   Note this duplication predates the advisory work: unresolved blocking violations were
   double-reported the same way.
2. **The short label was ambiguous where it mattered.** Two activities were both
   `unsociable_hours`, so the card rendered "Note: unsociably early or late" twice,
   identically, naming neither. The card now shows the full message; `violationLabel`
   stays for the live progress list, where compactness is right.

Also confirmed incidentally: 11 `search_places` + 5 `get_travel_time` calls, the model
reaching for travel times **before** committing to a schedule (the un-circular tool
description), and the run reporting `reached the 16-call tool budget; skipped 1 further
call(s)` rather than quietly researching less.

## 2026-08-17 -- Five runs, and the eval cannot measure what I keep asking it

| run | change under test | cases | checks | transfer | hours | budget |
|---|---|---|---|---|---|---|
| a | tier-2 work complete | 1/5 | 29/35 | 4 | 1 | 0 |
| b | + transfer minimum in the prompt | 2/5 | 32/35 | **0** | 4 | 0 |
| c | + opening-hours rule covers times | 1/5 | 26/31* | 4 | **0** | 1 |
| d | + malformed-conversation fixes | **3/5** | 31/35 | 4 | 1 | 2 |
| e | + brief, advisory split, tool description | 1/5 | 28/35 | 3 | **5** | 2 |

\* run c lost a case to the 400 described above.

**Run e is worse than run d, and `outside_opening_hours` is at its highest -- in the run
that added a brief whose main job is summarising opening hours.** That is the honest
headline and it is not spun here.

**But nothing in this table supports a causal claim, in either direction.** Adjacent runs
swing 0 <-> 4 on transfers and 0 <-> 5 on hours, and between b and c the transfer rule was
untouched. The noise band is as wide as any effect being looked for. Five runs, five
configurations, n=1 each: this is the shape of a measurement that cannot resolve what it
is pointed at.

**So the brief is not reverted, and that is not special pleading.** Reverting on a single
run is the same error as shipping on one -- and there is *direct* evidence from a live run
that the model uses it: a plan excluded a museum "due to its distance (11.7 km south)" and
noted "September 20, 2026 is a Sunday -- all venues verified against Sunday hours". Both
are decisions it could not have made before. What is missing is not a mechanism, it is a
measurement.

**The actual next step is to stop justifying prompt changes with single runs.** The cheap
version: repeat *one* case (`--case exclusions`, ~220 s, ~70k tokens) three times on the
current build and three times with the brief suppressed. ~400k tokens buys the noise band
for one case, which is the number every claim in this file has been missing.

Until that is spent, treat every prompt-level conclusion recorded today as provisional --
including the ones written approvingly.

## 2026-08-17 -- A community feed, reversing an earlier decision

`progress.md` recorded the feed as **deliberately deprioritised**: "CRUD plus moderation,
showing none of the agent engineering this project exists to demonstrate." The user asked
for it anyway. The reasoning was not wrong -- it is still CRUD, and it still shows none of
the agent work -- so it was built to be small, honest about what it is not, and easy to
delete.

**Storage mirrors `memory/store.py`.** SQLite via the standard library in a worker thread,
its own file so either store can move to Postgres on its own schedule. The itinerary is
one JSON column, not shredded into day and activity tables: it is a document read whole
and never queried by its parts, and normalising it would couple this schema to every
change in the agent's models. The fields the feed sorts and displays are denormalised
beside it at publish time, so listing thirty trips parses zero itineraries.

Decisions worth keeping:

- **The card is derived, never supplied.** `PublishRequest` has no `destination` or
  `total_cost` field at all. A caller that could set them independently could advertise a
  trip as somewhere it is not.
- **`saved_by_viewer` is tri-state.** `null` (nobody said who is asking) is a different
  claim from `false` (you have not saved this), and only one of them should draw an empty
  heart. Carried through the wire type and the Compose icon.
- **Saving is idempotent both ways**, enforced by `(plan_id, user_id)` being the primary
  key rather than by a check-then-insert. The client is a phone; a retry must not
  double-count.
- **Saving copies the trip into the local library**, because "saved" has meant *that*
  since the library existed. A heart that only moved a number on a stranger's card would
  be a different feature wearing the same icon.
- **One view model, held at the shell.** Publishing happens in the Saved tab and listing
  in the Community tab; two instances would leave a just-shared trip missing from the feed
  until something happened to reload it.
- **Per-author cap of 20, trimmed oldest-first**, so one account cannot crowd out the feed
  and the post someone just made is never the one refused.
- **A pin, not a globe.** `Icons.Default.Public` lives in material-icons-extended, which
  is a large dependency for one tab glyph.

**What it deliberately does not have: authentication or moderation.** `author_id` is a
claim, the same as `PlanRequest.user_id` has always been, so anyone can post as anyone and
withdraw anyone's post. The author check is still *written*, so a real token slots in later
without changing a caller -- but until then this is safe only because the server is
reachable over `adb reverse` and nothing else. The screen says so in its own header rather
than leaving it to be discovered.

## 2026-08-17 -- Two community fixes, both found by reading the code back

Neither showed up in the device walkthrough that "verified" the feature an hour earlier.
Worth noting on its own: a happy-path demo confirms the feature exists, not that it is
correct.

**1. Saving copied the trip twice.** The server's save is keyed on `(plan_id, user_id)`
and is idempotent; the local copy had no such key, so **save -> unsave -> save left two
identical trips in the library**. Fixed with a nullable `sharedPlanId` column on
`saved_plans` (Room v4 -> v5) so the copy is idempotent *by identity*.

Deduping on contents was the obvious alternative and is wrong: someone who deletes their
copy and saves it again should get it back, and a contents match would refuse that. The
migration adds the column with no default, so rows that predate it read as null -- they
were planned here, not copied, and null says exactly that. Pinned by six tests driven
against a fake DAO (Room needs a device; the rule lives in the repository).

**2. The feed only reloaded when the reader changed.** `setUser` did double duty --
identity and loading -- and returned early on an unchanged id, so a trip posted by someone
else while the app was open stayed invisible until the refresh button was tapped. A feed
goes stale by *time*, not by the reader changing. Split apart: `setUser` sets the id,
the screen calls `refresh` on every entry, and `refresh` skips when one is already in
flight so entering the tab and a publish completing cannot race.

Both verified on the device against an **in-place upgrade**, not a fresh install: the v4
library survived, and four heart taps left exactly one copy.

## 2026-08-17 -- Authentication: the fix is deleting the field, not checking it

Every `user_id` and `author_id` in this service was a claim the client made. Anyone could
post as anyone, withdraw anyone's post, and pad their own post's save count with invented
readers. The check on `author_id` was written and enforced and completely worthless,
because the thing it checked came from the same request it was guarding.

**So the fix is that the fields no longer exist.** `PublishRequest` has no author,
`SaveRequest` has no user, `PlanRequest` has no `user_id`. Identity comes from a bearer
token or it does not come at all. A test asserts their *absence* -- `"author_id" not in
PublishRequest.model_fields` -- because that absence is the security property.

The compiler enforced the rest: deleting the fields made every Kotlin call site that used
to pass an identity fail to build, which is how the SSE client turned up. It built its own
`OkHttpClient`, so it would never have received the auth interceptor and would have
streamed every plan anonymously while looking like it worked.

**Passwords: PBKDF2-HMAC-SHA256 from `hashlib`, 600k iterations, per-row count.** The app
hashed with a single round of SHA-256 -- fine for a local unlock, and billions of guesses a
second on a GPU for a server-side store. Argon2id is better and is a C extension
dependency; PBKDF2 is what the standard library offers, which is the same trade this
project made choosing `sqlite3` over SQLAlchemy. Storing the iteration count per account
means the cost can be raised later without locking anyone out.

**Tokens: opaque random strings, stored as SHA-256, not JWT** -- a deliberate departure
from what `progress.md` sketched:

- **Revocable.** Logging out deletes a row. Revoking a JWT needs a denylist table, which
  means carrying the state *and* the signature verification.
- **Nothing to get wrong.** No `alg: none`, no HS/RS confusion, no library.
- JWT's one advantage, skipping a database lookup, buys nothing on one server whose every
  request already touches SQLite.

Hashing the token is right where hashing the *password* slowly is right: the token is 256
bits of `secrets` output, so there is no guessing attack to slow down, only a database
leak to survive.

**Reading stays anonymous.** The feed and planning work without an account -- an anonymous
run simply recalls no preferences. Only writes that other people see require a token. A
lapsed token during planning degrades to anonymous rather than erroring, because a
traveller mid-trip should get their plan.

**The Room `users` table was rebuilt, not altered**, to *drop* `passwordSalt` and
`passwordHash` (v5 -> v6). A dead credential column is a live vulnerability the day someone
reinstates a "quick offline login" against it. The local row survives as a storage
partition only -- the library and transcript are keyed on its `Long` id.

Consequences worth knowing:

- **Two id namespaces now exist** and conflating them was a live bug caught on device: the
  local row id (`Long`) and the server account id (uuid). `isMine` compared the wrong one,
  so the reader's own posts offered a save button while a legacy post whose author id
  happened to be `"1"` offered a delete button.
- **A session from before this change is cleared at launch** rather than left presenting
  as signed-in-but-401-on-everything.
- **Signing in with the same username re-attaches the local library.** Verified on device
  through an in-place upgrade: the trip saved before the migration was still there.
- **Posts published before this change are orphaned.** Their `author_id` is `"1"` or
  `"999"`, which matches no account, so nobody can withdraw them.

**What this does not fix.** Authentication is impersonation and nothing else. There is
still no rate limiting (accounts are free and instant, so spam is a script away), no
moderation, no reporting, no email verification and no password reset.

## 2026-08-17 -- Sharing a trip was also sharing the sentence that produced it

Publishing sent `plan.request` -- the free-text sentence the traveller typed -- verbatim,
and every reader saw it as "Original request: ...". The share dialog promised only that
the destination and the display name would be visible. It never mentioned this.

That sentence is where the private half of a trip lives: "honeymoon", "my mother cannot
manage stairs", "budget is tight since the move", the names of whoever is coming. Found by
reading the share path back rather than by anything failing -- nothing does fail, which is
the whole problem with a privacy default.

**Now opt-in, defaulting to off**, and the dialog **shows the exact words** rather than
describing the field. A description of what would be shared is not consent; only the person
who typed it knows what is in it. The quoted text sits greyed out until the box is ticked,
so the state is visible without reading the checkbox.

The rule is a named function (`sharedRequest`) with its own test, because a privacy default
is exactly the kind of thing nothing else catches: the app works identically either way,
and the person who discovers the regression is the one whose sentence is already public.

**Not retroactive.** Posts published before this still carry their request; their authors
can withdraw them, and there is no other remedy.

Also considered and rejected: truncating or summarising the request. Either would mean
publishing something the traveller never read.

## 2026-08-17 -- Rate limiting, and what was actually worth protecting

Three things, and they are worth naming separately because an unlimited request costs
wildly different amounts depending on which one it hits:

1. **Money.** `/plan` spends LLM tokens and Google quota on *every* call and was completely
   open. It is by far the most expensive endpoint here, and it was the last one anybody
   would have thought to limit -- the obvious candidate is "posting", which is free.
2. **Passwords.** PBKDF2 at 600k iterations costs an attacker ~0.2 s a guess, which sounds
   like a defence until you multiply it out: ~400,000 attempts a day against one account.
3. **The feed.** "20 posts per author" bounds nothing while authors are free and instant.

**Sliding window, in memory, single process.** Honest for one server and **wrong for two**:
counters are per process, so N replicas mean N times the limit, and a restart forgives
everyone. Redis is the Phase 4 answer; this sits behind `RateLimiter` so the swap changes
nothing above it. Written down here rather than discovered during a deploy.

Decisions worth keeping:

- **Login counts failures per username, and all attempts per address.** Counting every
  attempt per username would let anyone lock a stranger out of their own account by
  guessing wrong on purpose -- trading a brute-force hole for a denial-of-service one. The
  per-address counter is what sees password *spraying*, which the per-user one cannot,
  because no single account is hit twice. A successful sign-in clears the failure count.
- **The lockout is still two-way and the test says so.** Once the failure limit is hit the
  real owner is refused too. That is why the window is short (15 min) and the count
  generous (10). A test pins the trade-off rather than pretending it is free.
- **A refused request is not recorded.** Otherwise a client polling every second pushes its
  own window out forever: a punishment that compounds rather than a ceiling on work.
- **`Retry-After` on every 429**, and the client turns it into "try again in about 57 min".
  A message without a number in it produces someone tapping the button every two seconds,
  which is the behaviour the limit exists to stop.
- **The client key is `request.client.host`, never `X-Forwarded-For`.** Without a trusted
  proxy in front, that header is set by the caller, so honouring it would let anyone reset
  their own limit by inventing an address.
- **Reads are never limited.** Cheap, anonymous, and limiting them would break browsing for
  everyone behind one address while protecting nothing.
- **Keys are pruned.** The key space is attacker-controlled -- one entry per address or
  username ever seen -- so without it this is a slow memory leak.

**Two bugs the device found that the tests could not.** A refused publish showed *nothing*
where the user tapped: sharing starts on the Saved tab but is carried out by the community
view model, whose snackbar lives on the Community tab, so the message appeared later and
out of context. And the server's detail has no trailing full stop, so it ran into ours:
"the limit is 10 per 60 min Try again in about 59 min."

**Test cost.** The limit tests spend whole login and registration allowances on purpose,
which is fifty-odd password hashes; at production iterations that alone doubled the suite's
runtime. PBKDF2's cost is what `test_auth.py` is for, so those tests turn it down.

## 2026-08-17 -- Feed paging is cursor-based, and search is destination-only

**Cursors, not offsets.** The feed grows at the top, so with `OFFSET 30` a single post
published while somebody is reading shifts every later page down by one: they see one trip
twice and never see another. A cursor is a *position*, so nothing published above it can
move it. There is a test that publishes mid-read and asserts the walk is still each id
exactly once.

**Ordered by `(created_at, id)`, not `created_at` alone.** Two plans published in the same
millisecond have no defined order between them, and a cursor landing on that tie repeats
one row and skips the other. Pinned by a test that forces every timestamp equal and then
walks the pages.

Details worth keeping:

- **The cursor is base64 of the sort key**, so a client cannot hand-build one and end up
  depending on which columns order the feed.
- **A cursor this service did not issue is a 422**, not silently ignored: starting over
  from the top would show a client bug as an endless first page.
- **The query asks for `limit + 1`** and reports "there is more" from whether it got it.
  A separate COUNT can disagree with the page it is describing.
- **`next_cursor` is null on the last page**, including when the last page is exactly
  full. A client cannot tell an empty page from a slow one, so it would keep asking.
- **The client de-duplicates appended pages by id.** A refresh can land between asking and
  answering, and a withdrawal shifts the window.

**Search matches the destination only.** Not the note (the author's prose) and not the
request (which may not be published at all -- see the privacy fix). LIKE wildcards are
escaped with `!`: unescaped, a search for `%` matches every trip and `_` matches any
character, so the filter quietly stops filtering exactly when someone types punctuation.

**Debounced at 350 ms.** Per-keystroke, "New Orleans" is eleven requests, and the ten that
are discarded can still arrive out of order and overwrite the one that matters.

**The empty state distinguishes the two cases.** "Nothing shared yet" under an active
search is a lie, and the remedy differs: post something, or search for something else.

**A crash the device caught and no test could.** The `init` block collecting `_query` was
declared *above* the property, and Kotlin runs initialisers in declaration order -- so it
captured null and the app died on launch inside `debounce`, with a stack trace naming
neither the class nor the field. There are still no ViewModel tests; this is what that
costs.

## 2026-08-17 -- Containerised, and the three things that would have broken it

**The databases must be on a volume.** `memory_db_path` and friends default to *relative*
paths, so in a container they resolve under the working directory -- the writable layer,
which is discarded with the container. Every account, session and shared trip would vanish,
without an error to say so. The image sets all three to `/data/...` and declares the
volume. This is also why this project **cannot** run as-is on Cloud Run, App Runner or
Fargate: scale-to-zero with an ephemeral disk is the same failure with better marketing.

**One worker, and the Dockerfile says why.** Two would break two things at once: the rate
limiter keeps its counters in process memory, so N workers means N times every limit; and
several processes writing one SQLite file contend on a lock that WAL only partly relieves.
Redis and PostgreSQL fix both, and they are Phase 4. Until then a single process is the
honest configuration, not a limitation to be quietly worked around with `--workers 4`.

**`.dockerignore` before anything else.** Without it `COPY` would bake `backend/.env` --
the LLM key and both Maps keys -- and the `.db` files, which hold password hashes and live
session tokens, into an image layer. Deleting them in a later layer does not remove them.

Smaller choices: the port is published on `127.0.0.1` only, because an unencrypted API
carrying bearer tokens does not belong on a public interface without a reverse proxy doing
TLS in front. `uv sync --locked` fails rather than re-resolving, so the container cannot
quietly run a different dependency set than the tests did. The uv image is pinned rather
than `latest`, because a floating tag makes one commit produce different images on
different days.

**Gotcha found while verifying: `docker compose config` prints resolved `env_file` values
in cleartext.** It is the command's job, and it means running it in a shared terminal, a
screen share or a CI log discloses every secret in `.env`. That is the third way keys have
leaked in this project in one day, after the app's debug panel and httpx's request logging.

**Verified 2026-08-17, not assumed.** Image built and run: healthy on the container's own
health check, `/health` answering, process running as uid 10001, and `/app` containing only
`.venv` and `app` -- no `.env`, no `.db`. Then the test the whole exercise exists for:
registered an account, published a trip, `docker compose down`, `up`, and both the trip and
the **bearer token issued before the restart** still worked. Image is 360 MB.

## 2026-08-17 -- A ceiling that bounds the bill, and TLS that cannot be bypassed

**The per-account limit never bounded the bill.** Twenty planning runs an hour each,
multiplied by an unbounded number of free instant accounts, is unbounded. The difference
between "no single person can run up the bill" and "the bill has a maximum" is a *global*
counter, and only the second one lets anyone sleep. `settings.max_plans_per_day` is a
setting rather than a constant because it is a budget and only the person paying knows the
number; the default is deliberately low, since too low costs a refused request and too high
costs money.

- **Both ceilings are checked before either is recorded.** Taken one at a time, a run
  refused by the personal limit would already have been counted against the global one --
  so a caller bouncing off their own hourly cap would quietly eat the day's budget without
  producing a single plan. There is a test for exactly that.
- **The two refusals are different on purpose.** Spending your own allowance is a 429: you
  did it, waiting fixes it. Hitting the service ceiling is a **503** saying "nothing is
  wrong with your request" -- calling it "too many requests" blames someone who did nothing
  and sends them tapping retry at something they cannot influence.
- **Anonymous runs count.** They cost the same, and excluding them would leave the entire
  budget reachable without an account.
- **Known weakness: the counter is in memory, so a restart forgives the day.** Consistent
  with every other limit here, and fixed by the same Redis work. Worth knowing before
  trusting it against a determined attacker rather than against an accident.

**TLS: the backend stops being reachable at all.** `compose.tls.yaml` extends the backend
service and `!reset`s its published port, so the only route in is Caddy. That removal is
the whole point -- with port 8000 still on the host, the certificate is decoration and an
API carrying bearer tokens is available in plaintext beside it. `!reset` rather than an
empty list because Compose *merges* `ports` across files: an override that looks like it
clears the mapping leaves it in place.

Caddy rather than nginx for the renewals, which are the step everyone forgets ninety days
later. Two details that would otherwise bite: `flush_interval -1` on `/plan/stream`, or the
proxy buffers a minutes-long SSE stream into one lump delivered at the end -- indistinguish-
able from a hang, and the client would fall back to non-streaming every time; and the domain
and ACME email have **no defaults**, because a wrong domain means asking a certificate
authority for a name you do not control, repeatedly, until the address is rate limited.

**Verified, not assumed.** Brought the stack up against a `.localhost` domain (Caddy signs
those with its internal CA and never contacts Let's Encrypt): host port 8000 refused,
HTTPS answering, HTTP 308-redirecting, an account created by the *previous* container still
able to log in, publish and withdraw over TLS, and an unauthenticated write still 401.

## 2026-08-17 -- Password reset, and the thing it has to take away

Reset is the one flow that hands out access on the strength of an email address, so most
of the design is about what it refuses to say and what it revokes.

**It revokes every session.** The case this feature exists for is an account somebody else
got into; leaving their bearer token alive would make changing the password pointless.
The screen says so before you commit -- "This signs you out everywhere."

**It answers identically for an unknown address.** A reset endpoint that behaves
differently for a known one is an account-enumeration oracle wearing a helpful face. The
server always 204s, and the client says "If that address has an account, a code is on its
way" rather than confirming anything. A client that helpfully reported "no such address"
would hand back exactly what the server withheld -- which is why the repository's own
docstring says so.

**A code, not a link.** There is no web frontend for a link to land on, and asking someone
to paste a 43-character token into a phone is worse than eight characters they can read.
Eight from a 32-symbol alphabet with **no O/0 or I/1**: it is read off one screen and typed
into another, and a character pair nobody can distinguish turns a working code into a
support request.

**A counter I wrote and then deleted.** The first version had `attempts` on each code, to
destroy it after five wrong guesses. It cannot work: a wrong guess hashes to no stored row,
so there is nothing to charge the attempt against. It would have *looked* like a defence
and counted nothing. What actually bounds guessing is the code's entropy and a rate limit
on `/auth/reset/confirm`, and the constant that replaced it says exactly that.

**Email is optional, and the sign-up screen states the consequence** -- "Without one, a
forgotten password cannot be reset" -- rather than just marking the field optional.
Requiring it would lock out the accounts that already exist, and an unverified address is
not proof of anything anyway. Verification is the obvious follow-up and is *not* built: a
typo'd address today silently makes an account unrecoverable.

**Mail: stdlib `smtplib`, and a console fallback.** Requiring a provider to run the service
would mean the flow could not be developed or tested without one. With no `SMTP_HOST` the
code goes to the log behind a `NO SMTP CONFIGURED` marker, and **the boot log warns every
start** -- a server that believes it is emailing people while printing their reset codes to
stdout is a security problem, not a convenience. Sending never raises, because a 500 from a
delivery failure would tell the caller the address existed, which is the one thing the
endpoint is built not to reveal.

Two limits: five requests an hour per address (otherwise this endpoint is a way to have a
stranger's inbox flooded using the service's own good name), and ten confirmations per
quarter hour.

**Verified end to end on the device**, not just in tests: requested a code from the app,
read it out of the server log, set a new password, and confirmed the old password stopped
working, the new one worked, and a token issued *before* the reset came back 401.

## 2026-08-17 -- Email verification, and the rule that makes it worth having

Verification is only worth building if it **blocks** something. Here it blocks password
reset, and that rule closes a real takeover: register with a stranger's address -- by typo
or on purpose -- and without it the stranger can reset their way into the account. The
query says so out loud (`AND email_verified = 1`) and a test names the attack.

Decisions worth keeping:

- **A separate `verify_codes` table**, not a `purpose` column on `reset_codes`. One table
  would mean every lookup had to remember to filter by purpose, and the day someone forgets
  is the day a code mailed to prove an address also sets a password. Two tables make that
  unrepresentable rather than discouraged.
- **Confirmation is scoped to the account presenting the code.** Checking the code alone
  would make it a bearer credential for somebody else's address.
- **The address is taken from the code, not from the account row.** Between issuing and
  confirming, the account may have been pointed somewhere else; marking *that* address
  proven on the strength of a code mailed elsewhere would rebuild the takeover out of the
  fix for it.
- **Changing the address always clears the flag.** An address is proven only for as long as
  it is the one that was proven -- otherwise anyone holding a session could move a verified
  account onto an address they do not own.
- **Resending requires a session** rather than taking an address, which is what stops it
  becoming the enumeration oracle the reset endpoint carefully is not.
- **Two accounts cannot share an address**, because one reset request with two possible
  answers is not something the flow can express. Blank is exempt: opting out of being
  recoverable must not collide with every other address-less account.

**Three bugs found by using it, not by the suite** -- which now stands at four such finds
in two days on a client layer that still has no ViewModel tests:

1. **The profile lied about recoverability.** `restore()` called `/auth/me` and threw the
   answer away, using it only to check the token was alive. So the cached flag went stale
   the moment the account changed anywhere else, and the profile would say "Confirmed"
   about an address the server considered unproven -- telling someone they can recover
   their account when they cannot. It now writes the answer back.
2. **Confirming an email threw you out of the profile.** `AppRoot` keyed its navigation on
   the whole session object, and `SignedIn` carries the user row -- so editing that row
   produced a new instance, re-ran the effect, and navigated to `main`, resetting the tab
   to Plan. Now keyed on which state it is, so navigation happens on transitions only.
3. **"Change" wrapped to one letter per line.** A long email address squeezed the action
   label because the row had no weight on the value. Fixed in the row rather than by
   shortening the string, since any long value did it.

## 2026-08-18 -- The device's two leaks: a whitelist that could not express the case, and a backup nobody turned off

Two client-side holes, both invisible on the test device for the same reason: it runs
API 27, where the platform defaults are the permissive ones.

### Cleartext was configured per host, and the case it had to cover is not a host

`network_security_config.xml` named `10.0.2.2`, `localhost` and `127.0.0.1`, and permitted
cleartext to those. Meanwhile the backend-address dialog told people to type
`http://<your computer's LAN IP>:8000`. Those two cannot both be right: `<domain>` matches
one host at a time and there is no CIDR form, so "any machine on my Wi-Fi" is not
expressible. On API 28+ the documented setup dies with CLEARTEXT NOT PERMITTED. The test
device is API 27, where cleartext is allowed by default and the file is barely consulted,
so nobody ever saw it.

The whitelist was also wrong in the other direction, which is the part worth keeping:
enumerating dev hosts in `src/main` means a *shipped* build carries permission to talk
plaintext to a loopback address. Two configs by source set, so each build states its own
posture:

- `src/main` -- `cleartextTrafficPermitted="false"`, no exceptions. The release posture.
- `src/debug` -- `cleartextTrafficPermitted="true"` for everything, which is what "point
  this at whatever machine is running the backend today" actually requires. It cannot
  reach a shipped build, because the debug source set is not compiled into release.

Consequence, and the reason `ServerConfig` changed with it: a bare `192.168.1.10:8000` was
unconditionally read as `http`. In a release build that now composes an address the
platform refuses to dial, and the refusal is indistinguishable from the server being down.
The assumed scheme follows the build (`http` in debug, `https` in release), and `parse`
takes it as a defaulted parameter so both branches are reachable from a test rather than
only from an APK. An explicit scheme is still never overridden -- a typed `http://` fails
loudly instead of being silently rewritten into a different server.

### Backup was on, and it was carrying the session token

`SessionStore` had documented that the token sits in plain DataStore and "lands in a device
backup". The manifest never acted on it. `allowBackup` defaults to true, so the bearer
credential was leaving the device by the most ordinary route there is -- worse than the
root-readability the comment worried about, because it needs no attacker on the phone.

`android:allowBackup="false"` stops cloud backup on every API level. It does **not** stop
device-to-device transfer on Android 12+, which is a separate channel with its own opt-out,
so `data_extraction_rules.xml` excludes the session file from both. Scoped to that one file
rather than the whole app: the saved-trip library and the transcript are the user's content
and should ride along to a new phone. Only the credential does not, and the cost is that
the new device asks them to sign in.

Not done here, deliberately: `EncryptedSharedPreferences`. It adds a dependency to defend
against an attacker with root, which is a strictly smaller exposure than the one closed
above. The mitigations that already exist (30-day expiry, server-side revocation on sign
out) stay the honest answer until there is a reason to spend the dependency.

## 2026-08-18 -- Run warnings became codes, and the two lists stopped overlapping

`PlanResult.warnings` was a `list[str]`, and the strings were written for whoever wrote
the tool loop. Travellers were reading `reached the 16-call tool budget; skipped 3 further
call(s) to search_places` and `stopped calling tools after 4 rounds` verbatim on the plan.

Now each entry is a `RunWarning`: `code`, plus the numbers behind it (`budget`,
`dropped_calls`, `dropped_tools`), plus `detail` -- the old developer sentence, kept on
the wire for logs, the smoke scripts and a client that meets a code it predates. The
client writes the traveller's sentence, because only the client knows who is reading.

Decisions worth keeping:

- **A constructor per code, not a `RunWarning(...)` at each call site.** `code` and
  `detail` have to stay in sync, and they can only do that in one place per code. A test
  asserts every member of the `Literal` has a function of the same name, so adding a code
  without a rendering fails rather than shipping.
- **The client collapses the three budget codes into one sentence.** Which ceiling was hit
  matters to whoever tunes the tool loop and not at all to someone deciding whether to
  double-check a closing time. The codes stay distinct on the wire; only the rendering
  flattens them. That split is the entire argument for codes over sentences.
- **`code` is an open string on the client, never an enum.** The server ships ahead of the
  app, and an unrecognised code has to render as something rather than throw.
- **The validation report is no longer restated as warnings.** It used to be copied in
  wholesale, so every finding arrived twice in two renderings, and the Android client had
  to suppress one set by comparing sentences -- which is precisely the coupling a code is
  supposed to remove. `warnings` is now what the *run* could not finish; `validation` is
  what the *plan* gets wrong. Nothing stops being said: the report carries advisory
  remarks too, and the client already rendered them. The smoke scripts print both lists
  now, because they used to get the violations for free through `warnings`.
- **`no_itinerary` is dropped by the client rather than rendered.** The empty-state card
  is that same message, said properly. Filtering it is a one-line predicate on the code;
  under strings it was a substring match nobody would have trusted.

**Old saved trips would have stopped parsing.** Room stores the response verbatim in
`planJson`, so every plan in an existing library holds warnings as bare strings.
`ignoreUnknownKeys` covers a new *field* and nothing covers a changed *type*, so those
rows would have thrown and the trips would have vanished from the list. A
`JsonTransformingSerializer` reads a bare string as `{"code": "legacy", "detail": <it>}`;
legacy entries keep their old wording, because that wording is genuinely all that was
stored and there is nothing to upgrade them to.

Verified live on the device with `MAX_TOOL_ROUNDS=1`, which guarantees the warning fires:
the card read *"Built on less research than usual: the planner hit its lookup limit before
it had checked everything. Worth confirming opening times and prices yourself."* The same
plan shipped five unresolved violations, and they appeared once, on the validation card.

## 2026-08-18 -- CI's first real run: the SDK platform id is not what the API level is

`ci.yml` had installed `platforms;android-37` for a `compileSdk = 37` build. That package
does not exist. From API 37 the platform packages are published with a minor version --
`platforms;android-37.0`, `platforms;android-37.1`, `platforms;android-37.2-beta1` -- and
there is no bare `android-37`, so `sdkmanager` answers `Failed to find package` and exits 1.
API 35 and 36 still publish a bare id alongside the qualified one, which is why the pattern
looks safe right up until the version where it isn't.

`compileSdk = 37` resolves to the `.0` minor; confirmed rather than assumed by grepping the
local build outputs for the platform path it actually used (`platforms/android-37.0`), since
this machine has both minors installed and would have built fine either way.

**This is the class of bug CI exists to find.** It was invisible locally -- the SDK was
already installed by Android Studio, so nothing ever resolved the package id from the
network. The failure needed a machine that starts with nothing.
