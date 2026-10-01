"""Verify persisted, content-free planning spans through Phoenix's read-only API."""

import argparse
import asyncio
import json

import httpx


def verify(data: list[dict], trace_id: str, expected: set[str]) -> dict:
    if not data:
        raise ValueError("No persisted spans found")
    ids = {s["context"]["span_id"] for s in data}
    names = {s["name"] for s in data}
    if not expected <= names:
        raise ValueError("Expected spans are missing")
    if any(s["context"]["trace_id"] != trace_id for s in data):
        raise ValueError("Trace identity mismatch")
    if any(s.get("parent_id") and s["parent_id"] not in ids for s in data):
        raise ValueError("Missing parent span")
    roots = [s for s in data if not s.get("parent_id")]
    if len(roots) != 1 or roots[0]["name"] != "plan":
        raise ValueError("Expected one plan root")
    attrs = roots[0]["attributes"]
    if not attrs.get("wandergent.implementation.sha256"):
        raise ValueError("Implementation fingerprint is missing")
    return {
        "trace_id": trace_id,
        "persisted_spans": len(data),
        "names": sorted(names),
        "failures": sorted(
            {s["attributes"]["error.type"] for s in data if "error.type" in s["attributes"]}
        ),
        "implementation_sha256": attrs["wandergent.implementation.sha256"],
        "outcome": attrs.get("wandergent.outcome"),
        "root_status": roots[0]["status_code"],
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace_id")
    parser.add_argument("--endpoint", default="http://127.0.0.1:6006")
    parser.add_argument("--expect", action="append", default=["plan"])
    args = parser.parse_args()
    if len(args.trace_id) != 32 or any(c not in "0123456789abcdef" for c in args.trace_id):
        parser.error("trace_id must be 32 lowercase hexadecimal characters")
    async with httpx.AsyncClient(timeout=5.0) as client:
        for attempt in range(5):
            response = await client.get(
                f"{args.endpoint.rstrip('/')}/v1/projects/wandergent/spans",
                params={"trace_id": args.trace_id, "limit": 1000},
            )
            response.raise_for_status()
            try:
                result = verify(response.json()["data"], args.trace_id, set(args.expect))
                print(json.dumps(result, indent=2))
                return
            except ValueError:
                if attempt == 4:
                    raise
                await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
