"""Shared contract for agent tools.

Every tool follows the same rule: an upstream failure never propagates as an
exception. The tool returns a result object whose `ok` flag tells the orchestration
layer whether the payload is usable, so a dead weather API degrades the itinerary
instead of killing the request.
"""

import httpx
from pydantic import BaseModel, Field

#: Why a tool call did not deliver, as a closed vocabulary. `error` cannot serve both
#: readers: the model needs the detail to route around a failure, the traveller needs a
#: short phrase. Only the code crosses to the client, which owns the wording -- so codes
#: are stable API and the sentences behind them are not.
NOT_CONFIGURED = "not_configured"
TIMED_OUT = "timed_out"
RATE_LIMITED = "rate_limited"
UNAVAILABLE = "unavailable"
NO_MATCH = "no_match"
NO_COVERAGE = "no_coverage"
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
    #: Runtime bookkeeping. It is deliberately excluded from the tool reply: the model
    #: needs the fact or failure, while the audit trail needs to know whether a bounded
    #: retry happened.
    attempts: int = Field(default=1, exclude=True)


def http_failure_code(exc: httpx.HTTPError) -> str:
    """Classify an HTTP failure into a stable retry/fallback decision.

    A 429 is quota pressure, a caller-side 4xx is a bad request, and server/network
    failures are availability problems. Keeping this mapping beside the vocabulary
    prevents each HTTP-backed tool from inventing a subtly different policy.
    """
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status == 429:
        return RATE_LIMITED
    if isinstance(status, int) and 400 <= status < 500:
        return BAD_REQUEST
    return UNAVAILABLE
