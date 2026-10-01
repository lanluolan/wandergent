"""Compare retained live eval reports without claiming a controlled causal A/B."""

import argparse
import json
from pathlib import Path


def compare(before: dict, after: dict) -> dict:
    keys = (
        "model",
        "fast_model",
        "today",
        "maps_enabled",
        "suite_sha256",
        "token_prices_usd_per_million",
    )
    mismatches = [key for key in keys if before.get(key) != after.get(key)]
    unverified = [key for key in keys if key not in before or key not in after]
    left = {r["id"]: r for r in before.get("reports", [])}
    right = {r["id"]: r for r in after.get("reports", [])}
    common = sorted(left.keys() & right.keys())

    def totals(reports):
        def tools(key):
            if not reports or any(key not in r.get("tool_usage", {}) for r in reports):
                return None
            return sum(r["tool_usage"][key] for r in reports)

        return {
            "passed_cases": sum(bool(r["passed"]) for r in reports),
            "checks": sum(r["checks_run"] for r in reports),
            "failed_checks": sum(len(r.get("failures", {})) for r in reports),
            "seconds": sum(r["seconds"] for r in reports),
            "llm_calls": sum(r["usage"]["llm_calls"] for r in reports),
            "tokens": sum(r["usage"]["total_tokens"] for r in reports),
            "executed_tools": tools("executed_calls"),
            "cache_hits": tools("cache_hits"),
            "estimated_model_usd": (
                sum(r["cost_usd"] for r in reports)
                if reports and all(r.get("cost_usd") is not None for r in reports)
                else None
            ),
        }

    old, new = totals([left[k] for k in common]), totals([right[k] for k in common])
    return {
        "common_case_ids": common,
        "setting_mismatches": mismatches,
        "unverified_settings": unverified,
        "reports_complete": bool(before.get("completed")) and bool(after.get("completed")),
        "before": old,
        "after": new,
        "delta": {
            k: new[k] - old[k] if old[k] is not None and new[k] is not None else None for k in old
        },
        "quality_regressed_cases": [
            k for k in common if left[k]["passed"] and not right[k]["passed"]
        ],
        "uncontrolled_live_variance": True,
        "causal_efficiency_acceptance": "not_established_by_historical_report_comparison",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            compare(
                json.loads(args.before.read_text(encoding="utf-8-sig")),
                json.loads(args.after.read_text(encoding="utf-8-sig")),
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
