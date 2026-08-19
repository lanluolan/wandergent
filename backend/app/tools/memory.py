"""The tool the agent uses to remember something about the traveller.

Making this a tool rather than a separate extraction pass is the whole design: the
model is already reading the request, so noticing "I don't want to hike" costs nothing
extra, and *what is worth remembering* becomes the agent's judgement rather than a
regex or a second LLM call on every request.

`user_id` deliberately is **not** a tool argument. Identity comes from the request
context, injected by the registry -- a model that could name the user whose memory it
writes to is a model that can write into someone else's.
"""

import logging

from app.memory import store as default_store
from app.memory.store import PreferenceStore
from app.tools.base import ToolOutcome

logger = logging.getLogger(__name__)

# More than this in one call is the model dumping the whole request into memory rather
# than picking out what is durable.
MAX_PER_CALL = 5


class RememberOutcome(ToolOutcome):
    stored: list[str] = []
    already_known: list[str] = []


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
                    "items": {"type": "string"},
                    "description": (
                        "Short statements in the third person, e.g. 'avoids hiking', "
                        "'loves regional food', 'travels with a toddler'."
                    ),
                }
            },
            "required": ["preferences"],
            "additionalProperties": False,
        },
    },
}


async def remember_preference(
    preferences: list[str],
    *,
    context: dict | None = None,
    store: PreferenceStore | None = None,
) -> RememberOutcome:
    """Store durable preferences for the user this request belongs to."""
    user_id = (context or {}).get("user_id") or ""
    if not user_id:
        # Anonymous requests are legitimate -- the client may have no account yet -- so
        # this is a no-op rather than an error the model would try to work around.
        return RememberOutcome(ok=True, error=None, stored=[], already_known=[])

    if not isinstance(preferences, list):
        return RememberOutcome(ok=False, error="preferences must be a list of strings")

    wanted = [text for text in preferences if isinstance(text, str)][:MAX_PER_CALL]
    target = store or default_store
    stored = await target.remember(user_id, wanted)
    already = [text for text in wanted if text not in stored]

    logger.info("remembered %s new preference(s) for %s", len(stored), user_id)
    return RememberOutcome(ok=True, stored=stored, already_known=already)


def recall_block(preferences: list[str]) -> str:
    """Render known preferences for the system prompt."""
    lines = "\n".join(f"- {text}" for text in preferences)
    return (
        "You already know this traveller:\n"
        f"{lines}\n"
        "Apply what is relevant to this trip without being asked, and do not ask them to "
        "repeat it. If something here is contradicted by the new request, the new request wins."
    )
