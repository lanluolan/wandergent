import json
from functools import lru_cache
from pathlib import Path

from app.agent.schemas import Itinerary

SKILL_ROOT = Path(__file__).resolve().parents[2] / "skills" / "travel-planner"
PLANNING_REFERENCES = (
    "planning-criteria.md",
    "travel-guidelines.md",
    "itinerary-style.md",
    "output-template.md",
)


@lru_cache(maxsize=1)
def planning_skill() -> str:
    source = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    workflow = source.split("### 1. Gather essentials first", 1)[1]
    workflow = workflow.split("### 7. Write to Wanderlog", 1)[0]
    rules = source.split("## Planning rules", 1)[1].split("## References", 1)[0]
    blocks = ["# Travel Planner\n\n### 1. Gather essentials first" + workflow, rules]
    for name in PLANNING_REFERENCES:
        text = (SKILL_ROOT / "references" / name).read_text(encoding="utf-8")
        if name == "itinerary-style.md":
            text = text.split("## Wanderlog translation guidance", 1)[0]
        if name == "output-template.md":
            text = text.split("## Optional Wanderlog execution plan", 1)[0]
        blocks.append(text)
    return "\n\n".join(
        "\n".join(line for line in block.splitlines() if "wanderlog" not in line.lower())
        for block in blocks
    )


def bind_web_sources(itinerary: Itinerary | None, messages: list[dict]) -> Itinerary | None:
    if itinerary is None or itinerary.travel_guide is None:
        return itinerary
    observed = set()
    for message in messages:
        if message.get("role") != "tool":
            continue
        try:
            payload = json.loads(message.get("content", ""))
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            continue
        sources = payload.get("sources")
        if not isinstance(sources, list):
            continue
        for source in sources:
            if isinstance(source, dict) and isinstance(source.get("url"), str):
                observed.add(source["url"])
    guide = itinerary.travel_guide.model_copy(
        update={
            "source_urls": list(
                dict.fromkeys(url for url in itinerary.travel_guide.source_urls if url in observed)
            )
        }
    )
    return itinerary.model_copy(update={"travel_guide": guide})
