"""Duck-typed stand-ins for the OpenAI client.

The orchestrator streams, so these model the wire shape that matters: a turn arrives as
many chunks, and a tool call is split across them -- id and name in the first, arguments
a few characters at a time, keyed by index. The builders here deliberately fragment
both, because reassembly is the part that is easy to get wrong and impossible to notice
with a fake that hands over whole messages.
"""

import json
from copy import deepcopy
from dataclasses import dataclass, field
from types import SimpleNamespace

CHUNK = 8


@dataclass
class FakeFunctionDelta:
    name: str | None = None
    arguments: str | None = None


@dataclass
class FakeToolCallDelta:
    index: int
    id: str | None = None
    function: FakeFunctionDelta | None = None


@dataclass
class FakeDelta:
    content: str | None = None
    tool_calls: list[FakeToolCallDelta] | None = None


@dataclass
class FakeChunkChoice:
    delta: FakeDelta
    #: Set on the last chunk of a turn, as the API does. "length" means cut off.
    finish_reason: str | None = None


@dataclass
class FakeChunk:
    choices: list[FakeChunkChoice] = field(default_factory=list)


@dataclass
class ToolCallSpec:
    name: str
    arguments: dict
    call_id: str = "call_1"


def tool_call(name: str, arguments: dict, call_id: str = "call_1") -> ToolCallSpec:
    """One scripted tool call, fragmented into chunks by `completion`."""
    return ToolCallSpec(name=name, arguments=arguments, call_id=call_id)


def completion(
    content: str | None = None,
    tool_calls: list[ToolCallSpec] | None = None,
    finish_reason: str = "stop",
) -> list[FakeChunk]:
    """One scripted assistant turn, expressed as the chunks it streams as.

    `finish_reason="length"` scripts a reply the endpoint cut off -- pair it with
    content that stops mid-JSON to reproduce a truncated itinerary.
    """
    chunks: list[FakeChunk] = []

    for index, spec in enumerate(tool_calls or []):
        chunks.append(
            FakeChunk(
                [
                    FakeChunkChoice(
                        FakeDelta(
                            tool_calls=[
                                FakeToolCallDelta(
                                    index=index,
                                    id=spec.call_id,
                                    function=FakeFunctionDelta(name=spec.name),
                                )
                            ]
                        )
                    )
                ]
            )
        )
        arguments = json.dumps(spec.arguments, ensure_ascii=False)
        for start in range(0, len(arguments), CHUNK):
            chunks.append(
                FakeChunk(
                    [
                        FakeChunkChoice(
                            FakeDelta(
                                tool_calls=[
                                    FakeToolCallDelta(
                                        index=index,
                                        function=FakeFunctionDelta(
                                            arguments=arguments[start : start + CHUNK]
                                        ),
                                    )
                                ]
                            )
                        )
                    ]
                )
            )

    if content is not None:
        for start in range(0, len(content), CHUNK):
            chunks.append(
                FakeChunk([FakeChunkChoice(FakeDelta(content=content[start : start + CHUNK]))])
            )

    # An empty turn still has to produce at least one chunk, the way the API would.
    if not chunks:
        chunks.append(FakeChunk([FakeChunkChoice(FakeDelta())]))
    # The API puts the reason on the final chunk, never on an earlier one.
    chunks[-1].choices[0].finish_reason = finish_reason
    return chunks


class FakeStream:
    """Async-iterable stand-in for the SDK's streaming response."""

    def __init__(self, chunks: list[FakeChunk]) -> None:
        self._chunks = list(chunks)

    def __aiter__(self) -> "FakeStream":
        return self

    async def __anext__(self) -> FakeChunk:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


class FakeCompletions:
    """Replays a scripted list of turns and records every request it received."""

    def __init__(self, script: list) -> None:
        self._script = list(script)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        # Snapshot, not a reference: the orchestrator mutates one message list across
        # the whole run, so storing it directly would make every recorded call show
        # the final state.
        self.calls.append(deepcopy(kwargs))
        if not self._script:
            raise AssertionError("the orchestrator made more LLM calls than the test scripted")
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return FakeStream(step)


class FakeLLM:
    """Stands in for AsyncOpenAI, exposing only `chat.completions.create`."""

    def __init__(self, script: list) -> None:
        self.completions = FakeCompletions(script)
        self.chat = SimpleNamespace(completions=self.completions)

    @property
    def requests(self) -> list[dict]:
        """Every kwargs dict the orchestrator passed to the API, in order."""
        return self.completions.calls


ITINERARY_JSON = json.dumps(
    {
        "destination": "Chicago",
        "start_date": "2026-08-06",
        "end_date": "2026-08-07",
        "travelers": 2,
        "currency": "CNY",
        "budget": 3000,
        "days": [
            {
                "date": "2026-08-06",
                "summary": "Loop and lakefront",
                "weather": "Thunderstorms, mostly indoors",
                "activities": [
                    {
                        "start_time": "09:00",
                        "end_time": "11:30",
                        "title": "Art Institute of Chicago",
                        "category": "sightseeing",
                        "location": "111 S Michigan Ave, Chicago, IL",
                        "indoor": False,
                        "estimated_cost": 100.0,
                    },
                    {
                        "start_time": "12:00",
                        "end_time": "13:30",
                        "title": "Lou Malnati's Pizzeria",
                        "category": "food",
                        "indoor": True,
                        "estimated_cost": 80.0,
                        "highlights": ["Signature tonkotsu", "Ramen set"],
                    },
                    # The trip has a night in it, so it needs a bed -- see the
                    # missing_accommodation rule in app/agent/validation.py.
                    {
                        "start_time": "21:00",
                        "end_time": "22:00",
                        "title": "Check in at The Blackstone",
                        "category": "accommodation",
                        "location": "The Blackstone Hotel",
                        "indoor": True,
                        "estimated_cost": 0.0,
                    },
                ],
            },
            {
                "date": "2026-08-07",
                "summary": "Museum Campus day",
                "activities": [
                    {
                        "start_time": "08:30",
                        "end_time": "12:00",
                        "title": "Shedd Aquarium",
                        "category": "sightseeing",
                        "estimated_cost": 110.0,
                    }
                ],
            },
        ],
        "notes": ["Bring an umbrella"],
    },
    ensure_ascii=False,
)
