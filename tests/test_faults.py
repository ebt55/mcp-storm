"""Fault-mode unit tests + end-to-end mode tests through the proxy (§3.3)."""

from __future__ import annotations

import json
import math
import time

from conftest import (
    frame,
    ledger_rows,
    read_responses,
    spawn_proxy,
    storm_env,
    tools_call,
    warmup_frames,
)

from mcp_storm.proxy import faults


# --------------------------------------------------------------------- pure units
def test_fault_draw_is_deterministic_and_seeded():
    k1 = faults.fault_key("r1", "t1", 1)
    assert k1 == "r1:t1:1", "empty seed must reproduce the exact spec key"
    assert faults.fault_key("r1", "t1", 1, "s9") == "r1:t1:1:s9"
    assert faults.uniform_draw(k1) == faults.uniform_draw(k1)
    assert faults.uniform_draw("a:1:1") != faults.uniform_draw("a:1:2")


def test_dose_coverage_is_uniformish():
    """Deterministic keys, so this is exact: ~10% of 2000 keys draw under 0.10."""
    hits = sum(
        faults.should_fault(0.10, faults.fault_key("run", "task", i)) for i in range(1, 2001)
    )
    assert 120 <= hits <= 280, f"dose coverage out of binomial range: {hits}/2000"
    assert all(faults.should_fault(0.0, f"k{i}") is False for i in range(50))
    assert all(faults.should_fault(1.0, f"k{i}") is True for i in range(50))


def _resp_with_text(text: str, id_: int = 7) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": id_,
        "result": {"content": [{"type": "text", "text": text}]},
    }


def test_malformed_variants():
    arr = json.dumps([{"id": 1}, {"id": 2}, {"id": 3}])
    cut = arr[: math.floor(len(arr) * 0.6)]
    text = "0123456789abcdefghij"  # len 20 -> floor(12)
    # cycle must be call_seq % 3 for ALL seq, not just 1..3 (CP-A review finding 1)
    for seq in range(1, 10):
        out = faults.apply_malformed(_resp_with_text(arr), call_seq=seq)
        variant = seq % 3
        got = out["result"]["content"][0]["text"]
        if variant == 2:
            assert got == "", f"seq={seq}"
        elif variant == 1:
            assert json.loads(got) == {"id": 1}, f"seq={seq}"
        else:
            assert got == cut, f"seq={seq}"
        assert out["result"]["isError"] is False
    # non-array text with variant 1 -> fallback to 60% mid-string truncation
    out = faults.apply_malformed(_resp_with_text(text), call_seq=1)
    assert out["result"]["content"][0]["text"] == text[:12]
    # call_seq % 3 == 0 -> truncate at 60% mid-string
    out = faults.apply_malformed(_resp_with_text(text), call_seq=3)
    assert out["result"]["content"][0]["text"] == text[:12]
    # call_seq % 3 == 2 -> empty text
    out = faults.apply_malformed(_resp_with_text(text), call_seq=2)
    assert out["result"]["content"][0]["text"] == ""


def test_transforms_on_jsonrpc_error_envelope():
    """A child tools/call ERROR envelope must never become error+result both
    (CP-A review finding 2): malformed/partial fold it into valid content form."""
    err_resp = {"jsonrpc": "2.0", "id": 9, "error": {"code": -32602, "message": "Unknown tool: nope"}}
    for transform in (faults.apply_malformed, faults.apply_partial):
        for seq in (1, 2, 3, 4):
            out = transform(err_resp, seq)
            assert "error" not in out and "result" in out, f"{transform.__name__} seq={seq}"
            assert out["result"].get("isError") is not True
            assert isinstance(out["result"]["content"][0]["text"], str)


def test_partial_variants():
    arr = json.dumps([{"id": i} for i in range(7)])  # ceil(7/2)=4 kept
    out = faults.apply_partial(_resp_with_text(arr), call_seq=1)
    assert len(json.loads(out["result"]["content"][0]["text"])) == 4
    # single text: last word boundary <= 60%
    text = "alpha beta gamma delta epsilon zeta eta theta"
    out = faults.apply_partial(_resp_with_text(text), call_seq=1)
    got = out["result"]["content"][0]["text"]
    assert got.endswith("delta")
    assert len(got) <= int(len(text) * 0.6) + 1
    assert "isError" not in out["result"], "partial must carry no error signal"
    # true value must not leak through sibling blocks or structuredContent
    leaky = {
        "jsonrpc": "2.0",
        "id": 3,
        "result": {
            "content": [
                {"type": "text", "text": arr},
                {"type": "text", "text": "sibling truth"},
            ],
            "structuredContent": {"secret": True},
        },
    }
    for transform in (faults.apply_malformed, faults.apply_partial):
        out = transform(leaky, call_seq=1)
        assert len(out["result"]["content"]) == 1
        assert "secret" not in json.dumps(out)
        assert "sibling truth" not in json.dumps(out)


def test_hard_error_messages():
    msg = faults.hard_error_message("sqlite", 3)  # call_seq % len: index 0 -> locked
    assert "locked" in msg
    out = faults.apply_hard_error({"jsonrpc": "2.0", "id": 5, "result": {}}, "filesystem", 2, "iserror")
    assert out["result"]["isError"] is True
    assert out["result"]["content"][0]["type"] == "text"
    out = faults.apply_hard_error({"jsonrpc": "2.0", "id": 5, "result": {}}, "filesystem", 2, "jsonrpc")
    assert out["error"]["code"] == -32603
    assert "id" in out and "result" not in out


# ------------------------------------------------------------- through the proxy
def _run_mode(tmp_path, mode: str, calls: list[tuple[str, dict]], dose: str = "1.0", **env_over):
    env = storm_env(tmp_path, mode=mode, dose=dose, **env_over)
    proc = spawn_proxy(tmp_path, env, server_name="filesystem")
    assert proc.stdin is not None
    proc.stdin.write(b"".join(warmup_frames()))
    proc.stdin.flush()
    warm = read_responses(proc, 2)
    assert len(warm) == 2, f"handshake failed: {warm}"
    for i, (tool, args) in enumerate(calls):
        proc.stdin.write(frame(tools_call(100 + i, tool, args)))
    proc.stdin.flush()
    responses = read_responses(proc, len(calls))
    proc.stdin.close()
    proc.wait(timeout=20)
    return responses


def test_hard_error_end_to_end(tmp_path):
    res = _run_mode(
        tmp_path,
        "hard_error",
        [("echo", {"text": "hi"}), ("echo", {"text": "hi"}), ("echo", {"text": "hi"})],
    )
    texts = [r["result"]["content"][0]["text"] for r in res]
    assert all(r["result"].get("isError") is True for r in res)
    from mcp_storm.proxy.faults import HARD_ERROR_MESSAGES

    expected = [HARD_ERROR_MESSAGES["filesystem"][i % 3] for i in (1, 2, 3)]
    assert texts == expected
    rows = ledger_rows(tmp_path)
    assert all(r["fault_mode_injected"] == "hard_error" for r in rows)
    assert all(r["is_error"] is True for r in rows)


def test_hard_error_jsonrpc_variant(tmp_path):
    res = _run_mode(
        tmp_path, "hard_error", [("echo", {"text": "x"})], hard_error_style="jsonrpc"
    )
    assert res[0].get("error", {}).get("code") == -32603


def test_malformed_end_to_end(tmp_path):
    arr = [{"id": 1, "n": "a"}, {"id": 2, "n": "b"}]
    res = _run_mode(
        tmp_path,
        "malformed",
        [
            ("echo_array", {"items": arr}),  # call_seq 1 -> array collapsed to first object
            ("echo", {"text": "0123456789abcdefghij"}),  # call_seq 2 -> empty text
            ("echo", {"text": "0123456789abcdefghij"}),  # call_seq 3 -> 60% cut
        ],
    )
    assert json.loads(res[0]["result"]["content"][0]["text"]) == {"id": 1, "n": "a"}
    assert res[1]["result"]["content"][0]["text"] == ""
    assert res[2]["result"]["content"][0]["text"] == "0123456789ab"
    assert all(r["result"].get("isError") is False for r in res)
    assert all("structuredContent" not in r["result"] for r in res)


def test_partial_end_to_end(tmp_path):
    arr = [{"id": i} for i in range(6)]
    res = _run_mode(tmp_path, "partial", [("echo_array", {"items": arr})])
    kept = json.loads(res[0]["result"]["content"][0]["text"])
    assert kept == [{"id": 0}, {"id": 1}, {"id": 2}]
    assert "isError" not in res[0]["result"] or res[0]["result"]["isError"] is not True
    assert "structuredContent" not in res[0]["result"]


def test_latency_mode_delays_true_response(tmp_path):
    env = storm_env(tmp_path, mode="latency", dose="1.0", latency_delay_s="0.4")
    proc = spawn_proxy(tmp_path, env)
    assert proc.stdin is not None
    proc.stdin.write(b"".join(warmup_frames()))
    proc.stdin.flush()
    assert len(read_responses(proc, 2)) == 2
    t0 = time.monotonic()
    proc.stdin.write(frame(tools_call(100, "echo", {"text": "late"})))
    proc.stdin.write(frame(tools_call(101, "echo", {"text": "late2"})))
    proc.stdin.flush()
    got = read_responses(proc, 2)
    dt = time.monotonic() - t0
    assert dt >= 0.35, f"latency fault not applied: {dt:.3f}s"
    # two concurrent holds may race; order is not guaranteed, presence is
    assert sorted(g["result"]["content"][0]["text"] for g in got) == ["late", "late2"]
    # pump must not serialize the two delays (concurrent holds)
    assert dt < 0.75, f"delayed forwards appear serialized: {dt:.3f}s"
    proc.stdin.close()
    proc.wait(timeout=20)
    rows = ledger_rows(tmp_path)
    assert all(r["fault_mode_injected"] == "latency" and r["delay_s"] == 0.4 for r in rows)
    assert all(r["latency_ms"] is not None and r["latency_ms"] >= 350 for r in rows)


def test_dropped_silences_only_tool_calls(tmp_path):
    env = storm_env(tmp_path, mode="dropped", dose="1.0")
    proc = spawn_proxy(tmp_path, env)
    assert proc.stdin is not None
    proc.stdin.write(b"".join(warmup_frames()))
    proc.stdin.flush()
    assert len(read_responses(proc, 2)) == 2, "non-tools/call traffic must pass"
    proc.stdin.write(frame(tools_call(100, "echo", {"text": "gone"})))
    proc.stdin.flush()
    proc.stdin.write(frame({"jsonrpc": "2.0", "id": 101, "method": "ping"}))
    proc.stdin.flush()
    # only the ping comes back
    got = read_responses(proc, 1, timeout_s=5)
    assert len(got) == 1 and got[0]["id"] == 101
    proc.stdin.close()
    proc.wait(timeout=20)
    rows = ledger_rows(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["forwarded"] is False
    assert row["fault_mode_injected"] == "dropped"
    assert row["t_response_ns"] is None and row["latency_ms"] is None
    assert row["child_result_bytes"] > 0, "child did answer; proxy chose not to forward"


def test_dose_zero_is_pure_meter(tmp_path):
    res = _run_mode(tmp_path, "malformed", [("echo", {"text": "clean"})], dose="0.0")
    assert res[0]["result"]["content"][0]["text"] == "clean"
    rows = ledger_rows(tmp_path)
    assert rows and rows[0]["fault_mode_injected"] == "none"
    assert rows[0]["forwarded"] is True


def test_deterministic_draws_reproducible_across_processes(tmp_path):
    """Same (run,task,seq) -> same fault decision in a fresh process."""
    env = storm_env(tmp_path, mode="dropped", dose="0.5", seed="alpha")
    run_ids = []
    for _ in range(2):
        proc = spawn_proxy(tmp_path, env)
        assert proc.stdin is not None
        proc.stdin.write(b"".join(warmup_frames()))
        proc.stdin.flush()
        read_responses(proc, 2)
        for i in range(6):
            proc.stdin.write(frame(tools_call(200 + i, "echo", {"text": f"m{i}"})))
        proc.stdin.flush()
        got = read_responses(proc, 6, timeout_s=5)
        run_ids.append(sorted(g["id"] for g in got))
        proc.stdin.close()
        proc.wait(timeout=20)
    assert run_ids[0] == run_ids[1], "fault draws differ across processes with identical keys"
