"""Tool registry: the one place the orchestration loop resolves a tool name.

Schemas and callables are registered as pairs and the dispatch name is read out of
the schema, so a rename cannot leave the two halves pointing at different things.

**Request context is injected, never taken from the model.** A tool that needs to know
*who* is asking (memory, and later anything user-scoped) receives a `context` keyword
from the caller. Putting the user id in the tool schema instead would let a model name
whose memory it writes to.
"""

import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.tools.base import ToolOutcome
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


def _accepts_context(fn: ToolFn) -> bool:
    """Whether this tool wants the request context.

    Checked per call rather than cached at import: tests swap entries in
    `TOOL_FUNCTIONS`, and a cache would describe the tool that used to be there.
    """
    try:
        parameters = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    if "context" in parameters:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())


async def call_tool(
    name: str,
    arguments: dict[str, Any],
    context: dict | None = None,
) -> ToolOutcome:
    """Dispatch a tool call from the model.

    Always returns an outcome, never raises: the model picked both the name and the
    arguments, so a typo on its side must come back as feedback it can correct, not
    as a 500 for the user.
    """
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        known = ", ".join(sorted(TOOL_FUNCTIONS)) or "none"
        return ToolOutcome(ok=False, error=f"unknown tool {name!r}; available tools: {known}")

    call_kwargs = dict(arguments)
    if _accepts_context(fn):
        call_kwargs["context"] = context or {}

    try:
        return await fn(**call_kwargs)
    except TypeError as exc:
        return ToolOutcome(ok=False, error=f"bad arguments for {name}: {exc}")
    except Exception as exc:  # noqa: BLE001 - deliberate boundary, see docstring
        # Tools promise not to raise, but this is the seam between model-chosen input
        # and our code; one misbehaving tool must not take the whole request down.
        logger.exception("tool %s raised unexpectedly", name)
        return ToolOutcome(ok=False, error=f"{name} failed unexpectedly: {exc}")
