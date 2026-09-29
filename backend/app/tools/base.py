"""Shared contract for agent tools.

Every tool follows the same rule: an upstream failure never propagates as an
exception. The tool returns a result object whose `ok` flag tells the orchestration
layer whether the payload is usable, so a dead weather API degrades the itinerary
instead of killing the request.
"""

from pydantic import BaseModel

#: Why a tool call did not deliver, as a closed vocabulary. `error` cannot serve both
#: readers: the model needs the detail to route around a failure, the traveller needs a
#: short phrase. Only the code crosses to the client, which owns the wording -- so codes
#: are stable API and the sentences behind them are not.
NOT_CONFIGURED = "not_configured"
TIMED_OUT = "timed_out"
UNAVAILABLE = "unavailable"
NO_MATCH = "no_match"
BAD_REQUEST = "bad_request"
UNKNOWN_TOOL = "unknown_tool"


class ToolOutcome(BaseModel):
    """Base result for every tool: success flag plus a reason when degraded.

    `error` is for the model and the logs and may quote upstream verbatim. `code` is what
    the client is told. Anything that embeds an exception, a URL or an upstream body must
    set a code, or that text becomes the client's only description of the failure.
    """

    ok: bool = True
    error: str | None = None
    code: str | None = None
