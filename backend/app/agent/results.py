"""Outcome types for a planning run.

Split out from the orchestrator so that both the orchestrator and the streaming event
model can depend on them without a circular import.
"""

from typing import Literal

from pydantic import BaseModel, computed_field

from app.agent.schemas import Itinerary
from app.agent.validation import ValidationReport

#: How a run fell short of doing everything it set out to do. Every code describes the
#: *run*, never the plan -- what the plan gets wrong is `ValidationReport`'s job, and the
#: two were previously mixed into one list of strings that the client had to separate by
#: comparing sentences.
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

    A code and its parameters rather than a sentence, for the same reason `Violation`
    carries a code: the reader is a traveller, and `reached the 16-call tool budget;
    skipped 3 further call(s) to search_places` is a sentence written for whoever wrote
    the tool loop. Only the client knows who is reading and in what language, so only the
    client can write that sentence.

    `detail` is the developer rendering, kept on the wire on purpose: logs, the smoke
    scripts and a failing eval all want the specifics, and a client that meets a code it
    has never heard of needs something to fall back on rather than a blank line.
    """

    code: RunWarningCode
    #: English, aimed at whoever is debugging. Not traveller copy -- see the class docs.
    detail: str
    #: The ceiling that was hit, on the two codes that have one. Named so a client can
    #: say "16" without hardcoding a number this service is free to change.
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

    Reported per run rather than per call: the interesting number is what a *request*
    costs, and the tool loop makes the number of calls vary. `cached_prompt_tokens`
    comes from the endpoint's prompt-cache accounting and is the headline figure for
    the Phase 4 cost work -- the system prompt carries a ~2 KB JSON Schema on every
    call, so how much of it is billed at the cached rate matters.
    """

    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_prompt_tokens: int = 0
    reasoning_tokens: int = 0

    #: Total tokens per model name. With routing on, this is what shows the split --
    #: the same token count spread over a cheaper model is the whole point, and a
    #: single total cannot show it.
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
    """What the agent did, surfaced so the client can show tool progress."""

    name: str
    arguments: dict
    ok: bool
    error: str | None = None


class PlanResult(BaseModel):
    """Outcome of one planning run."""

    itinerary: Itinerary | None = None
    tool_calls: list[ToolCallRecord] = []

    #: How the run went, as codes the client renders. Deliberately disjoint from
    #: `validation`: this list says what the agent could not finish, that one says what
    #: the plan gets wrong. They used to overlap, and every consumer paid for it -- the
    #: Android client suppressed the duplicates by string-matching two renderings of the
    #: same finding, which is exactly the thing a code exists to make unnecessary.
    warnings: list[RunWarning] = []
    raw_reply: str | None = None

    # Result of the hard-constraint check on the itinerary as shipped. Empty violations
    # means it passed; a non-empty list means repair was attempted and did not fully
    # succeed, so the client can say so rather than implying the plan is sound.
    validation: ValidationReport | None = None

    #: What this run cost. Zero if the endpoint does not report usage.
    usage: Usage = Usage()
