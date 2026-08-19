"""Shared contract for agent tools.

Every tool follows the same rule: an upstream failure never propagates as an
exception. The tool returns a result object whose `ok` flag tells the orchestration
layer whether the payload is usable, so a dead weather API degrades the itinerary
instead of killing the request.
"""

from pydantic import BaseModel

#: Why a tool call did not produce what was asked for, as a closed vocabulary.
#:
#: Exists because `error` cannot serve both readers. The model needs the detail -- which
#: service, what it said -- to route around a failure. The traveller needs a short phrase.
#: One string doing both put `weather service unavailable: Client error '400 Bad Request'
#: for url 'https://api.open-meteo.com/v1/forecast?latitude=34.05223&...'` on screen, with
#: the query string and a link to the MDN page for HTTP 400.
#:
#: Only the code crosses to the client, which owns the wording -- the same split the run
#: warnings already use. Codes are stable API; the sentences behind them are not.
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
