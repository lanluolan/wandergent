"""Send a synthetic, content-free trace to the configured OTLP JSON collector.

No model or travel API is called. Inspect export logs and search the printed trace
ID in Phoenix to finish verification; exit success alone does not confirm receipt.
"""

import asyncio
import json

from app.agent.events import PlanEvent
from app.agent.results import PlanResult
from app.config import settings
from app.observability import span, traced_stream


@traced_stream
async def synthetic_run():
    with span("graph.smoke", **{"openinference.span.kind": "CHAIN"}):
        with span("tool.smoke", **{"openinference.span.kind": "TOOL"}):
            pass
        try:
            with span("validation.smoke"):
                raise ValueError("Synthetic failure: this message is not exported")
        except ValueError:
            pass
    yield PlanEvent(type="result", result=PlanResult())


async def main():
    if not settings.otel_exporter_otlp_traces_endpoint:
        raise SystemExit("Set OTEL_EXPORTER_OTLP_TRACES_ENDPOINT to the collector /v1/traces")
    async for event in synthetic_run():
        print(json.dumps({"trace_id": event.result.trace_id, "scenario": "synthetic-smoke"}))


if __name__ == "__main__":
    asyncio.run(main())
