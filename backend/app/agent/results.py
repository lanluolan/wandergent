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
    #: `miss` executed the tool; the two hit kinds replayed a prior successful result.
    cache_status: Literal["miss", "run", "shared"] = "miss"
    cache_age_seconds: float | None = None
    duration_ms: int = 0
    attempts: int = 1
    #: Deterministic evidence match against the shipped itinerary. False means no use
    #: could be proved, not that the model definitely ignored the result.
    contributed: bool = False
    collected_at: str | None = None
    fact_payload: dict = Field(default_factory=dict, exclude=True)
    #: Candidate fact identifiers used only to derive `contributed`; never serialized.
    evidence: list[str] = Field(default_factory=list, exclude=True)
    #: The detailed reason, for the log line at the point of failure. `exclude` keeps it
    #: out of every serialisation, so it cannot reach the client by being forgotten.
    error: str | None = Field(default=None, exclude=True)


class ToolUsage(BaseModel):
    """Comparable tool-efficiency counters for one run or eval case."""

    requested_calls: int = 0
    executed_calls: int = 0
    cache_hits: int = 0
    run_cache_hits: int = 0
    shared_cache_hits: int = 0
    failed_calls: int = 0
    retried_calls: int = 0
    contributed_calls: int = 0
    dropped_calls: int = 0
    duration_ms: int = 0
    calls_by_tool: dict[str, int] = {}

    @computed_field
    @property
    def cache_hit_rate(self) -> float:
        handled = self.executed_calls + self.cache_hits
        return self.cache_hits / handled if handled else 0.0

    @classmethod
    def from_records(
        cls, records: list[ToolCallRecord], *, dropped_tools: list[str] | None = None
    ) -> "ToolUsage":
        dropped_tools = dropped_tools or []
        by_tool: dict[str, int] = {}
        for record in records:
            by_tool[record.name] = by_tool.get(record.name, 0) + 1
        for name in dropped_tools:
            by_tool[name] = by_tool.get(name, 0) + 1
        return cls(
            requested_calls=len(records) + len(dropped_tools),
            executed_calls=sum(record.cache_status == "miss" for record in records),
            cache_hits=sum(record.cache_status != "miss" for record in records),
            run_cache_hits=sum(record.cache_status == "run" for record in records),
            shared_cache_hits=sum(record.cache_status == "shared" for record in records),
            failed_calls=sum(not record.ok for record in records),
            retried_calls=sum(max(0, record.attempts - 1) for record in records),
            contributed_calls=sum(record.contributed for record in records),
            dropped_calls=len(dropped_tools),
            duration_ms=sum(record.duration_ms for record in records),
            calls_by_tool=by_tool,
        )

    def plus(self, other: "ToolUsage") -> "ToolUsage":
        by_tool = dict(self.calls_by_tool)
        for name, count in other.calls_by_tool.items():
            by_tool[name] = by_tool.get(name, 0) + count
        return ToolUsage(
            requested_calls=self.requested_calls + other.requested_calls,
            executed_calls=self.executed_calls + other.executed_calls,
            cache_hits=self.cache_hits + other.cache_hits,
            run_cache_hits=self.run_cache_hits + other.run_cache_hits,
            shared_cache_hits=self.shared_cache_hits + other.shared_cache_hits,
            failed_calls=self.failed_calls + other.failed_calls,
            retried_calls=self.retried_calls + other.retried_calls,
            contributed_calls=self.contributed_calls + other.contributed_calls,
            dropped_calls=self.dropped_calls + other.dropped_calls,
            duration_ms=self.duration_ms + other.duration_ms,
            calls_by_tool=by_tool,
        )


class PlanResult(BaseModel):
    """Outcome of one planning run."""

    run_id: str = Field(default_factory=lambda: uuid4().hex)
    trace_id: str | None = None
    feedback_available: bool = False

    constraints: TripConstraints = TripConstraints()
    itinerary: Itinerary | None = None
    tool_calls: list[ToolCallRecord] = []
    tool_usage: ToolUsage = ToolUsage()
    activity_evidence: list["ActivityEvidence"] = []

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


class ActivityEvidence(BaseModel):
    """Fact coverage, not a claim that the complete activity is verified."""

    day_index: int
    activity_index: int
    source: str | None = None
    collected_at: str | None = None
    venue_verified: bool = False
    hours_available: bool = False
    price_level_available: bool = False
    price_confidence: Literal["estimate"] = "estimate"
    recheck_before_departure: bool = True
    route_source: str | None = None
    route_collected_at: str | None = None
    route_departure: str | None = None
    route_mode: str | None = None
    route_seconds: int | None = None


PlanResult.model_rebuild()
