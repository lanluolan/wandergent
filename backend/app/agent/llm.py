"""LLM plumbing: client, streaming turns, and parsing the model's output.

Split out from the orchestrator so that module can be about the *shape* of the run --
the state graph -- rather than about the mechanics of talking to an API. Nothing here
knows what a planning run looks like; it knows how to stream one turn and how to read
an itinerary out of text.
"""

import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from openai import APIError, APITimeoutError, AsyncOpenAI
from pydantic import ValidationError

from app.agent.events import PlanEvent
from app.agent.results import Usage
from app.agent.schemas import Itinerary
from app.config import settings

logger = logging.getLogger(__name__)

# Pulled out of the partial JSON to show what is being written right now. Deliberately
# tolerant: these run against a half-finished document, so they must never assume the
# buffer parses.
_TITLE_RE = re.compile(r'"title"\s*:\s*"([^"\\]{1,60})"')
_DATE_RE = re.compile(r'"date"\s*:\s*"(\d{4}-\d{2}-\d{2})"')

# What to tell the model when its reply was cut off. Deliberately not the JSON parser's
# error text: that says "expecting ',' at column 4678", and a model handed that will
# obediently add a comma to a document whose real problem is that it does not fit. Name
# the cause and name the levers, or the repair round is wasted.
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


class LlmCredentialsError(ValueError):
    """The caller supplied their own LLM credentials and they are not usable.

    Deliberately not a `PlanningError`: those mean the service or the upstream model
    failed and map to 5xx. This one means the *request* was wrong, which is a 400. A
    caller who cannot tell the two apart retries forever against a server that will
    never accept what they are sending.
    """


@dataclass(frozen=True, repr=False)
class LlmOverride:
    """LLM credentials supplied per request instead of read from the environment.

    Exists so someone who is not the operator can run the app against their own account:
    the operator's key stays in `.env` and is never handed out, and the caller pays for
    their own tokens.

    `repr` is written by hand and `__str__` follows it. A dataclass' generated repr would
    print the key, and this object ends up inside exception messages, structured logs and
    debugger frames -- all of which get pasted into issues.
    """

    api_key: str
    base_url: str
    model: str

    def __repr__(self) -> str:
        return (
            f"LlmOverride(api_key=***{self.api_key[-4:]}, "
            f"base_url={self.base_url!r}, model={self.model!r})"
        )


def allowed_byok_base_urls() -> frozenset[str]:
    """Endpoints a caller may point their own key at.

    Always includes the server's own, so the common case -- same provider, different
    account -- needs no configuration. Anything else has to be listed by the operator in
    `LLM_BYOK_BASE_URLS`.

    This is an allowlist rather than a validity check on purpose. The base URL is a
    caller-supplied address that *this server* then makes requests to, which is
    server-side request forgery by construction: an unchecked one can be pointed at a
    cloud metadata endpoint or an internal host, and the reply comes back in an error
    message. A hostname check cannot fix that -- DNS can resolve anywhere, and can change
    between the check and the call -- so the set of reachable hosts has to be closed.
    """
    configured = (url.strip().rstrip("/") for url in settings.llm_byok_base_urls.split(","))
    return frozenset({settings.openai_base_url.rstrip("/"), *(u for u in configured if u)})


def parse_override(
    api_key: str | None,
    base_url: str | None,
    model: str | None,
) -> LlmOverride | None:
    """Read per-request LLM credentials, or None to use the server's own.

    The model may only be chosen alongside a key. Letting a caller pick the model while
    the *server* pays turns a daily plan budget into an open-ended one -- the ceiling
    counts runs, and the cost of a run is exactly what the model choice decides.

    Raises [LlmCredentialsError] rather than ignoring what it cannot honour: a silently
    dropped header looks like a working setting that never takes effect, which is the
    single most expensive kind of bug to find from the client side.
    """
    key = (api_key or "").strip()
    url = (base_url or "").strip().rstrip("/")
    name = (model or "").strip()

    if not key:
        if url or name:
            raise LlmCredentialsError(
                "a model or endpoint was supplied without an API key; "
                "send your own key alongside them, or send none of the three"
            )
        return None

    if url and url not in allowed_byok_base_urls():
        raise LlmCredentialsError(
            f"this server does not allow {url!r} as an LLM endpoint; "
            "leave the endpoint unset to use the one it is configured with"
        )

    return LlmOverride(
        api_key=key,
        base_url=url or settings.openai_base_url,
        model=name or settings.openai_model,
    )


@dataclass
class StreamedToolCall:
    """A tool call reassembled from deltas.

    The API sends a tool call in fragments across chunks -- id and name usually in the
    first, arguments a few characters at a time -- keyed by index, so the pieces have
    to be accumulated per slot rather than read off any single chunk.
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

    # Why the model stopped. "length" means the reply was cut off at the output limit,
    # which arrives as a half-written JSON object and would otherwise be reported as a
    # syntax error -- sending the repair round to fix a comma when the real problem is
    # that the plan is too long to fit. Observed live on a plan whose activities each
    # carried highlights plus a full street address.
    finish_reason: str | None = None

    @property
    def truncated(self) -> bool:
        return self.finish_reason == "length"


def build_client(override: LlmOverride | None = None) -> AsyncOpenAI:
    """Create the LLM client. Timeout is mandatory, per the project's hard rules.

    With an `override` the caller's key and endpoint are used and the server's are not
    read at all, so a server with no key of its own still serves callers who bring one.
    """
    if override is not None:
        return AsyncOpenAI(
            api_key=override.api_key,
            base_url=override.base_url,
            timeout=settings.llm_timeout_seconds,
        )
    if not settings.openai_api_key:
        raise PlanningConfigError("OPENAI_API_KEY is not set; put your key in backend/.env")
    return AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        timeout=settings.llm_timeout_seconds,
    )


def strip_fences(raw: str) -> str:
    """Pull the JSON object out of a reply, whatever the model wrapped it in.

    Models add markdown fences even when told not to, and -- observed live -- they also
    preface the answer with a sentence: "I have everything I need to build the
    itinerary." followed by a fenced block. The previous version only stripped fences
    when the text *started* with one, so a single line of preamble defeated the fast
    path and the run paid for a whole second generation. The fast path exists precisely
    to avoid that, so it has to survive the model being chatty.

    Deliberately positional -- first "{" to last "}" -- rather than a JSON scanner:
    that is the object for every shape seen in practice, and anything cleverer would be
    guessing at malformed input the parser is about to reject anyway. There is no
    shortcut for text that already starts with "{" either, because a reply can be valid
    JSON followed by "let me know if you want changes".
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
        return Itinerary.model_validate_json(strip_fences(raw)), None
    except ValidationError as exc:
        return None, str(exc)


def parse_turn(turn: Turn) -> tuple[Itinerary | None, str | None]:
    """Read an itinerary out of a finished turn, blaming the right thing when it fails.

    Same as `parse_itinerary`, except a reply the endpoint cut off is reported as the
    truncation it is. Both callers feed the error straight back to the model, so which
    story it gets decides whether the repair round is spent shortening or shuffling
    punctuation.
    """
    itinerary, errors = parse_itinerary(turn.content)
    if itinerary is None and turn.truncated:
        logger.info("reply truncated at the output limit after %s chars", len(turn.content))
        return None, TRUNCATION_ERROR
    return itinerary, errors


def is_empty(turn: Turn) -> bool:
    """A turn that said nothing and asked for nothing.

    It cannot be sent back: an assistant message with neither content nor tool calls is
    rejected outright -- observed live as `400 ... assistant must provide content,
    reasoning_content or tool_calls`, which killed a whole eval case. It also carries no
    information, so dropping it loses nothing.
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
    """Stream one assistant turn into `turn`, yielding progress as the text arrives.

    Transport failures are translated here so every caller gets PlanningError rather
    than provider exceptions, whether they happen on connect or mid-stream.
    """
    last_hint: str | None = None
    try:
        stream = await client.chat.completions.create(
            model=model,
            stream=True,
            # Ask for the room a full itinerary needs. Left unset, the endpoint applies
            # its own default -- 4k on ours, which a week-long plan with highlights
            # overruns, arriving as half a JSON document. Detection below is the
            # backstop; this is the fix.
            max_tokens=settings.llm_max_output_tokens,
            # The endpoint sends a final chunk with empty `choices` and populated
            # `usage`. Endpoints that ignore the option simply never send it, and the
            # run reports zero rather than failing.
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
    except APITimeoutError as exc:
        raise PlanningTimeout("the language model timed out") from exc
    except APIError as exc:
        raise PlanningError(f"the language model is unavailable: {exc}") from exc
