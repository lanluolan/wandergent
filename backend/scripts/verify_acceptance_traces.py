"""Read-only Phoenix persistence audit for release, paired and fault-injection reports."""

import argparse
import asyncio
import json
from pathlib import Path

from scripts.verify_live_hours import persisted_spans
from scripts.verify_phoenix import verify


def trace_ids(report: dict) -> list[str]:
    reports = report.get("reports", [])
    if "rows" in report:
        reports = [row["report"] for row in report["rows"]]
    turns = [turn for case in reports for turn in case.get("turns", [])]
    if "result" in report:
        turns.append(report["result"])
    ids = [turn["trace_id"] for turn in turns if turn.get("trace_id")]
    if not ids:
        raise ValueError("Report has no planning trace identities")
    return list(dict.fromkeys(ids))


async def audit(paths: list[Path], endpoint: str) -> dict:
    output = {"reports": [], "unique_traces": 0, "passed": True}
    seen = set()
    for path in paths:
        report = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8-sig"))
        rows = []
        for trace_id in trace_ids(report):
            spans = await persisted_spans(trace_id, endpoint, {"plan", "graph.validate"})
            checked = verify(spans, trace_id, {"plan"})
            rows.append(
                {
                    key: checked[key]
                    for key in (
                        "trace_id",
                        "persisted_spans",
                        "root_status",
                        "outcome",
                        "implementation_sha256",
                        "failures",
                    )
                }
            )
            seen.add(trace_id)
        output["reports"].append({"report": path.name, "traces": rows})
    output["unique_traces"] = len(seen)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--endpoint", default="http://127.0.0.1:6006")
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()
    workspace = Path(__file__).resolve().parents[2]
    target = args.json.resolve()
    if not target.is_relative_to(workspace) or target.exists():
        parser.error("Output must be a new workspace-local file")
    if any(not source.resolve().is_relative_to(workspace) for source in args.reports):
        parser.error("Source reports must be workspace-local")
    output = asyncio.run(audit(args.reports, args.endpoint))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({"persisted_traces": output["unique_traces"], "passed": output["passed"]}))


if __name__ == "__main__":
    main()
