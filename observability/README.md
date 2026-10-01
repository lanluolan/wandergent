# Content-free traces and fact coverage

The selected platform is **Phoenix**, not both Phoenix and Langfuse. On 2026-10-01,
the authorized local stack persisted the synthetic four-span smoke and an 85-span
real planning trace, verified using Phoenix's read-only spans API. Parent relationships,
tool/model/repair spans and source/configuration fingerprints were checked. Do not treat
static Compose validation or an HTTP acknowledgement alone as proof of persistence.
The `specifics` trace also persisted 60 spans, its `bad_request` tool failure, and an
estimated model cost of $0.02155274 matching the eval report's operator-supplied rates.

## What is recorded

Each plan has a `trace_id`, bounded parent/child spans for graph nodes, model turns,
tool calls, route confirmation, validation and repair. Spans carry elapsed time,
reported tokens, cache hits, failure codes, repair count and model name. Prompt,
schema and tool-definition hashes and an implementation-source hash identify versions.
The result outcome and counters share the root trace. Eval snapshots retain `trace_id`.

`repair.schedule` records the bounded observed-hours fallback: candidate count, strategy
and accepted/rejected status, without venue names or itinerary text. This is not an LLM
repair call. Its candidates retain full budget/time/intent checks and require newly
changed transfers to be confirmed; a failed candidate restores the original route facts.
There is at most one additional full route-confirmation pass per run.

For earlier closing windows, the candidate generator may backfill a flexible prefix
within a six-activity timing cap. It preserves venue identity/cost, transport durations
and original gaps; numeric requested durations, HH:MM times, bookings, named prefix
venues and accommodation are protected. Shortening a flexible stop is not proof that
every unstated preference remains satisfied. The full validator and fresh permitted-mode
Routes calls still decide acceptance; missing measurements cannot authorize the change.

## Release and live efficiency verification

First pass the unchanged `evals.run` smoke/full gates. A subsequent paid paired
experiment must run alone to avoid model-call contention with another gate:

```powershell
python -m scripts.benchmark_live --today 2026-09-28 --repetitions 2 --json .eval/NEW-paired.json
python -m scripts.verify_live_hours --packed-prefix --today 2026-09-28 --json .eval/NEW-hours.json
python -m scripts.verify_acceptance_traces .eval/NEW-paired.json .eval/NEW-hours.json --json .eval/NEW-persisted.json
```

The pair uses identical production cases/checks and current configured model: serialized
research with shared TTL disabled versus the current scheduler/cache. Both retain local
deduplication, query/retry budgets and mandatory route checks. Preferences/cache start
isolated per case/arm, and first-arm order is counterbalanced across two repetitions.
Acceptance requires all checks in both arms and fewer executed research tools OR lower
summed seconds and median paired time ratio. Research counters exclude the validator's
mandatory route lookups, but end-to-end elapsed time includes them. Small-sample provider
and fact variance remain; this is not a statistical production/billing guarantee.

The hours script makes real model/Places/timezone/Routes calls but deliberately injects
the same bad candidate after initial and repaired model output. Reports and spans mark
the injection; it proves dependency integration, not natural model recovery frequency.
Report targets must be new workspace-local files. If Collector acknowledgement races
Phoenix indexing, only read-only persistence requests are retried. `--verify-report`
rechecks a retained hours report into a new file without additional paid requests.
Missing prices remain unknown. Latest evidence and retained failures: [efficiency.md](efficiency.md).

This is an OTLP/HTTP JSON wire exporter using the existing `httpx` dependency, **not
an OpenTelemetry SDK**. It does not implement inbound W3C trace propagation or
automatic HTTP instrumentation. Model cost is an operator-priced estimate, never a
provider bill; missing prices/usage produce `unknown`, and Maps charges are excluded.

No prompts, tool arguments/results, addresses, user identity, itinerary contents,
exception messages or API keys are exported. Tool names are allowlisted. Export is
off by default, times out after two seconds, and fails without failing the plan.
Each run retains at most 512 child spans plus its root.

## Optional local Phoenix stack

Starting Docker and downloading images writes outside this repository. Under the
workspace rules, obtain explicit permission before doing this. The optional stack
uses pinned Phoenix and official Collector images and binds ports to localhost only.

```powershell
# Run at the repository root, only with permission and an already running engine:
docker compose -f compose.observability.yaml config --quiet
docker compose -f compose.observability.yaml up -d
```

In the backend environment:

```dotenv
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:4318/v1/traces
TRACE_DIRECTORY=backend/.traces
# Optional, USD per million tokens: uncached input, cached input, output
TRACE_MODEL_PRICES={"your-model":[0.0,0.0,0.0]}
```

Use actual operator-confirmed prices, not the zero example. Restart the backend after
changing settings or source; the implementation fingerprint is cached per process.
`TRACE_DIRECTORY` resolves relative to the repository root, rejects paths outside it,
and stops saving after 100 files rather than deleting history. It is disabled by default.

From `backend/`, using the project's existing environment:

```powershell
python -m scripts.smoke_trace
python -m scripts.verify_phoenix PRINTED_TRACE_ID --expect tool.smoke --expect validation.smoke
```

This creates only synthetic spans, with no paid model or travel API. Check the exporter
logs, then open `http://127.0.0.1:6006`, select the `wandergent` project and find the
printed trace ID. Verify the nested tool span, `ValueError` validation span and version
attributes on a real planning trace. Receipt and persistence must be checked in Phoenix;
the smoke script's exit status is not an end-to-end acceptance gate. The verifier checks
actual persisted spans, trace identity, expected names, one root and complete parent links;
it exits nonzero if those checks fail. Run it against a real plan trace as well.

The application sends JSON to the Collector, which converts to protobuf for Phoenix.
Phoenix's [trace router](https://github.com/Arize-ai/phoenix/blob/arize-phoenix-v20.1.0/src/phoenix/server/api/routers/v1/traces.py)
requires protobuf; do not point the JSON exporter directly at Phoenix's port 6006.
The wire format follows the [OTLP specification](https://opentelemetry.io/docs/specs/otlp/).

Stopping the test stack is a separate authorized operation. There is no persistent
volume in this configuration; removing containers also removes their test trace store.

## Offline structural replay and comparison

```powershell
# From backend; paths are relative to this directory:
python -m scripts.inspect_trace .traces/trace-ID.json --compare .traces/trace-OTHER.json
```

Use `../backend/.traces/` or `.traces/` according to your working directory. The tool
prints a span timeline, failure-type groups, model/tool composition, per-model token
and timing metrics, estimated model cost, result outcomes and source/configuration versions.
Replay here means reconstructing recorded execution structure, **not rerunning** a
private prompt. No model calls, external tools or hidden reasoning are replayed.
Compare like-for-like eval cases; model/upstream variance is not a controlled A/B.

## Fact coverage is not whole-activity verification

`activity_evidence` is additive metadata keyed by final day/activity indexes. An exact,
unambiguous Places identity match records provider, original observation timestamp,
availability of hours and price bands; it does not verify all recommendations, actual
opening status or exact prices. Cache hits retain the original timestamp. A matching
final-day route confirmation includes provider, measurement timestamp, effective
departure, mode and duration. Internal raw tool facts are excluded from API JSON.

Android shows only place checked/not independently checked and a reminder to recheck
hours and prices before departure. Prices remain `estimate`; unavailable/ambiguous
facts stay unverified. Older saved plans with no evidence retain their previous UI.
