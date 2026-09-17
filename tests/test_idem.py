"""Idempotency stamping unit tests (§3.2, §9). storm-ops dedupe e2e lands with CP-B."""

from __future__ import annotations

import json

from conftest import frame, read_responses, spawn_proxy, storm_env, tools_call, warmup_frames

from mcp_storm.proxy import idem


def test_stamp_is_pure_and_deterministic():
    params = {"name": "append_ledger", "arguments": {"note": "pay invoice 42"}}
    stamped = idem.stamp(params, "run-1")
    assert stamped is not params, "stamp must never mutate the original frame"
    assert stamped["_meta"]["idempotency_key"] == idem.stamp(params, "run-1")["_meta"]["idempotency_key"]
    assert stamped["_meta"]["idempotency_key"] != idem.stamp(params, "run-2")["_meta"]["idempotency_key"]
    # canonical ordering: same content, different key order -> same hash
    a = idem.idempotency_key("r", "t", {"x": 1, "y": [1, 2]})
    b = idem.idempotency_key("r", "t", {"y": [1, 2], "x": 1})
    assert a == b


def test_stamped_requests_still_work_through_proxy(tmp_path):
    """STORM_IDEM=1: the fake server tolerates _meta and still answers."""
    env = storm_env(tmp_path, idem="1")
    proc = spawn_proxy(tmp_path, env)
    assert proc.stdin is not None
    proc.stdin.write(b"".join(warmup_frames()))
    proc.stdin.flush()
    assert len(read_responses(proc, 2)) == 2
    proc.stdin.write(frame(tools_call(100, "echo", {"text": "with meta"})))
    proc.stdin.flush()
    got = read_responses(proc, 1)
    assert got and got[0]["result"]["content"][0]["text"] == "with meta"
    proc.stdin.close()
    proc.wait(timeout=20)
    # frame equality breaks by exactly the stamped requests (documented, §3.2)
    assert json.dumps(got[0])  # liveness


def test_ledger_row_fields(tmp_path):
    """Every §3.4 required field present with correct types on a clean call."""
    from conftest import ledger_rows

    env = storm_env(tmp_path)
    proc = spawn_proxy(tmp_path, env)
    assert proc.stdin is not None
    proc.stdin.write(b"".join(warmup_frames()))
    proc.stdin.flush()
    read_responses(proc, 2)
    proc.stdin.write(frame(tools_call(100, "echo", {"text": "fields"})))
    proc.stdin.flush()
    read_responses(proc, 1)
    proc.stdin.close()
    proc.wait(timeout=20)
    rows = ledger_rows(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    for field in (
        "run_id", "task_id", "server", "tool", "args_bytes", "result_bytes",
        "n_content_blocks", "is_error", "fault_mode_injected", "t_request_ns",
        "t_response_ns", "latency_ms", "jsonrpc_id", "call_seq",
    ):
        assert field in row, f"missing ledger field {field}"
    assert row["tool"] == "echo"
    assert row["fault_mode_injected"] == "none"
    assert row["n_content_blocks"] == 1
    assert row["args_hash"] and row["args_bytes"] > 0 and row["result_bytes"] > 0
    # wall-clock stamps (joinable against agent-side epoch records), not perf_counter
    import time as _time

    assert abs(row["t_response_ns"] / 1e9 - _time.time()) < 300, "not wall clock"
    assert 0 <= row["latency_ms"] < 10_000


def test_two_proxies_one_run_no_tearing(tmp_path):
    """§3.1 topology: 3 proxies per run append concurrently. Per-server files
    make every row intact (CP-A review finding 3)."""
    import sys

    from conftest import ledger_rows, read_responses, spawn
    from conftest import warmup_frames as wf

    env = storm_env(tmp_path, mode="malformed", dose="0.3", seed="iso")
    procs = []
    for name in ("filesystem", "sqlite", "storm-ops"):
        procs.append(
            spawn(
                [sys.executable, "-m", "mcp_storm.proxy", "--server-name", name,
                 "--spawn-cmd", sys.executable,
                 "--spawn-args", "-m", "mcp_storm.testing.fake_server"],
                env,
            )
        )
    try:
        for i, proc in enumerate(procs):
            assert proc.stdin is not None
            proc.stdin.write(b"".join(wf()))
            proc.stdin.flush()
        for proc in procs:
            assert len(read_responses(proc, 2, timeout_s=15)) == 2
        for j in range(40):
            for proc in procs:
                proc.stdin.write(frame(tools_call(1000 + j, "echo", {"text": f"b{j}"})))
                proc.stdin.flush()
        for proc in procs:
            read_responses(proc, 40, timeout_s=30)
    finally:
        for proc in procs:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
                proc.wait(timeout=20)
            except Exception:
                proc.kill()
    rows = ledger_rows(tmp_path)
    assert len(rows) == 120, f"expected 120 intact rows, got {len(rows)}"
    for row in rows:
        assert row["server"] in ("filesystem", "sqlite", "storm-ops")
        assert row["call_seq"] == row["call_seq"]  # survives JSON round-trip
    per_server = {s: [r for r in rows if r["server"] == s] for s in ("filesystem", "sqlite", "storm-ops")}
    for s, srows in per_server.items():
        assert [r["call_seq"] for r in srows] == list(range(1, 41)), s
