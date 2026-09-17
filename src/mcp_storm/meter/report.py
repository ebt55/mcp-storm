"""Meter report + ledger join (BUILD-GUIDE §8, §5.1).

Join rule (§5.1): join meter.json LLM calls to proxy ledger tool calls on
(run_id, tool_name), nearest timestamp within +-2s; a join whose matched-volume
mismatch exceeds 5% fails (test_meter_join gate). In practice LLM calls do not
carry tool names -- the operative join is the SANITY join: every ledgered tool
call must sit BETWEEN two LLM calls of the same run (a tool call outside any
LLM-to-LLM window means ledger/meter disagree about the run's shape). The
volume check compares per-run tool-call counts against per-run LLM-call
counts; mismatch >5% of max(count) fails.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

JOIN_TOLERANCE_S = 2.0
JOIN_MISMATCH_LIMIT = 0.05


def load_meter(run_dir: Path) -> dict[str, Any]:
    return json.loads((Path(run_dir) / "meter.json").read_text(encoding="utf-8"))


def join_meter_to_ledger(meter: dict, ledger_rows: list[dict]) -> dict[str, Any]:
    """Returns per-tool ledger aggregates + the join sanity verdict."""
    run_id = meter.get("run_id")
    rows = [r for r in ledger_rows if r.get("run_id") == run_id]
    per_tool: dict[str, dict[str, float]] = {}
    for row in rows:
        bucket = per_tool.setdefault(row["tool"], {"calls": 0, "latency_ms_sum": 0.0,
                                                   "bytes_sum": 0, "faulted": 0})
        bucket["calls"] += 1
        bucket["latency_ms_sum"] += row.get("latency_ms") or 0.0
        bucket["bytes_sum"] += row.get("result_bytes") or 0
        if row.get("fault_mode_injected", "none") != "none":
            bucket["faulted"] += 1
    for bucket in per_tool.values():
        calls = bucket["calls"]
        bucket["latency_ms_mean"] = round(bucket["latency_ms_sum"] / calls, 2) if calls else 0.0

    llm_calls = meter.get("totals", {}).get("llm_calls", 0)
    tool_calls = len(rows)
    window_violations = []
    llm_times = sorted(c["ts"] for c in meter.get("calls", []))
    if llm_times:
        for row in rows:
            t = (row.get("t_request_ns") or 0) / 1e9
            before = [x for x in llm_times if x <= t + JOIN_TOLERANCE_S]
            after = [x for x in llm_times if x >= t - JOIN_TOLERANCE_S]
            if not before or not after:
                window_violations.append({"tool": row["tool"], "call_seq": row.get("call_seq")})
    denom = max(llm_calls, tool_calls, 1)
    mismatch = abs(llm_calls - tool_calls) / denom
    joined = {
        "run_id": run_id,
        "per_tool": per_tool,
        "tool_calls": tool_calls,
        "llm_calls": llm_calls,
        "volume_mismatch": round(mismatch, 4),
        "window_violations": window_violations,
        "join_ok": mismatch <= JOIN_MISMATCH_LIMIT and len(window_violations) == 0,
    }
    return joined


def write_report(run_dir: Path, meter: dict, joined: dict) -> dict[str, Any]:
    report = {
        "run_id": meter.get("run_id"),
        "model": meter.get("model"),
        "totals": meter.get("totals"),
        "per_node": meter.get("per_node"),
        "tools": joined["per_tool"],
        "join": {k: joined[k] for k in ("tool_calls", "llm_calls",
                                        "volume_mismatch", "join_ok")},
    }
    (Path(run_dir) / "meter_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    lines = [
        f"# mcp-meter report — run {meter.get('run_id')}",
        "",
        f"- model: {meter.get('model')}",
        f"- LLM calls: {report['totals']['llm_calls']}, "
        f"tokens in/out/total: {report['totals']['input_tokens']}/"
        f"{report['totals']['output_tokens']}/{report['totals']['total_tokens']}",
        "",
        "| node | calls | in | out | total |",
        "|---|---|---|---|---|",
    ]
    for node, agg in sorted((meter.get("per_node") or {}).items()):
        lines.append(f"| {node} | {agg['llm_calls']} | {agg['input_tokens']} | "
                     f"{agg['output_tokens']} | {agg['total_tokens']} |")
    lines += ["", "| tool | calls | faulted | mean latency ms | result bytes |",
              "|---|---|---|---|---|"]
    for tool, agg in sorted(joined["per_tool"].items()):
        lines.append(f"| {tool} | {agg['calls']} | {agg['faulted']} | "
                     f"{agg['latency_ms_mean']} | {agg['bytes_sum']} |")
    (Path(run_dir) / "meter.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
