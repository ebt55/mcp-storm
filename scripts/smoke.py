"""Model smoke (BUILD-GUIDE §3.6 day-1 duty): 2 real-backend runs per model.

Verifies: model name resolves, reasoning_effort accepted (OpenAI),
usage_metadata arrives, measures tokens/wall/cost per run. Run ids carry the
smoke- prefix and are excluded from every number (predictions.json).
Costs are recorded into runs/spend.jsonl (DEVIATIONS #22).

Run: uv run python scripts/smoke.py
"""

from __future__ import annotations

import asyncio
import sys
import time

from mcp_storm.agent.models import MODEL_NAMES, get_model
from mcp_storm.envtools import load_dotenv
from mcp_storm.paths import runs_dir
from mcp_storm.prices import SpendTracker
from mcp_storm.runner.worker import run_one
from mcp_storm.tasks_io import load_task

RUNS_PER_MODEL = 2


async def main(models: list[str]) -> int:
    load_dotenv()
    runs = runs_dir()
    tracker = SpendTracker(runs_dir=runs)
    task = load_task("fs-08")  # trivial: create dir + one file
    failures = []
    print(f"{'model':28} {'run':5} {'outcome':12} {'tok_in':>7} {'tok_out':>7} "
          f"{'wall_s':>7} {'cost':>9}", flush=True)
    for model in models:
        for i in range(1, RUNS_PER_MODEL + 1):
            t0 = time.time()
            try:
                get_model(model)  # fail fast on bad names/keys
                record = await run_one(
                    run_id=f"smoke-{model.replace(':', '-').replace('/', '-')}-{i}",
                    task=task,
                    model_name=model,
                    model_factory=lambda m=model: get_model(m),
                    mode="passthrough",
                    dose=0.0,
                    seed="smoke",
                    runs_dir=runs,
                    backend="real",
                )
                print(f"{model:28} {i:<5} {record['outcome']:12} "
                      f"{record['tokens_in']:>7} {record['tokens_out']:>7} "
                      f"{record['wall_s']:>7} {record['cost_usd']:>9.6f}", flush=True)
                if record["cost_usd"]:
                    tracker.record({"kind": "run_cost", "run_id": record["run_id"],
                                    "cost_usd": record["cost_usd"], "smoke": True})
                if record["tokens_in"] == 0:
                    failures.append(f"{model}#{i}: no usage_metadata arrived")
                if record["outcome"] != "completed":
                    failures.append(f"{model}#{i}: outcome={record['outcome']} "
                                    f"({record['grader_failures'][:2]})")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{model}#{i}: {type(exc).__name__}: {exc}")
                print(f"{model:28} {i:<5} ERROR {exc}", flush=True)
            del t0
    print(f"\nspend.jsonl total after smoke: ${tracker.total():.6f}")
    if failures:
        print("SMOKE FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("smoke: all models healthy")
    return 0


if __name__ == "__main__":
    models = sys.argv[1:] or list(MODEL_NAMES)
    raise SystemExit(asyncio.run(main(models)))
