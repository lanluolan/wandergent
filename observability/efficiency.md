# Release and efficiency evidence — 2026-10-01

## Current gpt-6-luna gate: release, paired sample and live recovery passed

Current backend: **621 passed, 1 opt-in MCP deselected**; Ruff/format/diff pass.
Implementation trace fingerprint:
`bdc4e4ee874ab392d3ef63a5e1750cf30efd9aa5c39a4a33ff9a90c23df69fe0`.
Case/check suite remains
`990a218451c57da04b82eca9e57007f1361b1d3f1bea1c66e9f1483068773dd7`.
Model gpt-6-luna, fast routing off, reference date 2026-09-28, Maps on, prices absent.
No model/configuration, dependency, commit or push changes.

New request-owned lodging confirmation prevents invented lodging notes from bypassing
overnight accommodation. Explicit day/meal specialty edits must remain positively visible
in the correct meal; this is text compliance, not menu verification. Hours repair now
rejects conflicting partial candidates early and may backfill a flexible prefix within a
six-timing cap. Venue/cost/transport duration/gaps are preserved; numeric requested time/
duration, booking, named-prefix, meal-band and locked-day protections remain. Flexible
visits can shorten, which is not a guarantee about every unstated preference. Every
accepted candidate still requires complete static validation and fresh permitted routes.

Third-version targets: exclusions 6/6, memory 4/4, revision 13/13; 171.7s, 24 calls,
208,709 tokens, 61/66 executed research tools, 1 cache hit, 3 degraded outcomes.
Smoke: **5/5 cases, 35/35 checks**, 270.0s, 37 calls, 375,856 tokens (172,478 cached),
92/99 research tools, no hits/retries/failures. All five target and seven smoke traces
persisted in Phoenix with complete parents. Neither target nor smoke is an efficiency A/B.

Live packed-prefix fault experiment: genuine model, Places, timezone and Routes calls;
the bad parsed candidate alone is injected twice. Observed museum closing 17:00 rejects
18:00-19:00; accepted museum becomes 16:00-17:00, park 16:00-17:00 becomes 14:30-15:00.
Costs/venues/frame remain, one candidate and three successful route passes. Six real
model calls, 45,876 tokens; 13/14 research tools, one run cache hit, no failures/retries.
Trace `5adf8c6b9bf9423d90610316356ea3cb`: 46 persisted spans, complete parents, OK root,
accepted `repair.schedule`, explicit `experiment.inject_schedule`. This closes live
dependency integration evidence, not natural model recovery-frequency evidence.

Reports (workspace-local, ignored):

- `backend/.eval/phase1-6luna-v3-target-2026-10-01.json`
- `backend/.eval/phase1-6luna-v3-smoke-2026-10-01.json`
- `backend/.eval/phase1-6luna-v3-live-hours-packed-2026-10-01.json`
- `backend/.eval/phase1-6luna-v3-target-smoke-hours-phoenix-2026-10-01.json` (13 traces)

Refreshed full passed **9/9 cases, 75/75 checks**, 560.1s, 83 calls, 775,970 tokens
(357,098 cached), 163/178 research tools, 7 hits, no retries and 3 degraded outcomes.
All 16 planning traces persisted with complete parents; impossible-budget correctly
retains an infeasible/error root. Report `phase1-6luna-v3-full-2026-10-01.json` and
its separate `phase1-6luna-v3-full-phoenix-2026-10-01.json` audit are preserved.
The full/smoke release gate is closed for this implementation and fixed case set.
The two-repetition paired smoke benchmark completed on unchanged application, suite and
harness sources (`source_unchanged: true`). Same model/cases/checks/date, cold per-arm
cache and isolated preferences, seeded/counterbalanced order; serial research/no shared
cache control versus current runtime. Local deduplication, budgets/retries and mandatory
route validation were identical. No concurrent paid evaluation ran during the benchmark.

| Metric | Serial/no-shared-cache control | Current runtime |
|---|---:|---:|
| Passed samples | 10/10 | 10/10 |
| Passed checks | 70/70 | 70/70 |
| Summed end-to-end seconds | 749.8 | 604.2 |
| LLM calls | 82 | 77 |
| Tokens | 1,020,134 | 838,174 |
| Executed research tools | 185 | 183 |
| Research cache hits | 2 | 3 |
| Degraded research tool outcomes | 3 | 6 |

Summed time was **19.4% lower**; median paired current/control time ratio was **0.8838**
(11.6% lower). Both arms passed every quality check, with fewer research executions and
lower summed/median-paired time: the predeclared small-sample gate passed. All 28 planning
traces persisted in Phoenix with complete parent links, matching implementation fingerprint
and OK roots. Reports:

- `backend/.eval/phase1-6luna-v3-paired-2026-10-01.json`
- `backend/.eval/phase1-6luna-v3-paired-phoenix-2026-10-01.json`

Research counters exclude mandatory validation Routes; elapsed time includes them.
One of ten pairs was slower, and current had more degraded research outcomes. Real model
iterations and provider/fact variance remain; neither all latency savings nor lower token
use can be attributed solely to concurrency/cache. Two repetitions do not establish
statistical production reliability, a general regression bound or actual cost savings.
Model/Maps prices and bills remain unknown. Release, controlled efficiency **sample** and
marked live-hours fault-injection integration gates are closed for this source/case set;
natural recovery frequency remains unmeasured.

Android's changed `PlanDto.kt` and `PlanDtoTest.kt` were compiled together with the existing
Kotlin compiler/serialization plugin and declared cached libraries: **14 JUnit contract
tests passed**, including nullable lodging confirmation round trips. Cached dependencies
were read-only; all new classes/temp files stayed inside the workspace, without Gradle,
downloads or global-cache writes. The unchanged compiled `ApiJson` was reused. This is
scoped DTO verification, not a full Android suite or fresh APK build.

### Retained failures during this acceptance task

First refreshed smoke passed 5/5 (35/35); ensuing full **7/9 (72/75)**: Galleria Umberto
and Sullivan's Castle Island closing times, plus missing lodging in both New Orleans
turns. Full: 79 calls, 831,088 tokens, 179/193 tools, 4 hits; all 16 traces persisted.
The first lodging/candidate fix's smoke was **2/5 (32/35)**: late Galleria and Hauser &
Wirth visits plus missing po-boy edit text. All seven traces persisted. Subsequent prefix/
meal fixes have regression coverage and a changed implementation, not a same-code rerun.
Original reports `phase1-6luna-full-2026-10-01.json` and
`phase1-6luna-v2-smoke-2026-10-01.json` are preserved. An initial successful live-hours
plan failed its immediate Phoenix read due to indexing delay; read-only re-verification
confirmed that same trace, with no new paid requests or report overwrite.

## Latest removal/hour patch: target passed, refreshed release gate pending

The current implementation checks explicit run-scoped venue removals in activity
titles/locations/highlights and prunes complete excluded recommendation items only on
editable days. It cannot hide a real visit by renaming it; remaining visits still block.
After a model repair repeats a closure, a bounded observed-hours fallback can propose
feasible same-venue times or observed same-price-band restaurants, protecting locked
days, named venue requests, reservation signals and detected dietary restrictions.
All candidates require full static checks and fresh verification of changed transfers.
Missing/failed route measurements reject automatic changes and restore original evidence.
This is a conservative bounded fallback, not a general constraint solver or a guarantee
that arbitrary user requests have feasible solutions. Missing exact prices stay estimates.

Backend: **582 passed, 1 opt-in MCP deselected**; Ruff/format/diff pass. A single
changed-version revision-sequence target passed **29/29**, 117.2s, 22 calls, 154,324
tokens (58,291 cached), 33/35 research tools executed, 2 cache hits, zero retries/failures.
Model is gpt-6-luna; price schedule absent, cost unknown. Six OK-root planning traces
persisted in Phoenix with complete parent links. No `repair.schedule` span occurred:
this live run did not exercise the new fallback. Its successful/rejected branches and
final evidence binding have deterministic offline tests, not live recovery evidence.

Report: `backend/.eval/phase1-edit-intent-hours-fix-2026-10-01.json`.
Trace implementation hash: `3250e2a205656fc7e449865398d4f1f5fb6e84191ec2b0c3050d3c1d19987f9a`.
The deletion assertion now checks day 1, matching the request, and still rejects actual
day-1 visits/recommendations. Default global checks and locked-day checks remain intact.
Suite hash consequently changed to
`990a218451c57da04b82eca9e57007f1361b1d3f1bea1c66e9f1483068773dd7`.
Thus the older 27/29 and new 29/29 are not an identical-check causal A/B. Current-version
smoke/full were not rerun; the last full failure below is retained, not marked green.
Release and controlled efficiency acceptance remain pending. Android was not changed
or rebuilt in this turn; no dependencies, commit or push.

## Pre-removal/hour-patch live gate: retained smoke pass and full failure

The measured-transfer repair now supplies explicit local departure/earliest-arrival
bounds and the measured mode, and explains that a mode on a food/rest activity is not
a transport leg. Actual gaps, fresh route checks, opening hours, budget and locked-day
protection remain enforced. Backend regression: **546 passed, 1 opt-in MCP deselected**;
Ruff/format pass. Android was not changed or rebuilt in this turn.

Current configuration is **gpt-6-luna**, unlike the retained gpt-5.6-luna runs. One target
passed memory-recall **4/4** (110.0s, 11 calls, 138,941 tokens). The LA turn initially had
six measured transfer failures and an over-budget finding; one constraint repair cleared
them on complete revalidation. The target command inherited old operator rates: its
`cost_usd` is only an old-rate conversion, not a supported current-model estimate.
Actual cost is unknown. Subsequent smoke/full reports omit prices.

The fixed five-case smoke passed **5/5 cases, 35/35 checks**: 299.2s, 36 calls, 362,726
tokens (150,116 cached), 90/103 research tools executed, zero cache hits and 2 degraded
outcomes. All seven planning traces persisted in Phoenix with complete parent links.

The subsequent nine-case full gate remained red: **8/9 cases, 73/75 checks**. All five
smoke cases, forecast horizon, opening hours and honest impossible-budget reporting
passed. Chained revisions failed two checks: removing Grand Central Market left a text
reference (step 2), and Blue Daisy dinner was 17:00-18:00 although published Wednesday
hours were 08:00-15:00 (step 3). This is not the former zero-gap failure. The step 3 trace
`4f5bf2a35b684640b15bb7af13fd26e1` correctly retains an infeasible/error root; step 2
`c1f79c23462f43a3a9b5e289bb204fd4` is structurally feasible but fails user edit intent.
Thus an OK trace root alone is not proof that all product/eval checks passed.

Full totals: 84 calls, 888,500 tokens (402,686 cached), 191/209 research tools executed,
3 cache hits, 1 retry and 3 research-tool failures. All **16** planning traces were
verified in Phoenix. Recorded elapsed time is 5,595.4s, including abnormal tool waits
overlapping the memory and forecast-horizon cases, plus weather 503/route degradation.
It is **not** a usable normal-latency benchmark. Neither gate was rerun to select a
better stochastic outcome. Reports retain the original failures and raw timing:

- `backend/.eval/phase1-memory-transfer-fix-2026-10-01.json`
- `backend/.eval/phase1-transfer-fix-smoke-2026-10-01.json`
- `backend/.eval/phase1-transfer-fix-full-2026-10-01.json`

All three used the same implementation/suite. Trace implementation fingerprint:
`d68205fca7030e6f98d56ddf1afd7b41923f4511fbd3ca16e13e5d122c44f0ed`.
Historical smoke comparison flags the changed model/rates: calls rose 32->36 and tools
81->90 while quality recovered 4/5->5/5. No causal efficiency improvement is established.
At that checkpoint, remaining gates were chained-revision repair and live efficiency.
The newer target above does not replace a refreshed full gate.

## Retained pre-transfer-fix release failure

Authorized five-case smoke: **4/5 cases, 34/35 checks**, 546.7 seconds, 32 LLM calls,
409,080 tokens (151,287 cached), 81/81 research tools executed, zero cache hits and
7 degraded tool outcomes. Estimated model cost: **$0.10634934**, using historical
operator rates 0.20/0.02/1.20 USD per million input/cached/output tokens, not asserted
current prices or an actual bill. Maps and long-context charges are excluded.

Budget, specifics, exclusions and revision passed. Memory-recall's preference checks
passed but hard-constraint feasibility failed: Holiday Lodge → Fixins Soul Kitchen had
a zero-minute gap while the measured requirement was 40 minutes. All three repair
outputs parsed, so no format correction was used; this is a remaining schedule-repair
failure, not an invalid JSON or memory-recall failure. No full run followed the red gate,
and the smoke was not rerun to select a better stochastic result.

Raw report: `backend/.eval/phase1-release-smoke-2026-10-01.json`. All seven planning
traces were verified in Phoenix. Failed trace: `b259d1ad7246453fa1da25148bf79775`,
84 persisted spans, three repair nodes, infeasible/error root and stable failure codes.

## Historical live comparison: descriptive, not causal

Same five case IDs, model, reference date, Maps setting, suite hash and token rates:

| Report | Cases | Calls | Seconds | Tokens | Estimated model USD |
|---|---:|---:|---:|---:|---:|
| Earlier all-green smoke subset | 5/5 | 35 | 477.1 | 379,198 | 0.09912090 |
| Prior tool-planning smoke | 3/5 | 37 | 543.1 | 413,689 | 0.10366972 |
| Pre-transfer-fix smoke | 4/5 | 32 | 546.7 | 409,080 | 0.10634934 |

Compared with the all-green baseline, calls fell by 3, but time rose by 69.6 seconds,
tokens by 29,882 and one case regressed. Compared with the immediate red baseline,
one case recovered and calls fell by 5, but time/cost did not improve. Thus **lower
calls/latency with no quality regression is not demonstrated**. Model and live upstream
variation are not isolated, so these deltas cannot be attributed solely to this change.
Older reports without tool metrics are `unknown`, not zero.

From `backend/`, without external calls:

```powershell
python -m scripts.compare_evals ../docs/eval-runs/strengthening-after.json .eval/phase1-release-smoke-2026-10-01.json
python -m scripts.compare_evals .eval/phase3-smoke.json .eval/phase1-release-smoke-2026-10-01.json
```

The script joins shared case IDs, reports setting mismatches/missing metadata and quality
regressions, and explicitly does not certify a causal efficiency improvement.

## Frozen-fixture mechanics: demonstrated within this workload

The offline experiment uses the existing dev environment, fixed scripted model turns,
fixed tool facts and a 50ms artificial tool delay. Each sample executes two identical
planning requests with four independent place lookups each. The control serializes
execution and disables shared caching; the candidate uses the current bounded parallel
runtime and shared cache. Both arms start with an empty cache; only the second request
can reuse the first. Paired arm order is seeded/randomized, with five repetitions each.
External model/travel APIs and telemetry export are disabled.

| Arm | Median pair time | Tool executions per pair | Scripted model calls |
|---|---:|---:|---:|
| Serial, no shared cache | 498.9ms | 8 | 4 |
| Current runtime | 78.2ms | 4 | 4 |

All itinerary and validation hashes matched. This demonstrates a 50% reduction in fixture
tool executions and about 84% lower median elapsed time **for this artificial workload**.
It does not prove real model quality, provider billing savings, production latency or
all tool-policy changes. There are no paid model calls in this experiment.

```powershell
# From backend; the output must be a new workspace-local file:
python -m scripts.benchmark_tools --json .eval/new-tool-benchmark.json
```

Retained samples: `backend/.eval/phase1-tool-benchmark-2026-10-01.json`.
Backend regression at the earlier verification-script checkpoint: 541 passed, 1 opt-in MCP
deselected; Ruff/format pass. Android was not changed or rebuilt in this verification turn.
