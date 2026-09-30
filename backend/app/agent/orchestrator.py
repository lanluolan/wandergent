"""Planning orchestration as a LangGraph state graph.

A run has **two cycles** -- gather/tools and validate/repair -- which as nested loops with
break conditions would bury the control flow inside the code doing the work:

    START -> gather -> (tool calls?) -> run_tools -> gather
                    -> parse -> (parsed?) -> validate
                             -> emit -> (parsed?) -> validate
                                     -> emit (one retry)
             validate -> (violations and repairs left?) -> repair -> validate
                      -> finish

LangGraph only, not the LangChain stack: it brings `langchain-core` for base types and
nothing else. The prompts stay here, in plain sight -- the whole reason for avoiding the
higher-level abstractions.

Nodes emit through LangGraph's custom stream writer and `stream_plan` forwards them, so
the SSE contract is independent of the graph's shape.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from time import monotonic
from typing import Any, TypedDict

import httpx
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from openai import AsyncOpenAI

from app.agent import brief
from app.agent.constraints import TripConstraints, resolve_constraints, starts_new_trip
from app.agent.events import PlanEvent
from app.agent.llm import (
    TRUNCATION_ERROR,
    PlanningConfigError,
    PlanningError,
    PlanningTimeout,
    Turn,
    assistant_message,
    build_client,
    is_empty,
    parse_turn,
    safe_arguments,
    stream_turn,
)
from app.agent.results import (
    PlanResult,
    RunWarning,
    ToolCallRecord,
    ToolUsage,
    Usage,
    no_itinerary,
    tool_calls_dropped,
    tool_calls_spent,
    tool_rounds_spent,
)
from app.agent.revision import (
    RevisionScope,
    enforce_revision_scope,
    resolve_revision_scope,
    revision_payload,
)
from app.agent.schemas import Itinerary, itinerary_schema_json
from app.agent.transfers import confirm_transfers
from app.agent.validation import (
    MIN_TRANSFER_MINUTES,
    ValidationReport,
    transfer_candidates,
    validate_itinerary,
)
from app.config import settings
from app.memory import store as memory_store
from app.memory.store import PreferenceStore
from app.tools.base import BAD_REQUEST, ToolOutcome
from app.tools.cache import CachedToolResult, collected_now, shared_tool_cache
from app.tools.memory import recall_block
from app.tools.registry import (
    TOOL_SCHEMAS,
    call_tool,
    shared_cache_ttl,
    tool_cache_key,
    tool_capacity,
    tool_priority,
    uses_shared_http_client,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PlanResult",
    "PlanningConfigError",
    "PlanningError",
    "PlanningTimeout",
    "ToolCallRecord",
    "plan_trip",
    "stream_plan",
]

# One repair round. A model that cannot fix its own JSON twice will not fix it on a
# third try either; failing loudly beats burning tokens.
MAX_EMIT_ATTEMPTS = 2

# The other half of the cap: a round carries any number of calls, so the round budget
# bounds turns and nothing else -- fourteen searches at once stays inside it while costing
# a run three times the size. Sized off a thorough run (weather, a handful of searches, a
# few routes), so hitting it means something went wrong, not that the trip was hard.
MAX_TOOL_CALLS = 16

# What to tell the model when the round budget is about to run out. Without it the loop
# just stops answering and the model is asked for an itinerary mid-research -- observed
# live twice, both times returning no plan at all.
LAST_ROUND_NOTICE = (
    "This is your last round of tool calls. Ask for anything still genuinely missing "
    "now, then write the complete itinerary from what you have. Do not wait for more "
    "data: where something is unverified, choose a sensible option and say so in the "
    "notes rather than leaving the plan unfinished."
)

# A repair can fix the named defect while creating a new adjacent transfer or hours
# conflict. Three bounded passes cover that observed chain; validation runs after each,
# and an unresolved plan still ships with its findings instead of looping indefinitely.
MAX_CONSTRAINT_REPAIRS = 3

SYSTEM_PROMPT = """You are Wandergent, a travel planning assistant.

Today is {today} ({weekday}). Resolve relative dates such as "next month" or "this \
weekend" against that date.

Work in two steps.

Step 1 - gather facts. Call the available tools for anything you should not guess. \
Call get_weather_forecast for the destination and trip dates before committing to \
outdoor time. If the request reveals something durable about this traveller -- a \
taste, something they avoid, a constraint, who they travel with -- call \
remember_preference so future trips start from it. If a tool reports ok=false, do not \
retry it in a loop: carry on and record the gap in the plan's notes.

Treat the tool budget as a research budget. Ask for independent weather, venue and route \
facts together in one turn so they can run concurrently. Mark search_places as required \
only for venues you intend to schedule; mark searches for extra alternatives optional, \
and skip optional research when required facts are still missing. Equivalent calls are \
normalized and cached, so never rephrase a query merely to force another lookup.

After each round of searches you are given a short brief of everything verified so far \
-- opening hours, price bands, and how far apart the places are. **Plan from that \
brief.** It is the same data the searches returned, in the form the schedule needs.

Step 2 - when you have what you need, answer with the itinerary as a single JSON \
object matching this schema exactly. No prose, no markdown fences.

{schema}

Rules for the plan:
- Honour the stated budget, interests and exclusions. Anything the user rules out \
must not appear at all.
- Give every activity a start_time and an end_time.
- **Consecutive activities in different places need at least {min_transfer} minutes \
between them**, and more across a large city or at a busy hour. If two things really \
are next door, say so by putting an explicit transport activity between them. This is \
checked against real travel times, and a plan that fails it is sent back to you.
- Put estimated_cost on each activity, covering the whole party, in the trip \
currency. Do not compute totals yourself; they are derived from the activities.
- **Only genuinely free things cost 0.** search_places reports a price level; if it \
says a venue costs anything at all, estimate what it costs rather than entering 0. A \
budget built on zeros is not within budget.
- When the forecast shows rain, prefer indoor options and say so in that day's \
weather field.
- When no forecast is available (the trip is more than 16 days out), plan against \
seasonal norms and say so in notes.
- Name the actual place, and check it exists. Call search_places for the meals, \
hotels and sights you intend to schedule, and use the names and addresses it returns \
rather than ones you recall. One search can cover several slots -- do not spend a \
round per activity. If search_places is unavailable, say in notes that venues are \
unverified.
- Never hedge with "or similar", "or nearby", "some restaurant". Commit to one \
choice. If you are unsure it still exists, pick it anyway and put the caveat in notes \
-- a named guess the traveller can check beats a vague one they cannot.
- A trip with overnight stays needs an accommodation activity for each night, with a \
named hotel or area, unless the user says lodging is already handled.
- search_places returns opening hours. **Schedule inside them, to the hour.** Two \
different mistakes, both checked: a venue shut on the day you wanted it, and a venue \
open that day but not yet open at the time you picked. A 09:00 breakfast at somewhere \
that opens at 11:00 fails exactly like a Monday visit to a place shut on Mondays. Read \
the hours for the specific weekday of the visit, put the activity wholly inside them, \
and if it does not fit, move it or choose somewhere else -- do not schedule it anyway \
and note the problem.
- Use highlights for the specifics that make a choice worth it: the dishes to order, \
the exhibits worth the queue, what to book ahead. Give them to whatever the user said \
they care about -- if they mention food, every restaurant gets dishes.
- Write user-facing text in English, unless the traveller wrote to you in another \
language, in which case answer in theirs."""

REVISION_RULE = """You are **revising an itinerary the traveller already has**, not \
writing a new one.

Change only what they asked for, plus whatever must change as a direct consequence. \
Every other activity keeps its time, its venue, its cost and its highlights exactly as \
they are -- do not reword, reorder or "improve" anything they did not mention. Keep the \
destination, dates, traveller count, currency and budget unless the change is about one \
of those.

If their change makes the plan infeasible -- over budget, no longer enough travel time \
-- make the smallest further adjustment that fixes it, and say in notes what you \
changed and why.

Use tools when the change needs a fact you do not have: a replacement venue must come \
from search_places like any other, and a new location may need its travel time checked.

If they are clearly asking for a different trip rather than an edit -- another city, \
other dates -- ignore the itinerary below and plan afresh."""

REVISION_REQUEST = """This is my current itinerary:

{itinerary}

Now change it: {request}"""

CONTEXT_POLICY = """Context priority, highest first:
1. System rules and request-owned hard constraints.
2. The traveller's current request.
3. Verified tool facts from this run, including their source and collection time.
4. The editable part of the previous itinerary.
5. Relevant durable preferences; the current request overrides them.

Conversation history is not retained between requests. A revision receives only the
previous itinerary, the current edit and durable preferences. Tool work is bounded by
{tool_calls} calls; verified venue briefs are bounded by {venues} venues; durable memory
is bounded by its recall cap. Locked days may be compacted because the server restores
their exact content after every model turn."""

CURRENCY_RULE = """The traveller settles up in {currency}. Set the itinerary's \
currency field to {currency} and estimate every cost in it, whatever the destination \
uses locally. If they state a budget in another currency, treat the amount as {currency} \
unless they name a unit, and say so in notes."""

EMIT_INSTRUCTION = """Now return the finished itinerary as a single JSON object \
matching this JSON Schema. Output JSON only.

{schema}"""

REPAIR_INSTRUCTION = """That JSON did not validate:

{errors}

Return the corrected JSON object only."""

CONSTRAINT_REPAIR_INSTRUCTION = """That itinerary is well-formed but not feasible:

{violations}

Fix every point above and return the corrected JSON object only. Keep everything that \
was already fine -- do not rewrite the whole trip. Before returning, scan the entire \
repaired plan again for budget, overlaps, opening hours and every consecutive real-stop \
transfer. Moving one item must not create a new zero-gap hop or another violation."""


class PlanState(TypedDict, total=False):
    constraints: TripConstraints
    """Everything one planning run carries between nodes.

    The LLM client lives here rather than in a context schema: there is no checkpointer,
    so nothing is serialised and a live client is safe to hold -- which lets a node read
    all its inputs from one place.
    """

    llm: AsyncOpenAI
    model: str
    #: Cheaper model for the first, tool-choosing turn. Empty disables routing.
    fast_model: str
    schema: str
    #: Whose memory this run reads and writes. Empty for anonymous requests.
    user_id: str

    messages: list[dict]
    records: list[ToolCallRecord]
    dropped_tool_names: list[str]
    warnings: list[RunWarning]
    #: Accumulated across every node that talks to the model.
    usage: Usage

    # Tool loop. `rounds_total` is kept alongside the countdown so the warning can name
    # the budget this run was given, not the global default.
    rounds_total: int
    rounds_left: int
    #: Tool calls executed so far, across every round. See MAX_TOOL_CALLS.
    calls_made: int
    pending: list[Any]
    #: The turn that ended the tool loop. Kept whole rather than as its text: whether
    #: it was cut off decides what the repair round is told.
    last_turn: Turn | None
    #: Results of tool calls already made this run, keyed by name + arguments.
    tool_cache: dict[str, CachedToolResult]
    #: Venue name -> Google's opening-hours lines, harvested from `search_places`. The run
    #: already paid for this; keeping it lets the constraint layer check opening times
    #: against Google rather than against the model's account of them.
    place_hours: dict[str, list[str]]
    place_prices: dict[str, str]
    #: Venue name -> (latitude, longitude), harvested from `search_places`. Rendered
    #: into a distance block so the schedule is written with spatial facts in hand.
    place_points: dict[str, tuple[float, float]]
    place_addresses: dict[str, str]
    fact_collected_at: str
    #: How many venues the brief last covered, so it is only re-sent when the picture
    #: actually changed rather than once per tool round.
    brief_covered: int

    # Emission
    itinerary: Itinerary | None
    raw: str
    parse_errors: str | None
    emit_attempts: int
    #: The last reply hit the output limit. The rewrite has to be told to be shorter,
    #: or it comes back the same size and gets cut in the same place.
    truncated: bool

    # Constraint loop
    report: ValidationReport | None
    repairs_left: int
    previous: Itinerary | None
    revision_scope: RevisionScope | None


def _tool_key(call) -> str:
    """Normalized identity; malformed argument text remains distinct and uncached."""
    try:
        arguments = json.loads(call.arguments or "{}")
    except (TypeError, json.JSONDecodeError):
        return f"{call.name}:invalid:{call.arguments!r}"
    if not isinstance(arguments, dict):
        return f"{call.name}:invalid:{call.arguments!r}"
    return tool_cache_key(call.name, arguments)


def _outcome_evidence(name: str, outcome: ToolOutcome) -> tuple[str, ...]:
    """Small, non-sensitive identifiers used to infer whether facts reached the plan."""
    payload = outcome.model_dump()
    if name == "search_places":
        return tuple(
            str(value)
            for place in payload.get("places") or []
            if isinstance(place, dict)
            for value in (place.get("name"), place.get("address"))
            if value
        )
    if name == "get_weather_forecast":
        return tuple(
            f"{day.get('date')}\t{day.get('condition')}"
            for day in payload.get("days") or []
            if isinstance(day, dict) and day.get("date") and day.get("condition")
        )
    if name == "get_travel_time":
        return tuple(
            str(value) for value in (payload.get("origin"), payload.get("destination")) if value
        )
    return ()


def _records_with_contribution(
    records: list[ToolCallRecord], itinerary: Itinerary | None
) -> list[ToolCallRecord]:
    """Mark only uses that can be proven from the shipped structured itinerary."""
    if itinerary is None:
        return records
    rendered = itinerary.model_dump_json().casefold()
    weather_by_date = {
        str(day.date): day.weather.casefold() for day in itinerary.days if day.weather
    }
    marked: list[ToolCallRecord] = []
    for record in records:
        used = False
        if record.ok and record.evidence:
            if record.name == "get_weather_forecast":
                used = any(
                    date_value in weather_by_date
                    and condition.casefold() in weather_by_date[date_value]
                    for value in record.evidence
                    for date_value, separator, condition in (value.partition("\t"),)
                    if separator
                )
            elif record.name == "search_places":
                used = any(value.casefold() in rendered for value in record.evidence)
            elif record.name == "get_travel_time" and len(record.evidence) >= 2:
                used = all(value.casefold() in rendered for value in record.evidence[:2])
        marked.append(record.model_copy(update={"contributed": used}))
    return marked


def _places_in(payload: str | dict) -> list[dict]:
    """The venue dicts inside a serialised `search_places` reply, or nothing.

    Reads the serialised reply, not the tool's return value: it is what the loop has in
    hand and the same string the model sees. Never raises -- an unreadable payload
    contributes nothing rather than failing the plan over bookkeeping.
    """
    if isinstance(payload, dict):
        parsed = payload
    else:
        try:
            parsed = json.loads(payload)
        except (TypeError, ValueError):
            return []
    # A replayed cache hit wraps the original reply one level down.
    if isinstance(parsed, dict) and "result" in parsed and "places" not in parsed:
        return _places_in(parsed.get("result") or "")
    if not isinstance(parsed, dict):
        return []
    return [place for place in parsed.get("places") or [] if isinstance(place, dict)]


def harvest_place_hours(tool: str, payload: str, into: dict[str, list[str]]) -> None:
    """Keep the opening hours a `search_places` result carried, so the constraint layer
    can check opening times against *Google* rather than the model's account of them."""
    if tool != "search_places":
        return
    for place in _places_in(payload):
        name, descriptions = place.get("name"), place.get("opening_hours")
        if name and descriptions:
            into[name] = list(descriptions)


def harvest_place_points(tool: str, payload: str, into: dict[str, tuple[float, float]]) -> None:
    """Keep the coordinates a `search_places` result carried.

    Coordinates turn into distances by arithmetic, so the model learns how far apart its
    candidates are without one extra API call. See `app/agent/proximity.py`.
    """
    if tool != "search_places":
        return
    for place in _places_in(payload):
        name = place.get("name")
        latitude, longitude = place.get("latitude"), place.get("longitude")
        if name and isinstance(latitude, int | float) and isinstance(longitude, int | float):
            into[name] = (float(latitude), float(longitude))


def harvest_place_prices(tool: str, payload: str, into: dict[str, str]) -> None:
    """Keep the price band a `search_places` result carried.

    Too coarse to price an activity from -- "MODERATE" is not a number -- but enough to
    catch a venue Google prices at all being budgeted at nothing. See
    `validation._check_price_levels`.
    """
    if tool != "search_places":
        return
    for place in _places_in(payload):
        name, level = place.get("name"), place.get("price_level")
        if name and isinstance(level, str) and level:
            into[name] = level


def harvest_place_addresses(tool: str, payload: str, into: dict[str, str]) -> None:
    """Keep display addresses for the bounded verified-facts brief."""
    if tool != "search_places":
        return
    for place in _places_in(payload):
        name, address = place.get("name"), place.get("address")
        if name and isinstance(address, str) and address:
            into[name] = address


async def _execute_tool_call(call, context: dict) -> tuple[ToolCallRecord, dict, CachedToolResult]:
    """Run one tool call and render both the audit record and the reply message."""
    started = monotonic()
    try:
        arguments = json.loads(call.arguments or "{}")
    except json.JSONDecodeError as exc:
        arguments = {}
        outcome: ToolOutcome = ToolOutcome(
            ok=False, error=f"arguments were not valid JSON: {exc}", code=BAD_REQUEST
        )
    else:
        if not isinstance(arguments, dict):
            outcome = ToolOutcome(
                ok=False, error="arguments must be a JSON object", code=BAD_REQUEST
            )
            arguments = {}
        else:
            outcome = await call_tool(call.name, arguments, context=context)

    elapsed_ms = round((monotonic() - started) * 1000)
    evidence = _outcome_evidence(call.name, outcome)
    record = ToolCallRecord(
        name=call.name,
        arguments=arguments,
        ok=outcome.ok,
        code=outcome.code,
        error=outcome.error,
        duration_ms=elapsed_ms,
        attempts=outcome.attempts,
        evidence=list(evidence),
    )
    content = outcome.model_dump_json()
    reply = {"role": "tool", "tool_call_id": call.id, "content": content}
    cached = CachedToolResult(
        content=content,
        ok=outcome.ok,
        code=outcome.code,
        error=outcome.error,
        evidence=evidence,
        collected_at=collected_now(),
    )
    return record, reply, cached


# --- nodes -------------------------------------------------------------------------


async def gather(state: PlanState) -> dict:
    """One tool-calling turn: let the model either ask for tools or answer.

    **Model routing lives here.** The first turn only reads the request and picks tool
    arguments, so it runs on the cheap model when one is configured. What makes that safe
    is `tool_choice="required"`: the cheap model is *only able* to emit a tool call, so it
    cannot produce a lower-quality plan, and the strong model does every turn that writes
    or repairs the itinerary. Forcing a call costs nothing -- the system prompt already
    asks for the weather first.
    """
    writer = get_stream_writer()
    first_turn = state["rounds_left"] == state["rounds_total"]
    routed = first_turn and bool(state.get("fast_model"))
    model = state["fast_model"] if routed else state["model"]

    # Sent with this turn but never written back into `messages`: a one-shot nudge about
    # the budget is not a fact about the trip, and keeping it out leaves the history the
    # same shape whether or not the loop ran long. Never on the opening turn, which would
    # tell the model to wrap up before it has asked anything.
    outgoing = list(state["messages"])
    if state["rounds_left"] <= 1 and not first_turn:
        outgoing.append({"role": "system", "content": LAST_ROUND_NOTICE})

    turn = Turn()
    async for event in stream_turn(
        state["llm"],
        model,
        turn,
        messages=outgoing,
        tools=TOOL_SCHEMAS,
        tool_choice="required" if routed else "auto",
    ):
        writer(event)

    # Trim to the call budget *before* the turn enters the history. Every declared tool
    # call must come back with a matching reply, so a message promising twenty while
    # sixteen run is malformed, not smaller -- the endpoint rejects the next request.
    allowed = max(0, MAX_TOOL_CALLS - state["calls_made"])
    dropped = []
    if len(turn.tool_calls) > allowed:
        # Spend the remaining quota on facts that can make or break the itinerary.
        # Stable index tie-breaking preserves model order among equally useful calls.
        seen = set(state.get("tool_cache") or {})
        ranked: list[tuple[int, int, Any]] = []
        for index, call in enumerate(turn.tool_calls):
            key = _tool_key(call)
            arguments = safe_arguments(call.arguments)
            ranked.append((tool_priority(call.name, arguments, repeated=key in seen), index, call))
            seen.add(key)
        kept_indices = {
            index for _, index, _ in sorted(ranked, key=lambda item: (-item[0], item[1]))[:allowed]
        }
        dropped = [call for index, call in enumerate(turn.tool_calls) if index not in kept_indices]
        turn.tool_calls = [
            call for index, call in enumerate(turn.tool_calls) if index in kept_indices
        ]

    update: dict = {
        # An empty turn is left out entirely rather than sent back; see `is_empty`.
        "messages": state["messages"]
        if is_empty(turn)
        else [*state["messages"], assistant_message(turn)],
        "rounds_left": state["rounds_left"] - 1,
        "usage": state["usage"].plus(turn.usage),
    }

    if dropped:
        # Never a silent truncation: the plan is built on fewer answers than the model
        # asked for, and the run has to say so.
        logger.warning("tool budget of %s reached; dropped %s calls", MAX_TOOL_CALLS, len(dropped))
        update["warnings"] = [
            *state["warnings"],
            tool_calls_dropped(MAX_TOOL_CALLS, [call.name for call in dropped], len(dropped)),
        ]
        update["dropped_tool_names"] = [
            *state.get("dropped_tool_names", []),
            *(call.name for call in dropped),
        ]

    if not turn.tool_calls:
        return {**update, "pending": [], "last_turn": turn}

    for call in turn.tool_calls:
        writer(
            PlanEvent(type="tool_call", name=call.name, arguments=safe_arguments(call.arguments))
        )
    update["calls_made"] = state["calls_made"] + len(turn.tool_calls)
    return {**update, "pending": turn.tool_calls}


async def run_tools(state: PlanState) -> dict:
    """Execute a bounded batch, reusing normalized results where they are still fresh."""
    writer = get_stream_writer()
    context = {"user_id": state.get("user_id") or ""}
    cache = dict(state.get("tool_cache") or {})

    # Collapse duplicates *before* starting tasks. The previous dict(zip(...)) collapsed
    # their results only after both upstream calls had already been paid for.
    unique: dict[str, Any] = {}
    for call in state["pending"]:
        key = _tool_key(call)
        if key not in cache:
            unique.setdefault(key, call)

    resolved: dict[str, tuple[str, CachedToolResult, ToolCallRecord | None, float | None]] = {}
    fresh: list[tuple[str, Any]] = []
    for key, call in unique.items():
        hit = shared_tool_cache.get(key) if shared_cache_ttl(call.name) > 0 else None
        if hit is not None:
            resolved[key] = ("shared", hit.value, None, hit.age_seconds)
        else:
            fresh.append((key, call))

    async def execute_bounded(key: str, call) -> tuple[str, tuple]:
        async with tool_capacity(call.name):
            return key, await _execute_tool_call(call, context)

    if fresh and any(uses_shared_http_client(call.name) for _, call in fresh):
        timeout = httpx.Timeout(settings.tool_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as batch_client:
            context["http_client"] = batch_client
            executed = await asyncio.gather(*(execute_bounded(key, call) for key, call in fresh))
    elif fresh:
        executed = await asyncio.gather(*(execute_bounded(key, call) for key, call in fresh))
    else:
        executed = []
    for key, (record, _reply, value) in executed:
        resolved[key] = ("miss", value, record, None)
        shared_tool_cache.put(key, value, shared_cache_ttl(record.name))

    messages = list(state["messages"])
    records = list(state["records"])
    hours = dict(state.get("place_hours") or {})
    prices = dict(state.get("place_prices") or {})
    points = dict(state.get("place_points") or {})
    addresses = dict(state.get("place_addresses") or {})
    fact_collected_at = state["fact_collected_at"]
    preexisting = set(cache)
    consumed: set[str] = set()

    for call in state["pending"]:
        key = _tool_key(call)
        if key in preexisting or key in consumed:
            source = "run"
            value = cache[key]
            age = None
            record = ToolCallRecord(
                name=call.name,
                arguments=safe_arguments(call.arguments),
                ok=value.ok,
                code=value.code,
                error=value.error,
                cache_status="run",
                attempts=0,
                evidence=list(value.evidence),
            )
        else:
            source, value, executed_record, age = resolved[key]
            consumed.add(key)
            cache[key] = value
            if source == "miss":
                assert executed_record is not None
                record = executed_record
            else:
                record = ToolCallRecord(
                    name=call.name,
                    arguments=safe_arguments(call.arguments),
                    ok=value.ok,
                    code=value.code,
                    error=value.error,
                    cache_status="shared",
                    cache_age_seconds=round(age or 0, 3),
                    attempts=0,
                    evidence=list(value.evidence),
                )

        if source == "miss":
            reply = {"role": "tool", "tool_call_id": call.id, "content": value.content}
        else:
            note = (
                "You already called this with equivalent arguments in this run."
                if source == "run"
                else "A recent successful result was reused within its freshness window."
            )
            try:
                replayed_result = json.loads(value.content)
            except (TypeError, ValueError):
                replayed_result = value.content
            reply = {
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(
                    {
                        "cache": source,
                        "repeat": source == "run",
                        "age_seconds": round(age, 1) if age is not None else None,
                        "note": f"{note} The result is unchanged -- use it and move on.",
                        "result": replayed_result,
                    },
                    ensure_ascii=False,
                ),
            }
            logger.info("tool %s reused from %s cache", call.name, source)

        records.append(record)
        messages.append(reply)
        harvest_place_hours(record.name, reply["content"], hours)
        harvest_place_prices(record.name, reply["content"], prices)
        harvest_place_points(record.name, reply["content"], points)
        harvest_place_addresses(record.name, reply["content"], addresses)
        if record.name == "search_places" and value.evidence and value.collected_at:
            fact_collected_at = min(fact_collected_at, value.collected_at)
        if not record.ok:
            logger.info("tool %s degraded: %s", record.name, record.error)
        writer(PlanEvent(type="tool_result", name=record.name, ok=record.ok, code=record.code))

    # A digest of everything verified so far -- hours, price bands, distances -- handed
    # over *before* venues and times are picked, rather than leaving the model to re-read
    # raw tool JSON while composing. Sent only when the set of known venues grew, so two
    # searches do not leave two near-identical briefs in the conversation.
    known = len({*points, *hours, *prices, *addresses})
    announced = state["brief_covered"]
    block = (
        brief.render(
            points,
            hours,
            prices,
            addresses,
            collected_at=fact_collected_at,
        )
        if known > announced
        else None
    )
    if block is not None:
        messages.append({"role": "system", "content": block})
        announced = known

    writer(PlanEvent(type="stage", name="composing", message="Building the itinerary"))

    warnings = list(state["warnings"])
    if state["rounds_left"] <= 0:
        warnings.append(tool_rounds_spent(state["rounds_total"]))
    elif state["calls_made"] >= MAX_TOOL_CALLS:
        # Spent exactly, nothing dropped. The loop still ends here, and ending research
        # early is a fact about the plan rather than an implementation detail.
        warnings.append(tool_calls_spent(MAX_TOOL_CALLS))

    return {
        "messages": messages,
        "records": records,
        "warnings": warnings,
        "pending": [],
        "tool_cache": cache,
        "place_hours": hours,
        "place_prices": prices,
        "place_points": points,
        "place_addresses": addresses,
        "fact_collected_at": fact_collected_at,
        "brief_covered": announced,
    }


async def parse(state: PlanState) -> dict:
    """Fast path: the turn that ended the tool loop is usually the itinerary already."""
    turn = state.get("last_turn") or Turn()
    itinerary, errors = parse_turn(turn)
    itinerary = enforce_revision_scope(
        itinerary, state.get("previous"), state.get("revision_scope")
    )
    return {
        "itinerary": itinerary,
        "parse_errors": errors,
        "raw": turn.content,
        "truncated": turn.truncated,
    }


async def emit(state: PlanState) -> dict:
    """Ask explicitly for the itinerary, with the schema attached and JSON mode on."""
    writer = get_stream_writer()
    messages = list(state["messages"])
    attempt = state["emit_attempts"]

    if attempt == 0:
        logger.info("no usable itinerary from the tool stage (%s)", state.get("parse_errors"))
        writer(PlanEvent(type="stage", name="composing", message="Writing it up as an itinerary"))
        instruction = EMIT_INSTRUCTION.format(schema=state["schema"])
        # The tool-stage reply was not malformed, it was too long. Asking again without
        # saying so buys an identical reply cut in the same place, and there are only two
        # attempts.
        if state.get("truncated"):
            instruction += "\n\n" + TRUNCATION_ERROR
        messages.append({"role": "user", "content": instruction})
    else:
        writer(PlanEvent(type="stage", name="repairing", message="Fixing the format"))
        messages.append(
            {
                "role": "user",
                "content": REPAIR_INSTRUCTION.format(errors=state.get("parse_errors")),
            }
        )

    turn = Turn()
    async for event in stream_turn(
        state["llm"],
        state["model"],
        turn,
        messages=messages,
        response_format={"type": "json_object"},
    ):
        writer(event)

    messages.append(assistant_message(turn))
    itinerary, errors = parse_turn(turn)
    itinerary = enforce_revision_scope(
        itinerary, state.get("previous"), state.get("revision_scope")
    )
    if itinerary is None:
        logger.info("itinerary failed to parse on attempt %s: %s", attempt + 1, errors)

    return {
        "messages": messages,
        "itinerary": itinerary,
        "parse_errors": errors,
        "raw": turn.content,
        "emit_attempts": attempt + 1,
        "truncated": turn.truncated,
        "usage": state["usage"].plus(turn.usage),
    }


async def validate(state: PlanState) -> dict:
    """Hard constraints. Parsing proved it well-formed; this proves it feasible."""
    writer = get_stream_writer()
    if state.get("report") is None:
        writer(PlanEvent(type="stage", name="validating", message="Checking the itinerary"))

    report = validate_itinerary(
        state["itinerary"],
        state.get("place_hours"),
        state.get("place_prices"),
        constraints=state.get("constraints"),
    )
    # The heuristic proposes, measurement disposes. Add advisory candidates for the hops
    # the string heuristic considered safe, then measure both sets within the route-call
    # cap. Without a maps key the original findings and advisories remain visible.
    report = transfer_candidates(state["itinerary"], report)
    report = await confirm_transfers(report, allowed_modes=state["constraints"].allowed_modes)
    writer(PlanEvent(type="validation", violations=report.violations))
    return {"report": report}


async def repair(state: PlanState) -> dict:
    """Feed the violations back. The result is re-validated, never taken on trust."""
    writer = get_stream_writer()
    report = state["report"]
    logger.info("constraint violations, repairing: %s", [v.code for v in report.violations])
    writer(PlanEvent(type="stage", name="repairing", message="Resolving conflicts"))

    # The itinerary is already the last assistant turn -- appending a copy of it here
    # would put two assistant messages back to back, which is a malformed conversation.
    # Observed live: the model responded by echoing the JSON Schema instead of a plan.
    messages = [
        *state["messages"],
        {
            "role": "user",
            "content": CONSTRAINT_REPAIR_INSTRUCTION.format(violations=report.as_instructions()),
        },
    ]

    turn = Turn()
    async for event in stream_turn(
        state["llm"],
        state["model"],
        turn,
        messages=messages,
        response_format={"type": "json_object"},
    ):
        writer(event)
    messages.append(assistant_message(turn))

    repaired, parse_errors = parse_turn(turn)
    repaired = enforce_revision_scope(repaired, state.get("previous"), state.get("revision_scope"))
    if repaired is None:
        # The repair came back malformed. Keep the plan we have -- it is at least
        # well-formed -- and let the unresolved violations ship in the report.
        logger.info("constraint repair produced invalid JSON: %s", parse_errors)
        return {
            "messages": messages,
            "repairs_left": 0,
            "usage": state["usage"].plus(turn.usage),
        }

    return {
        "messages": messages,
        "itinerary": repaired,
        "repairs_left": state["repairs_left"] - 1,
        "usage": state["usage"].plus(turn.usage),
    }


async def finish(state: PlanState) -> dict:
    """Emit the single terminal event the whole contract is built around."""
    writer = get_stream_writer()
    warnings = list(state["warnings"])
    report = state.get("report")
    itinerary = state.get("itinerary")
    records = _records_with_contribution(list(state["records"]), itinerary)
    tool_usage = ToolUsage.from_records(
        records, dropped_tools=list(state.get("dropped_tool_names") or [])
    )

    if itinerary is None:
        warnings.append(no_itinerary())
        writer(
            PlanEvent(
                type="result",
                result=PlanResult(
                    itinerary=None,
                    constraints=state["constraints"],
                    tool_calls=records,
                    tool_usage=tool_usage,
                    warnings=warnings,
                    raw_reply=state.get("raw"),
                    usage=state["usage"],
                ),
            )
        )
        return {"warnings": warnings}

    # The validation report is *not* folded into `warnings`. It travels whole, in
    # `validation`, where each finding keeps its code, day and measured minutes -- copying
    # the messages across too made the same finding arrive twice in two renderings.

    writer(
        PlanEvent(
            type="result",
            result=PlanResult(
                itinerary=itinerary,
                constraints=state["constraints"],
                tool_calls=records,
                tool_usage=tool_usage,
                warnings=warnings,
                validation=report,
                usage=state["usage"],
            ),
        )
    )
    return {"warnings": warnings}


# --- edges -------------------------------------------------------------------------


def after_gather(state: PlanState) -> str:
    """Tools requested -> run them; otherwise try to read the answer as an itinerary."""
    return "run_tools" if state["pending"] else "parse"


def after_tools(state: PlanState) -> str:
    """Keep looping while both budgets hold; the caps are what stop a confused model."""
    if state["rounds_left"] <= 0 or state["calls_made"] >= MAX_TOOL_CALLS:
        return "parse"
    return "gather"


def after_parse(state: PlanState) -> str:
    return "validate" if state["itinerary"] is not None else "emit"


def after_emit(state: PlanState) -> str:
    if state["itinerary"] is not None:
        return "validate"
    return "emit" if state["emit_attempts"] < MAX_EMIT_ATTEMPTS else "finish"


def after_validate(state: PlanState) -> str:
    report = state["report"]
    if report.ok or state["repairs_left"] <= 0:
        return "finish"
    return "repair"


def after_repair(state: PlanState) -> str:
    """Always re-validate: a repair is a claim, and claims get checked."""
    return "validate"


def _build_graph():
    builder = StateGraph(PlanState)

    builder.add_node("gather", gather)
    builder.add_node("run_tools", run_tools)
    builder.add_node("parse", parse)
    builder.add_node("emit", emit)
    builder.add_node("validate", validate)
    builder.add_node("repair", repair)
    builder.add_node("finish", finish)

    builder.add_edge(START, "gather")
    builder.add_conditional_edges("gather", after_gather, ["run_tools", "parse"])
    builder.add_conditional_edges("run_tools", after_tools, ["gather", "parse"])
    builder.add_conditional_edges("parse", after_parse, ["validate", "emit"])
    builder.add_conditional_edges("emit", after_emit, ["validate", "emit", "finish"])
    builder.add_conditional_edges("validate", after_validate, ["repair", "finish"])
    builder.add_conditional_edges("repair", after_repair, ["validate"])
    builder.add_edge("finish", END)

    return builder.compile()


GRAPH = _build_graph()


async def stream_plan(
    request: str,
    *,
    user_id: str = "",
    currency: str = "",
    previous: Itinerary | None = None,
    constraints: TripConstraints | None = None,
    previous_constraints: TripConstraints | None = None,
    client: AsyncOpenAI | None = None,
    model: str | None = None,
    fast_model: str | None = None,
    today: date | None = None,
    max_tool_rounds: int | None = None,
    memory: PreferenceStore | None = None,
) -> AsyncIterator[PlanEvent]:
    """Plan a trip, emitting progress events as the work happens.

    Pass `previous` to **revise** that itinerary instead of writing a new one. A revision
    takes the same path as a fresh plan -- tools, validate, repair, re-validate -- so an
    edit that breaks the budget or leaves ten minutes to cross the city is caught by the
    same code that would have caught it the first time. The caller supplies the itinerary
    rather than the service remembering it, which keeps this stateless.

    Terminates with exactly one `result` event. Failures raise PlanningError subclasses
    rather than yielding an error event, so the HTTP layer keeps deciding status codes.
    """
    if starts_new_trip(request):
        previous = None
        previous_constraints = None
    constraints = resolve_constraints(
        request, previous=previous_constraints, confirmed=constraints, currency=currency
    )
    llm = client or build_client()
    model = model or settings.openai_model
    if not model:
        # Only reachable when OPENAI_MODEL was explicitly blanked. Sending "" to the API
        # gets a provider-specific message about an unknown model; this one names the fix.
        raise PlanningConfigError(
            "no model was chosen and OPENAI_MODEL is not set; pass model= or put one in "
            "backend/.env"
        )
    fast_model = settings.fast_model if fast_model is None else fast_model
    today = today or date.today()
    rounds = max_tool_rounds if max_tool_rounds is not None else settings.max_tool_rounds

    # The schema goes in the system prompt, not just the emit-stage fallback. Without it
    # the model guesses field names on its first attempt, so the fast path always failed
    # and every request paid for a second full generation.
    schema = itinerary_schema_json()

    # Recall costs no LLM call: known preferences go straight into the system prompt.
    # Writing them back is the agent's job, through the remember_preference tool.
    system_prompt = SYSTEM_PROMPT.format(
        today=today.isoformat(),
        weekday=today.strftime("%A"),
        schema=schema,
        # Taken from the validator rather than written into the prompt as a literal, so
        # the number the model is asked for and the number it is judged against cannot
        # drift apart.
        min_transfer=MIN_TRANSFER_MINUTES,
    )
    system_prompt += "\n\n" + CONTEXT_POLICY.format(
        tool_calls=MAX_TOOL_CALLS,
        venues=brief.MAX_VENUES,
    )
    # Estimate in the traveller's currency rather than converting afterwards: conversion
    # needs an FX source, and an hours-old rate makes an estimate look precise. Empty
    # means the model picks.
    if currency:
        system_prompt += "\n\n" + CURRENCY_RULE.format(currency=currency)
    system_prompt += (
        "\n\nRequest-owned hard constraints (do not change these in the output): "
        + constraints.model_dump_json(exclude_none=True)
    )
    known = await (memory or memory_store).recall(user_id) if user_id else []
    if known:
        system_prompt += "\n\n" + recall_block(known)

    # The rule is a standing instruction, so it goes in the system prompt; the plan is
    # this turn's data, so it goes in the user turn. Sending the plan rather than
    # replaying the original transcript keeps out tool calls and superseded drafts.
    revision_scope = None
    if previous is not None:
        revision_scope = resolve_revision_scope(request, len(previous.days))
        system_prompt += "\n\n" + REVISION_RULE
        if revision_scope.locked_days:
            locked = ", ".join(str(index + 1) for index in sorted(revision_scope.locked_days))
            system_prompt += (
                "\n\nServer-enforced revision scope: day(s) "
                f"{locked} are locked. Their activities are omitted from the compact input "
                "and restored server-side after every generation or repair. Keep a day at "
                "each original list position."
            )
        request = REVISION_REQUEST.format(
            itinerary=revision_payload(previous, revision_scope),
            request=request,
        )

    state: PlanState = {
        "constraints": constraints,
        "llm": llm,
        "model": model,
        "fast_model": fast_model,
        "schema": schema,
        "user_id": user_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": request},
        ],
        "records": [],
        "dropped_tool_names": [],
        "warnings": [],
        "usage": Usage(),
        "rounds_total": rounds,
        "rounds_left": rounds,
        "calls_made": 0,
        "pending": [],
        "last_turn": None,
        "tool_cache": {},
        "place_hours": {},
        "place_prices": {},
        "place_points": {},
        "place_addresses": {},
        "fact_collected_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "brief_covered": 0,
        "itinerary": None,
        "raw": "",
        "parse_errors": None,
        "emit_attempts": 0,
        "truncated": False,
        "report": None,
        "repairs_left": MAX_CONSTRAINT_REPAIRS,
        "previous": previous,
        "revision_scope": revision_scope,
    }

    # Emitted here rather than from a node, so it reaches the client the moment the
    # request arrives, before the graph does any work.
    yield PlanEvent(type="stage", name="understanding", message="Understanding your request")

    # `stream_mode="custom"` yields exactly what the nodes write, keeping this layer a
    # pass-through. `recursion_limit` bounds the cycles a routing bug could spin in.
    async for event in GRAPH.astream(
        state,
        stream_mode="custom",
        config={"recursion_limit": 2 * (rounds + MAX_EMIT_ATTEMPTS + MAX_CONSTRAINT_REPAIRS) + 12},
    ):
        yield event


async def plan_trip(
    request: str,
    *,
    user_id: str = "",
    currency: str = "",
    previous: Itinerary | None = None,
    constraints: TripConstraints | None = None,
    previous_constraints: TripConstraints | None = None,
    client: AsyncOpenAI | None = None,
    model: str | None = None,
    fast_model: str | None = None,
    today: date | None = None,
    max_tool_rounds: int | None = None,
    memory: PreferenceStore | None = None,
) -> PlanResult:
    """Turn a natural-language trip request into a validated itinerary.

    Drains `stream_plan`; the streaming and non-streaming endpoints therefore share one
    implementation and cannot drift apart.
    """
    async for event in stream_plan(
        request,
        user_id=user_id,
        currency=currency,
        previous=previous,
        constraints=constraints,
        previous_constraints=previous_constraints,
        client=client,
        model=model,
        fast_model=fast_model,
        today=today,
        max_tool_rounds=max_tool_rounds,
        memory=memory,
    ):
        if event.type == "result" and event.result is not None:
            return event.result

    # stream_plan always ends with a result event; this only fires if that invariant
    # is ever broken, and failing loudly beats returning a silently empty plan.
    raise PlanningError("the planner produced no result")
