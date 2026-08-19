"""Shared contract for agent tools.

Every tool follows the same rule: an upstream failure never propagates as an
exception. The tool returns a result object whose `ok` flag tells the orchestration
layer whether the payload is usable, so a dead weather API degrades the itinerary
instead of killing the request.
"""

from pydantic import BaseModel


class ToolOutcome(BaseModel):
    """Base result for every tool: success flag plus a reason when degraded."""

    ok: bool = True
    error: str | None = None
