"""§7 exclusion mechanism: move index records (and their run dirs) aside so
excluded cells re-run fresh. Usage:
  uv run python scripts/exclude_runs.py --model openai:gpt-5-nano --reason "harness fix #28"
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from mcp_storm.paths import runs_dir


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="exclude all runs of this model")
    ap.add_argument("--prefix", help="exclude run_ids with this prefix")
    ap.add_argument("--reason", required=True)
    args = ap.parse_args()
    runs = runs_dir()
    index = runs / "index.jsonl"
    records = [json.loads(x) for x in index.read_text(encoding="utf-8").splitlines() if x.strip()]
    excluded = runs / "excluded-index.jsonl"

    def keep(r: dict) -> bool:
        if args.model and r.get("model") == args.model:
            return False
        if args.prefix and str(r.get("run_id", "")).startswith(args.prefix):
            return False
        return True

    kept = [r for r in records if keep(r)]
    dropped = [r for r in records if not keep(r)]
    for r in dropped:
        run_dir = runs / r["run_id"]
        if run_dir.exists():
            shutil.rmtree(run_dir)
        with open(excluded, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({**r, "excluded_reason": args.reason},
                                ensure_ascii=False) + "\n")
        print("excluded", r["run_id"])
    index.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept),
                     encoding="utf-8")
    print(f"kept {len(kept)}, excluded {len(dropped)}")


if __name__ == "__main__":
    main()
