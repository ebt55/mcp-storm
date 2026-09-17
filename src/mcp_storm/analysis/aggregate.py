"""Aggregate runs/index.jsonl into the numbers table (BUILD-GUIDE §2.1, §6.4).

Every README number regenerates from here: `storm report --all`.
"""

from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path
from typing import Any

from mcp_storm.analysis.stats import p95, retry_metrics, wilson_ci
from mcp_storm.paths import runs_dir as default_runs_dir
from mcp_storm.runner.worker import read_index


def aggregate(index_records: list[dict], ledger_rows_by_run: dict[str, list[dict]] | None = None,
              ) -> list[dict[str, Any]]:
    """One row per (model, mode, dose) cell with Wilson CIs and derived metrics."""
    cells: dict[tuple, list[dict]] = {}
    for r in index_records:
        if str(r.get("run_id", "")).startswith("smoke-"):
            continue  # harness-validation smoke, excluded (§7)
        model = str(r.get("model", ""))
        mode = "clean" if r.get("dose") in (0, 0.0) else r.get("mode", "clean")
        cells.setdefault((model, mode, float(r.get("dose", 0.0))), []).append(r)

    clean_tokens: dict[str, float] = {}
    for (model, mode, dose), runs in cells.items():
        if mode == "clean":
            clean_tokens[model] = statistics.mean(
                [r.get("tokens_in", 0) + r.get("tokens_out", 0) for r in runs]
            )

    rows: list[dict[str, Any]] = []
    for (model, mode, dose), runs in sorted(cells.items()):
        n = len(runs)
        completed = sum(1 for r in runs if r.get("outcome") == "completed")
        lo, hi = wilson_ci(completed, n)
        tokens = [r.get("tokens_in", 0) + r.get("tokens_out", 0) for r in runs]
        mean_tokens = statistics.mean(tokens) if tokens else 0.0
        walls = [r.get("wall_s", 0.0) for r in runs]
        cost = sum(r.get("cost_usd", 0.0) for r in runs)
        storms = storm_runs = 0
        if ledger_rows_by_run:
            checked = 0
            for r in runs:
                lrows = ledger_rows_by_run.get(r.get("run_id", ""), [])
                if lrows:
                    checked += 1
                    if retry_metrics(lrows)["storm_run"]:
                        storm_runs += 1
            storms = storm_runs
        rows.append(
            {
                "model": model, "mode": mode, "dose": dose, "n": n,
                "completed": completed,
                "completion_rate": round(completed / n, 4) if n else 0.0,
                "completion_ci_lo": round(lo, 4), "completion_ci_hi": round(hi, 4),
                "mean_tokens": round(mean_tokens, 1),
                "cost_multiplier": (round(mean_tokens / clean_tokens[model], 3)
                                    if clean_tokens.get(model) else None),
                "escalated": sum(1 for r in runs if r.get("escalated")),
                "refusals": sum(1 for r in runs if r.get("refusal")),
                "retry_storm_runs": storms,
                "wall_p95_s": round(p95(walls), 1),
                "cost_usd": round(cost, 4),
                "usd_per_completed": round(cost / completed, 4) if completed else None,
                "outcomes": {
                    o: sum(1 for r in runs if r.get("outcome") == o)
                    for o in ("completed", "wrong_answer", "loop", "timeout", "crashed")
                },
            }
        )
    return rows


def ledgers_by_run(runs: Path) -> dict[str, list[dict]]:
    from mcp_storm.proxy.ledger import read_run_ledger

    out: dict[str, list[dict]] = {}
    for run_path in sorted(Path(runs).glob("*/proxy.*.jsonl")):
        run_dir = run_path.parent
        rows = read_run_ledger(run_dir)
        if rows:
            out[run_dir.name] = rows
    return out


def write_tables(rows: list[dict[str, Any]], runs: Path) -> dict[str, Path]:
    csv_path = Path(runs) / "cells.csv"
    fieldnames = [k for k in rows[0] if k != "outcomes"] + [
        "completed_n", "wrong_answer_n", "loop_n", "timeout_n", "crashed_n"
    ] if rows else []
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            flat = {k: v for k, v in row.items() if k != "outcomes"}
            for key, count in row.get("outcomes", {}).items():
                flat[f"{key}_n"] = count
            writer.writerow(flat)

    md_path = Path(runs) / "summary.md"
    lines = [
        "# mcp-storm — dose-response summary (regenerated from runs/)",
        "",
        "| model | mode | dose | n | completed | rate (95% CI) | cost× | storms | P95 s | $/done |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['model']} | {row['mode']} | {row['dose']:.0%} | {row['n']} | "
            f"{row['completed']} | {row['completion_rate']:.0%} "
            f"([{row['completion_ci_lo']:.0%}, {row['completion_ci_hi']:.0%}]) | "
            f"{row['cost_multiplier'] if row['cost_multiplier'] is not None else '—'} | "
            f"{row['retry_storm_runs']} | {row['wall_p95_s']} | "
            f"{row['usd_per_completed'] if row['usd_per_completed'] is not None else '—'} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"csv": csv_path, "markdown": md_path}


def regenerate_all(runs: Path | None = None) -> dict[str, Any]:
    runs = Path(runs) if runs else default_runs_dir()
    records = read_index(runs)
    ledgers = ledgers_by_run(runs)
    rows = aggregate(records, ledgers)
    tables = write_tables(rows, runs) if rows else {}
    return {"runs": len(records), "cells": len(rows), "tables": tables, "rows": rows}
