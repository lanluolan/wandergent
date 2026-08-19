"""Planning orchestration as a LangGraph state graph.

Phase 1 ran this as a hand-written loop, which was the right call while the shape was
still unknown. It stopped being the right call once Phase 3 added constraint repair:
the run now has **two cycles** -- gather/tools, and validate/repair -- and expressing
those as nested loops with break conditions buries the control flow in the middle of
the code that also does the work.

    START -> gather -> (tool calls?) -> run_tools -> gather
                    -> parse -> (parsed?) -> validate
                             -> emit -> (parsed?) -> validate
                                     -> emit (one retry)
             validate -> (violations and repairs left?) -> repair -> validate
                      -> finish

LangGraph only, not the LangChain stack: it brings `langchain-core` for base types and
nothing else. The prompts stay here, in plain sight, which was the whole reason for
avoiding the higher-level abstractions in the first place.

**Events are unchanged.** Nodes emit through LangGraph's custom stream writer and
`stream_plan` forwards them, so the SSE contract and the Android client see exactly
the same sequence as before the refactor. Every pre-existing test passes untouched.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import date
from typing import Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from openai import AsyncOpenAI

from app.agent import brief
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
    Usage,
    no_itinerary,
    tool_calls_dropped,
    tool_calls_spent,
    tool_rounds_spent,
)
from app.agent.schemas import Itinerary, itinerary_schema_json
from app.agent.transfers import confirm_transfers
from app.agent.validation import (
    MIN_TRANSFER_MINUTES,
    ValidationReport,
    validate_itinerary,
)
from app.config import settings
from app.memory import store as memory_store
from app.memory.store import PreferenceStore
from app.tools.base import ToolOutcome
from app.tools.memory import recall_block
from app.tools.registry import TOOL_SCHEMAS, call_tool

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

# Rounds are not the whole story: one round can carry any number of tool calls, so a
# four-round cap bounds the *turns* and nothing else. A confused model asking for
# fourteen searches at once stays inside the round budget while spending the latency and
# the money of a run three times the size. This is the other half of the cap.
#
# Sized off what a genuinely thorough run looks like: weather plus a handful of place
# searches plus a few route lookups, per day, is comfortably under this. Hitting it means
# something has gone wrong, not that the trip was complicated.
MAX_TOOL_CALLS = 16

# What to tell the model when the round budget is about to run out. Without it the tool
# loop simply stops answering and the model is asked for an itinerary mid-research --
# observed live, twice, ending in a run that returned no plan at all. Saying so plainly
# turns "it stopped talking to me" into a normal instruction it can follow.
LAST_ROUND_NOTICE = (
    "This is your last round of tool calls. Ask for anything still genuinely missing "
    "now, then write the complete itinerary from what you have. Do not wait for more "
    "data: where something is unverified, choose a sensible option and say so in the "
    "notes rather than leaving the plan unfinished."
)

# Same reasoning for constraint violations: one chance to fix them, then ship the plan
# with the remaining problems surfaced in the validation report rather than hidden.
MAX_CONSTRAINT_REPAIRS = 1

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
was already fine -- do not rewrite the whole trip."""


class PlanState(TypedDict, total=False):
    """Everything one planning run carries between nodes.

    The LLM client and model live in here rather than in a context schema because
    there is no checkpointer: nothing is serialised, so a live client is safe to hold,
    and keeping it in state means a node reads all its inputs from one place.
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
    tool_cache: dict[str, str]
    #: Venue name -> Google's opening-hours lines, harvested from `search_places`.
    #: The run already paid for this data; keeping it lets the constraint layer check
    #: opening times against Google rather than against the model's account of them.
    place_hours: dict[str, list[str]]
    place_prices: dict[str, str]
    #: Venue name -> (latitude, longitude), harvested from `search_places`. Rendered
    #: into a distance block so the schedule is written with spatial facts in hand.
    place_points: dict[str, tuple[float, float]]
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


def _tool_key(call) -> str:
    """Identity of a tool call: name plus arguments, order-independent."""
    return f"{call.name}:{json.dumps(safe_arguments(call.arguments), sort_keys=True)}"


def _places_in(payload: str) -> list[dict]:
    """The venue dicts inside a serialised `search_places` reply, or nothing.

    Reads the serialised reply rather than the tool's return value because that reply is
    what the loop already has in hand, and it is the same string the model sees.

    Never raises: a payload it cannot read simply contributes nothing, and a plan must
    not fail because a bookkeeping step was surprised.
    """
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
    """Keep the opening hours a `search_places` result carried.

    The run pays for this data and then throws it away; retaining it is what lets the
    constraint layer check opening times against *Google* rather than against the
    model's account of them.
    """
    if tool != "search_places":
        return
    for place in _places_in(payload):
        name, descriptions = place.get("name"), place.get("opening_hours")
        if name and descriptions:
            into[name] = list(descriptions)


def harvest_place_points(tool: str, payload: str, into: dict[str, tuple[float, float]]) -> None:
    """Keep the coordinates a `search_places` result carried.

    The cheapest of the three harvests and the one with the largest effect: coordinates
    turn into distances by arithmetic, so the model can be told how far apart its
    candidates are without a single extra API call. See `app/agent/proximity.py`.
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

    Same bargain as the hours: the data is already bought and paid for. It is far too
    coarse to price an activity from -- "MODERATE" is not a number -- but it is enough to
    catch the one contradiction that matters, a venue Google prices at all being budgeted
    at nothing. See `validation._check_price_levels`.
    """
    if tool != "search_places":
        return
    for place in _places_in(payload):
        name, level = place.get("name"), place.get("price_level")
        if name and isinstance(level, str) and level:
            into[name] = level


async def _execute_tool_call(call, context: dict) -> tuple[ToolCallRecord, dict]:
    """Run one tool call and render both the audit record and the reply message."""
    try:
        arguments = json.loads(call.arguments or "{}")
    except json.JSONDecodeError as exc:
        arguments = {}
        outcome: ToolOutcome = ToolOutcome(ok=False, error=f"arguments were not valid JSON: {exc}")
    else:
        if not isinstance(arguments, dict):
            outcome = ToolOutcome(ok=False, error="arguments must be a JSON object")
            arguments = {}
        else:
            outcome = await call_tool(call.name, arguments, context=context)

    record = ToolCallRecord(name=call.name, arguments=arguments, ok=outcome.ok, error=outcome.error)
    reply = {"role": "tool", "tool_call_id": call.id, "content": outcome.model_dump_json()}
    return record, reply


# --- nodes -------------------------------------------------------------------------


async def gather(state: PlanState) -> dict:
    """One tool-calling turn: let the model either ask for tools or answer.

    **Model routing lives here.** The first turn of a run only reads the request and
    picks tool arguments, which is a much smaller job than composing an itinerary --
    so when a cheaper model is configured, that turn runs on it.

    What makes the swap safe is `tool_choice="required"`: the cheap model is *only
    able* to emit a tool call, so it cannot produce a lower-quality plan. The strong
    model does every turn that writes or repairs the itinerary. Forcing a tool call
    costs nothing in practice -- the system prompt already tells the model to check
    the weather before committing to outdoor time.
    """
    writer = get_stream_writer()
    first_turn = state["rounds_left"] == state["rounds_total"]
    routed = first_turn and bool(state.get("fast_model"))
    model = state["fast_model"] if routed else state["model"]

    # Sent with this turn but deliberately not written back into `messages` below: it is
    # a one-shot nudge about the budget, not a fact about the trip, and leaving it out of
    # the history keeps the stored conversation the same shape whether or not the loop
    # ran long. Never on the opening turn -- telling the model to wrap up before it has
    # asked anything would defeat the tool loop entirely.
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

    # Trim to the call budget *before* the turn is written into the history. Every tool
    # call an assistant message declares must come back with a matching tool reply, so a
    # message promising twenty calls while only sixteen are executed is a malformed
    # conversation, not a smaller one -- and the endpoint rejects the next request.
    allowed = max(0, MAX_TOOL_CALLS - state["calls_made"])
    dropped = turn.tool_calls[allowed:]
    turn.tool_calls = turn.tool_calls[:allowed]

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

    if not turn.tool_calls:
        return {**update, "pending": [], "last_turn": turn}

    for call in turn.tool_calls:
        writer(
            PlanEvent(type="tool_call", name=call.name, arguments=safe_arguments(call.arguments))
        )
    update["calls_made"] = state["calls_made"] + len(turn.tool_calls)
    return {**update, "pending": turn.tool_calls}


async def run_tools(state: PlanState) -> dict:
    """Execute the pending tool calls concurrently and feed the results back.

    Identical calls are answered from a per-run cache instead of being made again.
    Observed live: the model asked for the same forecast three times in one run and
    wrote near-duplicate preferences each round. Replaying the result -- and saying
    plainly that it is a repeat -- costs nothing and stops the tool from running
    three times for one answer.
    """
    writer = get_stream_writer()
    context = {"user_id": state.get("user_id") or ""}
    cache = dict(state.get("tool_cache") or {})

    fresh = [call for call in state["pending"] if _tool_key(call) not in cache]
    executed = dict(
        zip(
            (_tool_key(call) for call in fresh),
            await asyncio.gather(*(_execute_tool_call(call, context) for call in fresh)),
            strict=True,
        )
    )

    messages = list(state["messages"])
    records = list(state["records"])
    hours = dict(state.get("place_hours") or {})
    prices = dict(state.get("place_prices") or {})
    points = dict(state.get("place_points") or {})

    for call in state["pending"]:
        key = _tool_key(call)
        if key in executed:
            record, reply = executed[key]
            cache[key] = reply["content"]
        else:
            record = ToolCallRecord(
                name=call.name, arguments=safe_arguments(call.arguments), ok=True
            )
            reply = {
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(
                    {
                        "repeat": True,
                        "note": (
                            "You already called this with the same arguments in this run. "
                            "The result is unchanged -- use it and move on."
                        ),
                        "result": cache[key],
                    },
                    ensure_ascii=False,
                ),
            }
            logger.info("tool %s repeated with identical arguments; replayed", call.name)

        records.append(record)
        messages.append(reply)
        harvest_place_hours(record.name, reply["content"], hours)
        harvest_place_prices(record.name, reply["content"], prices)
        harvest_place_points(record.name, reply["content"], points)
        if not record.ok:
            logger.info("tool %s degraded: %s", record.name, record.error)
        writer(PlanEvent(type="tool_result", name=record.name, ok=record.ok, error=record.error))

    # Hand the model a digest of everything verified so far -- hours, price bands and
    # distances -- *before* it picks venues and times, rather than leaving it to re-read
    # raw tool JSON while composing. Sent only when the set of known venues has actually
    # grown, so a run that searches twice does not carry two near-identical briefs for
    # the rest of the conversation.
    known = len({*points, *hours, *prices})
    announced = state["brief_covered"]
    block = brief.render(points, hours, prices) if known > announced else None
    if block is not None:
        messages.append({"role": "system", "content": block})
        announced = known

    writer(PlanEvent(type="stage", name="composing", message="Building the itinerary"))

    warnings = list(state["warnings"])
    if state["rounds_left"] <= 0:
        warnings.append(tool_rounds_spent(state["rounds_total"]))
    elif state["calls_made"] >= MAX_TOOL_CALLS:
        # Spent the budget exactly, with nothing dropped: the loop still ends here, and
        # ending a research loop early is a fact about the plan, not an implementation
        # detail to keep quiet about.
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
        "brief_covered": announced,
    }


async def parse(state: PlanState) -> dict:
    """Fast path: the turn that ended the tool loop is usually the itinerary already."""
    turn = state.get("last_turn") or Turn()
    itinerary, errors = parse_turn(turn)
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
        # The tool-stage reply was not malformed, it was too long. Asking for the same
        # itinerary again without saying so buys an identical reply, cut in the same
        # place -- and the emit budget is two attempts, not many.
        if state.get("truncated"):
            instruction += "\n\n" + TRUNCATION_ERROR
        messages.append({"role": "user", "content": instruction})
    else:
        writer(PlanEvent(type="stage", name="repairing", message="Fixing the format"))
        messages.append({"role": "assistant", "content": state["raw"]})
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

    itinerary, errors = parse_turn(turn)
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
        state["itinerary"], state.get("place_hours"), state.get("place_prices")
    )
    # The heuristic proposes, measurement disposes: only the pairs it already flagged
    # get a real travel time, so this costs a few Routes calls rather than one per
    # activity pair. Without a maps key it is a no-op and the heuristic stands.
    report = await confirm_transfers(report)
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

    if itinerary is None:
        warnings.append(no_itinerary())
        writer(
            PlanEvent(
                type="result",
                result=PlanResult(
                    itinerary=None,
                    tool_calls=state["records"],
                    warnings=warnings,
                    raw_reply=state.get("raw"),
                    usage=state["usage"],
                ),
            )
        )
        return {"warnings": warnings}

    # The validation report is *not* folded in here. It travels whole, in `validation`,
    # where every finding keeps its code, its day and its measured minutes. Copying the
    # messages across as well made the same finding arrive twice in two renderings, and
    # the only consumer that displayed both had to suppress one by comparing sentences.
    # Advisory remarks ride along in that report too, so nothing stops being said.

    writer(
        PlanEvent(
            type="result",
            result=PlanResult(
                itinerary=itinerary,
                tool_calls=state["records"],
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
    client: AsyncOpenAI | None = None,
    model: str | None = None,
    fast_model: str | None = None,
    today: date | None = None,
    max_tool_rounds: int | None = None,
    memory: PreferenceStore | None = None,
) -> AsyncIterator[PlanEvent]:
    """Plan a trip, emitting progress events as the work happens.

    Pass `previous` to **revise** that itinerary instead of writing a new one. The
    revision takes the same path as a fresh plan -- tools, then validate, then repair,
    then re-validate -- which is the whole point: an edit that quietly breaks the budget
    or leaves ten minutes to cross the city gets caught by the same code that would have
    caught it the first time. Revising is the one thing a chat transcript cannot give
    you for free.

    The caller supplies the itinerary rather than the service remembering it: this stays
    stateless, and the client already holds the plan it is asking to change.

    Terminates with exactly one `result` event. Failures raise PlanningError subclasses
    rather than yielding an error event, so the HTTP layer keeps deciding status codes;
    the SSE endpoint converts them for a stream that has already started.
    """
    llm = client or build_client()
    model = model or settings.openai_model
    fast_model = settings.fast_model if fast_model is None else fast_model
    today = today or date.today()
    rounds = max_tool_rounds if max_tool_rounds is not None else settings.max_tool_rounds

    # The schema goes in the system prompt, not just in the emit-stage fallback.
    # Without it the model has to guess the field names on its first attempt, so the
    # fast path essentially always failed and every request paid for a second full
    # generation -- visible as a "composing" stage restart in the event stream.
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
    # Estimate in the traveller's currency rather than converting afterwards. A
    # conversion needs an FX source, and a rate that is hours old turns an estimate
    # into a number that looks precise and is not. Empty means the model picks, which
    # is the old behaviour and what any non-app caller gets.
    if currency:
        system_prompt += "\n\n" + CURRENCY_RULE.format(currency=currency)
    known = await (memory or memory_store).recall(user_id) if user_id else []
    if known:
        system_prompt += "\n\n" + recall_block([preference.text for preference in known])

    # The rule goes in the system prompt and the plan itself in the user turn: the
    # discipline is standing instruction, the itinerary is this turn's data. Sending
    # the plan rather than replaying the original run's transcript keeps the prompt to
    # what is actually being edited -- no tool calls, no superseded drafts.
    if previous is not None:
        system_prompt += "\n\n" + REVISION_RULE
        request = REVISION_REQUEST.format(
            itinerary=previous.model_dump_json(indent=None),
            request=request,
        )

    state: PlanState = {
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
        "brief_covered": 0,
        "itinerary": None,
        "raw": "",
        "parse_errors": None,
        "emit_attempts": 0,
        "truncated": False,
        "report": None,
        "repairs_left": MAX_CONSTRAINT_REPAIRS,
    }

    # Emitted here rather than from a node: it should reach the client the moment the
    # request arrives, before the graph does any work.
    yield PlanEvent(type="stage", name="understanding", message="Understanding your request")

    # `stream_mode="custom"` yields exactly what the nodes hand to their writer, so the
    # event sequence is the nodes' business and this layer stays a pass-through.
    # `recursion_limit` bounds the cycles: without it a routing bug would spin forever.
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
    client: AsyncOpenAI | None = None,
    model: str | None = None,
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
        client=client,
        model=model,
        today=today,
        max_tool_rounds=max_tool_rounds,
        memory=memory,
    ):
        if event.type == "result" and event.result is not None:
            return event.result

    # stream_plan always ends with a result event; this only fires if that invariant
    # is ever broken, and failing loudly beats returning a silently empty plan.
    raise PlanningError("the planner produced no result")
