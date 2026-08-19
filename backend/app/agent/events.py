"""Events emitted while a plan is being produced.

One flat model rather than a discriminated union of six: the payloads are small, and a
single wire type keeps the Android client's deserialisation to one data class instead
of a sealed hierarchy plus polymorphic configuration. `type` says which fields are
meaningful.

These are the contract for `POST /plan/stream`, so renaming a field here is a breaking
API change -- see docs/api.md.
"""

from typing import Literal

from pydantic import BaseModel

from app.agent.results import PlanResult
from app.agent.validation import Violation

EventType = Literal[
    # Coarse phase of the run: understanding, gathering, composing.
    "stage",
    # The model asked for a tool, with the arguments it chose.
    "tool_call",
    # That tool came back, possibly degraded.
    "tool_result",
    # A recognisable fragment of the itinerary appeared in the partial JSON.
    "composing",
    # The hard-constraint check ran. Empty violations means the plan passed.
    "validation",
    # Terminal success: carries the same PlanResult the non-streaming endpoint returns.
    "result",
    # Terminal failure.
    "error",
]


class PlanEvent(BaseModel):
    type: EventType

    # stage / composing / error
    message: str | None = None

    # tool_call / tool_result
    name: str | None = None
    arguments: dict | None = None
    ok: bool | None = None
    error: str | None = None

    # validation
    violations: list[Violation] | None = None

    # result
    result: PlanResult | None = None
