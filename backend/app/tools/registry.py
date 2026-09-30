"""Tool registry: the one place the orchestration loop resolves a tool name.

Schemas and callables are registered as pairs and the dispatch name is read out of
the schema, so a rename cannot leave the two halves pointing at different things.

**Request context is injected, never taken from the model.** A tool that needs to know
*who* is asking receives a `context` keyword from the caller. Putting the user id in the
tool schema instead would let a model name whose memory it writes to.
"""

import asyncio
import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from weakref import WeakKeyDictionary

from app.tools.base import (
    BAD_REQUEST,
    UNAVAILABLE,
    UNKNOWN_TOOL,
    ToolOutcome,
)
from app.tools.maps import (
    PLACES_TOOL_SCHEMA,
    TRAVEL_TOOL_SCHEMA,
    get_travel_time,
    search_places,
)
from app.tools.memory import REMEMBER_TOOL_SCHEMA, remember_preference
from app.tools.weather import WEATHER_TOOL_SCHEMA, get_weather_forecast

logger = logging.getLogger(__name__)

ToolFn = Callable[..., Awaitable[ToolOutcome]]

_REGISTERED: tuple[tuple[dict, ToolFn], ...] = (
    (WEATHER_TOOL_SCHEMA, get_weather_forecast),
    (PLACES_TOOL_SCHEMA, search_places),
    (TRAVEL_TOOL_SCHEMA, get_travel_time),
    (REMEMBER_TOOL_SCHEMA, remember_preference),
)

TOOL_SCHEMAS: list[dict] = [schema for schema, _ in _REGISTERED]
TOOL_FUNCTIONS: dict[str, ToolFn] = {schema["function"]["name"]: fn for schema, fn in _REGISTERED}
_HTTP_TOOL_FUNCTIONS = {get_weather_forecast, search_places, get_travel_time}

# Tool-loop policy lives beside registration, so adding a tool requires an explicit
# decision about staleness, fan-out and retry rather than inheriting an unsafe default.
SHARED_CACHE_TTLS: dict[str, float] = {
    "get_weather_forecast": 15 * 60,
    "search_places": 6 * 60 * 60,
    "get_travel_time": 5 * 60,
    # remember_preference is a write and is deliberately absent.
}
TOOL_CONCURRENCY_LIMITS: dict[str, int] = {
    "get_weather_forecast": 2,
    "search_places": 4,
    "get_travel_time": 4,
    "remember_preference": 1,
}
MAX_PARALLEL_TOOLS = 6
RETRYABLE_CODES = {"timed_out", "unavailable"}
RETRYABLE_TOOLS = {"get_weather_forecast", "search_places", "get_travel_time"}
MAX_TOOL_ATTEMPTS = 2

_LIMITERS_BY_LOOP: WeakKeyDictionary = WeakKeyDictionary()


@asynccontextmanager
async def tool_capacity(name: str):
    """Share concurrency ceilings across every planning run in one worker loop."""
    loop = asyncio.get_running_loop()
    limiters = _LIMITERS_BY_LOOP.get(loop)
    if limiters is None:
        limiters = (
            asyncio.Semaphore(MAX_PARALLEL_TOOLS),
            {
                tool_name: asyncio.Semaphore(limit)
                for tool_name, limit in TOOL_CONCURRENCY_LIMITS.items()
            },
        )
        _LIMITERS_BY_LOOP[loop] = limiters
    total, by_tool = limiters
    per_tool = by_tool.setdefault(name, asyncio.Semaphore(1))
    async with total, per_tool:
        yield


def _clean_text(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    if isinstance(value, list):
        return [_clean_text(item) for item in value]
    if isinstance(value, dict):
        return {key: _clean_text(item) for key, item in value.items()}
    return value


def normalized_tool_arguments(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Canonical arguments for identity, not the values sent to the tool.

    Defaults and spelling differences that do not change an upstream request collapse
    to one key. Planning-only metadata such as a Places call's purpose is excluded.
    """
    normalized = dict(arguments)
    if name == "get_weather_forecast":
        normalized.setdefault("language", "en")
    elif name == "search_places":
        normalized.setdefault("limit", 4)
        normalized.setdefault("language", "en")
        if normalized.get("purpose") in (None, "required", "optional"):
            normalized.pop("purpose", None)
    elif name == "get_travel_time":
        normalized["mode"] = str(normalized.get("mode") or "WALK").upper()
    return _clean_text(normalized)


def tool_cache_key(name: str, arguments: dict[str, Any]) -> str:
    canonical = normalized_tool_arguments(name, arguments)
    return f"{name}:{json.dumps(canonical, sort_keys=True, ensure_ascii=False, default=str)}"


def shared_cache_ttl(name: str) -> float:
    return SHARED_CACHE_TTLS.get(name, 0)


def uses_shared_http_client(name: str) -> bool:
    """Whether the currently registered implementation is one of our HTTP readers."""
    return TOOL_FUNCTIONS.get(name) in _HTTP_TOOL_FUNCTIONS


def tool_priority(name: str, arguments: dict[str, Any], *, repeated: bool = False) -> int:
    """Value of a requested fact when a round asks for more than remains.

    Exact repeats rank last because the model already has their answer. Optional Places
    alternatives rank below weather, scheduled-venue facts and route feasibility.
    """
    if repeated:
        return 0
    if name == "get_weather_forecast":
        return 100
    if name == "remember_preference":
        return 90
    if name == "search_places":
        return 30 if arguments.get("purpose") == "optional" else 80
    if name == "get_travel_time":
        return 70
    return 10


def _accepts_keyword(fn: ToolFn, name: str) -> bool:
    """Whether this tool accepts an injected runtime keyword.

    Checked per call rather than cached at import: tests swap entries in
    `TOOL_FUNCTIONS`, and a cache would describe the tool that used to be there.
    """
    try:
        parameters = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    if name in parameters:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())


async def call_tool(
    name: str,
    arguments: dict[str, Any],
    context: dict | None = None,
) -> ToolOutcome:
    """Dispatch a tool call from the model.

    Always returns an outcome, never raises: the model picked both the name and the
    arguments, so a typo on its side comes back as feedback it can correct.
    """
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        known = ", ".join(sorted(TOOL_FUNCTIONS)) or "none"
        return ToolOutcome(
            ok=False, error=f"unknown tool {name!r}; available tools: {known}", code=UNKNOWN_TOOL
        )

    call_kwargs = dict(arguments)
    if _accepts_keyword(fn, "context"):
        call_kwargs["context"] = context or {}
    http_client = (context or {}).get("http_client")
    if name in SHARED_CACHE_TTLS and http_client is not None and _accepts_keyword(fn, "client"):
        # One batch, one connection pool. The model still receives one result per call;
        # this only removes repeated TLS setup and lets independent Places requests run
        # concurrently over a shared client.
        call_kwargs["client"] = http_client

    outcome = ToolOutcome(ok=False, error=f"{name} was not attempted", code=UNAVAILABLE)
    for attempt in range(1, MAX_TOOL_ATTEMPTS + 1):
        try:
            outcome = await fn(**call_kwargs)
        except TypeError as exc:
            outcome = ToolOutcome(
                ok=False, error=f"bad arguments for {name}: {exc}", code=BAD_REQUEST
            )
        except Exception as exc:  # noqa: BLE001 - deliberate boundary, see docstring
            # Tools promise not to raise, but this is the seam between model-chosen input
            # and our code: one misbehaving tool must not take the request down.
            logger.exception("tool %s raised unexpectedly", name)
            outcome = ToolOutcome(
                ok=False, error=f"{name} failed unexpectedly: {exc}", code=UNAVAILABLE
            )
        outcome = outcome.model_copy(update={"attempts": attempt})
        if (
            outcome.ok
            or name not in RETRYABLE_TOOLS
            or outcome.code not in RETRYABLE_CODES
            or attempt >= MAX_TOOL_ATTEMPTS
        ):
            return outcome
        logger.info("retrying tool %s after %s (attempt %s)", name, outcome.code, attempt)
    return outcome
