"""storm-ops server: oracle durability, dedupe-awareness through the proxy
(BUILD-GUIDE §5.2, §9 vignette mechanics) -- all offline."""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import anyio
from conftest import storm_env
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def oracle_lines(tmp_path: Path, run_id: str) -> list[dict]:
    p = Path(tmp_path) / "runs" / run_id / "sandbox" / "oracle.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x]


async def _run_session(env, oracle: Path, calls: list[tuple[str, dict]]) -> list[str]:
    results: list[str] = []
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m", "mcp_storm.proxy", "--server-name", "storm-ops",
            "--spawn-cmd", sys.executable,
            "--spawn-args", "-m", "mcp_storm.servers.storm_ops", "--oracle", str(oracle),
        ],
        env=env,
    )
    try:
        async with stdio_client(params) as (read, write), ClientSession(
            read, write, read_timeout_seconds=timedelta(seconds=15)
        ) as session:
            await session.initialize()
            for tool, args in calls:
                res = await session.call_tool(tool, args)
                text = res.content[0].text if res.content else ""
                results.append(text)
    except BaseException:  # noqa: BLE001, S110 - teardown noise after outcomes captured
        pass
    return results


def test_append_ledger_durability_and_escalate(tmp_path):
    """Unkeyed appends always fire; escalate is recorded in the oracle."""
    from mcp_storm.proxy.idem import idempotency_key  # noqa: F401 (documented contrast)

    oracle = tmp_path / "runs" / "ops-run" / "sandbox" / "oracle.jsonl"
    env = storm_env(tmp_path, run_id="ops-run")
    results = anyio.run(
        _run_session,
        env,
        oracle,
        [
            ("append_ledger", {"note": "pay invoice 42"}),
            ("append_ledger", {"note": "pay invoice 42"}),  # identical, no key -> fires again
            ("escalate", {"reason": "tool backend too flaky"}),
        ],
    )
    assert results[0].startswith("appended#1")
    assert results[1].startswith("appended#2"), "unkeyed replay must double-fire (arm 1)"
    assert "ESCALATED" in results[2]
    lines = oracle_lines(tmp_path, "ops-run")
    kinds = [l["kind"] for l in lines]
    assert kinds == ["append", "append", "escalate"]
    assert all(l["idempotency_key"] == "" for l in lines[:2])


def test_idempotency_keys_dedupe(tmp_path):
    """STORM_IDEM=1: the proxy stamps the same key for identical (run,tool,args);
    the dedupe-aware server returns the ORIGINAL result and appends once."""
    oracle = tmp_path / "runs" / "idem-run" / "sandbox" / "oracle.jsonl"
    env = storm_env(tmp_path, run_id="idem-run", idem="1")
    results = anyio.run(
        _run_session,
        env,
        oracle,
        [
            ("append_ledger", {"note": "pay invoice 42"}),
            ("append_ledger", {"note": "pay invoice 42"}),  # same key -> deduped
            ("append_ledger", {"note": "different note"}),  # different key -> fires
        ],
    )
    assert results[0] == results[1], "replayed keyed call must return the original result"
    assert results[2].startswith("appended#2")
    lines = oracle_lines(tmp_path, "idem-run")
    assert len(lines) == 2
    assert lines[0]["idempotency_key"] == lines[0]["idempotency_key"] != ""


def test_startup_scan_recovers_dedupe_table(tmp_path):
    """A FRESH server process scanning an existing oracle dedupes a replayed key
    (the kill-9 resume scenario: proxy restarts, same run_id, same args)."""
    from mcp_storm.proxy.idem import idempotency_key

    oracle = tmp_path / "runs" / "resume-run" / "sandbox" / "oracle.jsonl"
    oracle.parent.mkdir(parents=True)
    run_id = "resume-run"
    args = {"note": "after crash replay"}
    key = idempotency_key(run_id, "append_ledger", args)
    oracle.write_text(
        json.dumps({"kind": "append", "note": "after crash replay",
                    "idempotency_key": key, "result": "appended#1: ORIGINAL"})
        + "\n",
        encoding="utf-8",
    )
    env = storm_env(tmp_path, run_id=run_id, idem="1")
    results = anyio.run(_run_session, env, oracle, [("append_ledger", args)])
    assert results == ["appended#1: ORIGINAL"], "resume must replay original, not append"
    assert len(oracle_lines(tmp_path, run_id)) == 1
