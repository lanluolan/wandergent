"""The tool the agent uses to remember something about the traveller.

A tool rather than a separate extraction pass: the model is already reading the request,
so noticing "I don't want to hike" costs nothing extra, and *what is worth remembering*
becomes the agent's judgement rather than a regex or a second LLM call per request.

`user_id` is **not** a tool argument. Identity comes from the request context, injected by
the registry -- a model that can name whose memory it writes to can write into anyone's.
"""

import logging
import re

from app.memory import store as default_store
from app.memory.store import Preference, PreferenceStore
from app.tools.base import BAD_REQUEST, ToolOutcome

logger = logging.getLogger(__name__)

# More than this in one call is the model dumping the whole request into memory rather
# than picking out what is durable.
MAX_PER_CALL = 5


class RememberOutcome(ToolOutcome):
    stored: list[str] = []
    already_known: list[str] = []
    ignored_one_off: list[str] = []


_KEY = re.compile(r"^[a-z0-9][a-z0-9_.:-]{2,79}$")


REMEMBER_TOOL_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "remember_preference",
        "description": (
            "Save a durable fact about this traveller for future trips -- tastes, things they "
            "avoid, constraints like diet or mobility, who they travel with. Call it when the "
            "request reveals something that would still be true on a different trip to a "
            "different city. Do NOT save one-off details of this trip such as dates, the "
            "destination, or this trip's budget."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "preferences": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "key": {
                                "type": "string",
                                "pattern": "^[a-z0-9][a-z0-9_.:-]{2,79}$",
                                "description": (
                                    "Language-independent semantic slot, e.g. "
                                    "'interest:museums' or 'avoid:hiking'. Reuse the same "
                                    "key when correcting or translating the same preference."
                                ),
                            },
                            "text": {
                                "type": "string",
                                "description": "Short canonical statement in English.",
                            },
                            "scope": {
                                "type": "string",
                                "enum": ["durable", "this_trip"],
                                "description": (
                                    "durable only if it should apply in another city and "
                                    "on another date; otherwise this_trip."
                                ),
                            },
                        },
                        "required": ["key", "text", "scope"],
                        "additionalProperties": False,
                    },
                    "description": (
                        "Candidate traveller facts. One-off dates, destination, budget, "
                        "or requests for this itinerary must use scope=this_trip and are "
                        "reported but never persisted."
                    ),
                }
            },
            "required": ["preferences"],
            "additionalProperties": False,
        },
    },
}


async def remember_preference(
    preferences: list[str | dict],
    *,
    context: dict | None = None,
    store: PreferenceStore | None = None,
) -> RememberOutcome:
    """Store durable preferences for the user this request belongs to."""
    user_id = (context or {}).get("user_id") or ""
    if not user_id:
        # Anonymous requests are legitimate, so this is a no-op rather than an error the
        # model would try to work around.
        return RememberOutcome(ok=True, error=None, stored=[], already_known=[])

    if not isinstance(preferences, list):
        return RememberOutcome(
            ok=False, error="preferences must be a list of strings", code=BAD_REQUEST
        )

    wanted: list[str] = []
    keys: list[str | None] = []
    ignored: list[str] = []
    for item in preferences[:MAX_PER_CALL]:
        if isinstance(item, str):
            # Backward-compatible for old clients and saved scripted runs. New model
            # calls use structured entries from the schema above.
            wanted.append(item)
            keys.append(None)
            continue
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            continue
        text = item["text"]
        if item.get("scope") != "durable":
            ignored.append(text)
            continue
        key = item.get("key")
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            continue
        wanted.append(text)
        keys.append(key)
    target = store or default_store
    stored = await target.remember(user_id, wanted, keys=keys)
    already = [text for text in wanted if text not in stored]

    logger.info("remembered %s new preference(s) for %s", len(stored), user_id)
    return RememberOutcome(
        ok=True,
        stored=stored,
        already_known=already,
        ignored_one_off=ignored,
    )


def recall_block(preferences: list[Preference]) -> str:
    """Render known preferences for the system prompt."""
    lines = "\n".join(
        f"- [key={preference.key}] {preference.text}"
        if preference.key
        else f"- [legacy-unkeyed] {preference.text}"
        for preference in preferences
    )
    return (
        "You already know this traveller:\n"
        f"{lines}\n"
        "Apply what is relevant to this trip without being asked, and do not ask them to "
        "repeat it. If the new request corrects an entry, call remember_preference with "
        "the same key and the new canonical English text. The new request always wins."
    )
