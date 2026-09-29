"""Outcome types for a planning run.

Split out from the orchestrator so that both the orchestrator and the streaming event
model can depend on them without a circular import.
"""

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field

from app.agent.constraints import TripConstraints
from app.agent.schemas import Itinerary
from app.agent.validation import ValidationReport

#: How a run fell short of what it set out to do. Every code describes the *run*, never
#: the plan -- what the plan gets wrong is `ValidationReport`'s job.
RunWarningCode = Literal[
    # The model asked for more tool calls in one round than the budget had left, so some
    # were never made. The plan is built on fewer answers than the model wanted.
    "tool_calls_dropped",
    # The call budget ran out exactly, with nothing dropped. Research ended early anyway.
    "tool_calls_spent",
    # The round budget ran out. Same consequence, different ceiling.
    "tool_rounds_spent",
    # No itinerary at all, after a repair round. `raw_reply` carries what came back.
    "no_itinerary",
]


class RunWarning(BaseModel):
    """Something that went less than perfectly while producing this plan.

    A code plus parameters rather than a sentence, for the same reason `Violation` carries
    a code: only the client knows who is reading and in what language. `detail` is the
    developer rendering, kept on the wire for logs, smoke scripts and as a fallback for a
    client that meets a code it has never heard of.
    """

    code: RunWarningCode
    #: English, aimed at whoever is debugging. Not traveller copy.
    detail: str
    #: The ceiling that was hit, on the two codes that have one, so a client need not
    #: hardcode a number this service is free to change.
    budget: int | None = None
    #: Set on `tool_calls_dropped` only: how many calls went unmade, and to which tools.
    dropped_calls: int | None = None
    dropped_tools: list[str] = []


def tool_calls_dropped(budget: int, tool_names: list[str], count: int) -> RunWarning:
    """The budget cut a round short. Never emitted silently; see the orchestrator."""
    names = ", ".join(sorted(set(tool_names)))
    return RunWarning(
        code="tool_calls_dropped",
        detail=f"reached the {budget}-call tool budget; skipped {count} further call(s) to {names}",
        budget=budget,
        dropped_calls=count,
        dropped_tools=sorted(set(tool_names)),
    )


def tool_calls_spent(budget: int) -> RunWarning:
    return RunWarning(
        code="tool_calls_spent",
        detail=f"stopped calling tools after {budget} calls",
        budget=budget,
    )


def tool_rounds_spent(budget: int) -> RunWarning:
    return RunWarning(
        code="tool_rounds_spent",
        detail=f"stopped calling tools after {budget} rounds",
        budget=budget,
    )


def no_itinerary() -> RunWarning:
    return RunWarning(
        code="no_itinerary",
        detail="the model did not return a valid itinerary, even after a repair round",
    )


class Usage(BaseModel):
    """What one planning run cost in model calls and tokens.

    Per run, not per call: what a *request* costs is the interesting number, and the tool
    loop makes the call count vary. `cached_prompt_tokens` comes from the endpoint's
    prompt-cache accounting -- the system prompt carries a ~2 KB JSON Schema every call,
    so how much of it is billed at the cached rate matters.
    """

    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_prompt_tokens: int = 0
    reasoning_tokens: int = 0

    #: Total tokens per model name. With routing on, only this shows the split a single
    #: total hides.
    tokens_by_model: dict[str, int] = {}

    @computed_field
    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def plus(self, other: "Usage") -> "Usage":
        """Accumulate. Returns a new value rather than mutating shared state."""
        merged = dict(self.tokens_by_model)
        for model, tokens in other.tokens_by_model.items():
            merged[model] = merged.get(model, 0) + tokens
        return Usage(
            llm_calls=self.llm_calls + other.llm_calls,
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cached_prompt_tokens=self.cached_prompt_tokens + other.cached_prompt_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            tokens_by_model=merged,
        )


class ToolCallRecord(BaseModel):
    """What the agent did, surfaced so the client can show tool progress.

    `code`, not the tool's `error`: that one is written for the model and quotes upstream
    verbatim, which once put a raw Open-Meteo URL on a traveller's itinerary. Codes are
    the stable part of this contract; the sentences shown for them are the client's.
    """

    name: str
    arguments: dict
    ok: bool
    code: str | None = None
    #: The detailed reason, for the log line at the point of failure. `exclude` keeps it
    #: out of every serialisation, so it cannot reach the client by being forgotten.
    error: str | None = Field(default=None, exclude=True)


class PlanResult(BaseModel):
    """Outcome of one planning run."""

    run_id: str = Field(default_factory=lambda: uuid4().hex)
    feedback_available: bool = False

    constraints: TripConstraints = TripConstraints()
    itinerary: Itinerary | None = None
    tool_calls: list[ToolCallRecord] = []

    #: How the run went, as codes the client renders. Deliberately disjoint from
    #: `validation`: this list says what the agent could not finish, that one says what
    #: the plan gets wrong. Overlap makes clients deduplicate by string-matching.
    warnings: list[RunWarning] = []
    raw_reply: str | None = None

    # Hard-constraint check on the itinerary as shipped. Empty violations means it
    # passed; a non-empty list means repair ran and did not fully succeed, so the client
    # can say so rather than imply the plan is sound.
    validation: ValidationReport | None = None

    #: What this run cost. Zero if the endpoint does not report usage.
    usage: Usage = Usage()
