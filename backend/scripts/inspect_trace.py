"""Inspect/replay stored span structure without executing tools or spending tokens.

python -m scripts.inspect_trace backend/.traces/trace-ID.json [--compare other.json]
Paths are relative to the current working directory. Output contains only metrics.
"""

import argparse
import json
from collections import Counter
from pathlib import Path


def summarize(payload: dict) -> dict:
    spans = [
        span
        for resource in payload.get("resourceSpans", [])
        for scope in resource.get("scopeSpans", [])
        for span in scope.get("spans", [])
    ]
    attributes = [
        {a["key"]: next(iter(a["value"].values())) for a in s.get("attributes", [])} for s in spans
    ]
    roots = [s for s in spans if not s.get("parentSpanId")]
    model_metrics = {}
    for recorded, attrs in zip(spans, attributes, strict=True):
        if attrs.get("openinference.span.kind") != "LLM":
            continue
        model = attrs.get("gen_ai.request.model", "unknown")
        metrics = model_metrics.setdefault(
            model, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "ms": 0}
        )
        metrics["calls"] += 1
        for key, attribute in (
            ("input_tokens", "gen_ai.usage.input_tokens"),
            ("output_tokens", "gen_ai.usage.output_tokens"),
            ("cached_tokens", "gen_ai.usage.cache_read.input_tokens"),
        ):
            metrics[key] += int(attrs.get(attribute, 0))
        metrics["ms"] += (
            int(recorded["endTimeUnixNano"]) - int(recorded["startTimeUnixNano"])
        ) / 1e6
    root_attrs = [attrs for s, attrs in zip(spans, attributes, strict=True) if s in roots]
    return {
        "trace_ids": sorted({s["traceId"] for s in spans}),
        "duration_ms": sum(
            (int(s["endTimeUnixNano"]) - int(s["startTimeUnixNano"])) / 1e6 for s in roots
        ),
        "span_count": len(spans),
        "model_metrics": model_metrics,
        "outcomes": dict(
            Counter(a["wandergent.outcome"] for a in root_attrs if a.get("wandergent.outcome"))
        ),
        "estimated_model_cost_usd": (
            sum(float(a["wandergent.cost.usd"]) for a in root_attrs)
            if root_attrs and all("wandergent.cost.usd" in a for a in root_attrs)
            else None
        ),
        "configuration_versions": [
            {
                key: a[key]
                for key in (
                    "wandergent.prompt.sha256",
                    "wandergent.schema.sha256",
                    "wandergent.tools.sha256",
                )
                if key in a
            }
            for a in attributes
            if "wandergent.schema.sha256" in a
        ],
        "models": dict(
            Counter(
                a["gen_ai.request.model"]
                for a in attributes
                if a.get("openinference.span.kind") == "LLM"
            )
        ),
        "tools": dict(
            Counter(a["gen_ai.tool.name"] for a in attributes if a.get("gen_ai.tool.name"))
        ),
        "failures": dict(Counter(a["error.type"] for a in attributes if a.get("error.type"))),
        "versions": sorted(
            {
                a["wandergent.implementation.sha256"]
                for a in attributes
                if a.get("wandergent.implementation.sha256")
            }
        ),
        "timeline": [
            {
                "span_id": s["spanId"],
                "parent_id": s.get("parentSpanId"),
                "name": s["name"],
                "duration_ms": round(
                    (int(s["endTimeUnixNano"]) - int(s["startTimeUnixNano"])) / 1e6, 2
                ),
            }
            for s in sorted(spans, key=lambda s: int(s["startTimeUnixNano"]))
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    report = {"current": summarize(json.loads(args.path.read_text(encoding="utf-8")))}
    if args.compare:
        report["comparison"] = summarize(json.loads(args.compare.read_text(encoding="utf-8")))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
