"""LLM plumbing: client, streaming turns, and parsing the model's output.

Split out so the orchestrator can be about the *shape* of a run. Nothing here knows what a
planning run looks like -- only how to stream one turn and read an itinerary out of text.
"""

import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx
from openai import APIError, APITimeoutError, AsyncOpenAI
from pydantic import ValidationError

from app.agent.events import PlanEvent
from app.agent.results import Usage
from app.agent.schemas import Itinerary
from app.config import settings
from app.observability import span

logger = logging.getLogger(__name__)

# Pulled out of the partial JSON to show what is being written right now. Deliberately
# tolerant: these run against a half-finished document, so they must never assume the
# buffer parses.
_TITLE_RE = re.compile(r'"title"\s*:\s*"([^"\\]{1,60})"')
_DATE_RE = re.compile(r'"date"\s*:\s*"(\d{4}-\d{2}-\d{2})"')

# What to tell the model when its reply was cut off -- not the JSON parser's "expecting
# ',' at column 4678", which buys a comma added to a document whose real problem is that
# it does not fit. Name the cause and the levers, or the repair round is wasted.
TRUNCATION_ERROR = (
    "your reply was cut off at the output limit, so the JSON stops mid-document. "
    "This is a length problem, not a punctuation one. Return the whole itinerary "
    "again, shorter: keep every day and every activity, but give at most two short "
    "highlights per activity, put the venue name in location without the full street "
    "address, and drop notes unless something genuinely needs saying."
)


class PlanningError(RuntimeError):
    """The plan could not be produced because of the upstream LLM or its config."""


class PlanningTimeout(PlanningError):
    """The upstream LLM did not answer in time."""


class PlanningConfigError(PlanningError):
    """The service itself is misconfigured, e.g. a missing API key."""


@dataclass
class StreamedToolCall:
    """A tool call reassembled from deltas.

    The API sends a call in fragments across chunks -- id and name usually in the first,
    arguments a few characters at a time -- keyed by index, so pieces accumulate per slot
    rather than being read off any single chunk.
    """

    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class Turn:
    """One assistant turn, accumulated while it streams."""

    content: str = ""
    tool_calls: list[StreamedToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)

    # Why the model stopped. "length" means the reply was cut off at the output limit and
    # arrives as half a JSON object, which without this reads as a syntax error and sends
    # the repair round after a comma.
    finish_reason: str | None = None

    @property
    def truncated(self) -> bool:
        return self.finish_reason == "length"


def build_client() -> AsyncOpenAI:
    """Create the LLM client. Timeout is mandatory, per the project's hard rules."""
    if not settings.openai_api_key:
        raise PlanningConfigError("OPENAI_API_KEY is not set; put your key in backend/.env")
    return AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        timeout=settings.llm_timeout_seconds,
    )


def strip_fences(raw: str) -> str:
    """Pull the JSON object out of a reply, whatever the model wrapped it in.

    Models add fences when told not to, and preface the answer with a sentence before
    them. Stripping fences only when the text *starts* with one lets a line of preamble
    defeat the fast path, costing a whole second generation.

    Positional -- first "{" to last "}" -- rather than a JSON scanner: that is the object
    in every shape seen in practice, and anything cleverer only guesses at input the
    parser is about to reject. No shortcut for text already starting with "{" either: a
    reply can be valid JSON followed by "let me know if you want changes".
    """
    text = raw.strip()

    if text.startswith("```"):
        if "\n" in text:
            text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0].strip()

    opening = text.find("{")
    closing = text.rfind("}")
    if opening == -1 or closing <= opening:
        # Nothing object-shaped in there. Hand back what the model said, so the error
        # the caller reports is about the actual reply.
        return text
    return text[opening : closing + 1].strip()


def parse_itinerary(raw: str | None) -> tuple[Itinerary | None, str | None]:
    """Validate a model reply into an itinerary, returning the errors instead of raising.

    The error text is fed straight back to the model as repair instructions, so it has
    to survive as a string rather than an exception.
    """
    if not raw or not raw.strip():
        return None, "the model returned an empty message"
    try:
        payload = json.loads(strip_fences(raw))
        if isinstance(payload, dict) and "result" in payload:
            payload = payload["result"]
        return Itinerary.model_validate(payload), None
    except (ValueError, ValidationError) as exc:
        return None, str(exc)


def parse_turn(turn: Turn) -> tuple[Itinerary | None, str | None]:
    """Read an itinerary out of a finished turn, blaming the right thing when it fails.

    Same as `parse_itinerary`, except a reply the endpoint cut off is reported as the
    truncation it is. The error goes straight back to the model, so which story it gets
    decides whether the repair round is spent shortening or shuffling punctuation.
    """
    itinerary, errors = parse_itinerary(turn.content)
    if itinerary is None and turn.truncated:
        logger.info("reply truncated at the output limit after %s chars", len(turn.content))
        return None, TRUNCATION_ERROR
    return itinerary, errors


def is_empty(turn: Turn) -> bool:
    """A turn that said nothing and asked for nothing.

    It cannot be sent back: an assistant message with neither content nor tool calls is
    rejected outright (`400 ... must provide content, reasoning_content or tool_calls`).
    It carries no information either, so dropping it loses nothing.
    """
    return not turn.content and not turn.tool_calls


def assistant_message(turn: Turn) -> dict:
    """Render a finished turn back into a wire message for the next request."""
    payload: dict = {"role": "assistant", "content": turn.content or None}
    if turn.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in turn.tool_calls
        ]
    return payload


def safe_arguments(raw: str) -> dict:
    """Best-effort decode of tool arguments for display; never raises."""
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def composing_hint(buffer: str) -> str | None:
    """Name whatever the model is writing right now, from half-finished JSON.

    Only the last complete quoted value counts -- the one being typed is still open and
    would show as a truncated fragment.
    """
    titles = _TITLE_RE.findall(buffer)
    if titles:
        return titles[-1]
    dates = _DATE_RE.findall(buffer)
    if dates:
        return dates[-1]
    return None


def _read_usage(raw, model: str) -> Usage:
    """Pull one call's usage off the SDK object, tolerating missing sub-objects."""
    prompt_details = getattr(raw, "prompt_tokens_details", None)
    completion_details = getattr(raw, "completion_tokens_details", None)
    prompt = getattr(raw, "prompt_tokens", 0) or 0
    completion = getattr(raw, "completion_tokens", 0) or 0
    return Usage(
        llm_calls=1,
        prompt_tokens=prompt,
        completion_tokens=completion,
        cached_prompt_tokens=getattr(prompt_details, "cached_tokens", 0) or 0,
        reasoning_tokens=getattr(completion_details, "reasoning_tokens", 0) or 0,
        tokens_by_model={model: prompt + completion},
    )


async def stream_turn(
    client: AsyncOpenAI,
    model: str,
    turn: Turn,
    **kwargs,
) -> AsyncIterator[PlanEvent]:
    with span(
        f"chat {model}",
        **{
            "openinference.span.kind": "LLM",
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": model,
        },
    ) as record:
        try:
            async for event in _stream_turn(client, model, turn, **kwargs):
                yield event
        finally:
            record.attributes.update(
                {
                    "gen_ai.usage.input_tokens": turn.usage.prompt_tokens,
                    "gen_ai.usage.output_tokens": turn.usage.completion_tokens,
                    "gen_ai.usage.cache_read.input_tokens": turn.usage.cached_prompt_tokens,
                    "gen_ai.usage.reasoning.output_tokens": turn.usage.reasoning_tokens,
                    "llm.token_count.prompt": turn.usage.prompt_tokens,
                    "llm.token_count.completion": turn.usage.completion_tokens,
                    "wandergent.finish_reason": turn.finish_reason,
                }
            )


async def _stream_turn(
    client: AsyncOpenAI,
    model: str,
    turn: Turn,
    **kwargs,
) -> AsyncIterator[PlanEvent]:
    """Stream one assistant turn into `turn`, yielding progress as the text arrives.

    Transport failures are translated here so every caller gets PlanningError rather
    than provider exceptions, whether they happen on connect or mid-stream.
    """
    last_hint: str | None = None
    refusal = ""
    try:
        stream = await client.chat.completions.create(
            model=model,
            stream=True,
            # OpenAI reasoning models reject the legacy `max_tokens` parameter. The
            # current API uses `max_completion_tokens` for the same output ceiling.
            max_completion_tokens=settings.llm_max_output_tokens,
            # Keep reasoning disabled for the planner's function-tool calls.
            reasoning_effort="none",
            # Buys a final chunk with empty `choices` and populated `usage`. An endpoint
            # that ignores the option never sends it, and the run reports zero.
            stream_options={"include_usage": True},
            **kwargs,
        )
        async for chunk in stream:
            # Read usage before the `choices` guard below: the usage chunk has none.
            if getattr(chunk, "usage", None):
                turn.usage = turn.usage.plus(_read_usage(chunk.usage, model))

            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            # Only the last chunk of a turn carries this, and some endpoints omit it.
            if getattr(choice, "finish_reason", None):
                turn.finish_reason = choice.finish_reason
            delta = choice.delta

            if getattr(delta, "refusal", None):
                refusal += delta.refusal

            if delta.content:
                turn.content += delta.content
                hint = composing_hint(turn.content)
                if hint is not None and hint != last_hint:
                    last_hint = hint
                    yield PlanEvent(type="composing", message=hint)

            for fragment in delta.tool_calls or []:
                index = fragment.index or 0
                while len(turn.tool_calls) <= index:
                    turn.tool_calls.append(StreamedToolCall())
                slot = turn.tool_calls[index]
                if fragment.id:
                    slot.id = fragment.id
                if fragment.function is not None:
                    if fragment.function.name:
                        slot.name += fragment.function.name
                    if fragment.function.arguments:
                        slot.arguments += fragment.function.arguments
        if refusal or turn.finish_reason == "content_filter":
            raise PlanningError("the language model declined to produce a plan")
    except (APITimeoutError, httpx.TimeoutException) as exc:
        raise PlanningTimeout("the language model timed out") from exc
    except (APIError, httpx.HTTPError) as exc:
        # Provider messages can contain endpoint URLs, request ids or response bodies.
        # Preserve the exception as the cause for server logs; the API gets a stable,
        # text-free error just like tool failures do.
        raise PlanningError("the language model is unavailable") from exc
