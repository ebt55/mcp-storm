"""Byte-exact framing goldens + soak (BUILD-GUIDE §3.5, §14 Sat Oct 10 KC).

Law: at dose 0 the proxy is invisible. Untouched frames forwarded byte-for-byte,
CRLF preserved, 1,000-message soak with zero lost frames.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import timedelta

from conftest import (
    frame,
    initialize_request,
    ledger_rows,
    manifest,
    spawn,
    spawn_proxy,
    storm_env,
    tools_call,
    warmup_frames,
)

UNICODE = "héllo wörld — ✓ 日本語 🌩"
LONG = "x" * 5000


def full_exchange(requests: bytes, cmd: list[str], env, n_responses: int = 9) -> bytes:
    from conftest import exchange_and_collect

    proc = spawn(cmd, env)
    return exchange_and_collect(proc, requests, n_responses)


def test_byte_exact_passthrough_golden(tmp_path):
    """Direct server output == proxied server output, byte for byte."""
    requests = b"".join(
        [
            frame(initialize_request(1)),
            frame({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            frame({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            frame(tools_call(3, "echo", {"text": "hello"})),
            frame(tools_call(4, "echo", {"text": ""})),
            frame(tools_call(5, "echo", {"text": UNICODE})),
            frame(tools_call(6, "echo_json", {"payload": {"k": ["a", 1, None, True]}})),
            frame(tools_call(7, "echo", {"text": LONG})),
            frame({"jsonrpc": "2.0", "id": 8, "method": "ping"}),
            frame(tools_call(9, "echo_json", {"payload": {"nested": {"deep": [1, 2, 3]}}})),
        ]
    )
    direct = full_exchange(
        requests, [sys.executable, "-m", "mcp_storm.testing.fake_server"], storm_env(tmp_path)
    )
    proxied = full_exchange(requests, *proxy_argv(tmp_path), n_responses=9)
    assert proxied == direct, "proxy mutated the byte stream at dose 0"


def proxy_argv(tmp_path):
    env = storm_env(tmp_path)  # dose 0 -> passthrough meter
    argv = [
        sys.executable,
        "-m",
        "mcp_storm.proxy",
        "--server-name",
        "fake",
        "--spawn-cmd",
        sys.executable,
        "--spawn-args",
        "-m",
        "mcp_storm.testing.fake_server",
    ]
    return argv, env


def test_byte_exact_with_crlf_input(tmp_path):
    """CRLF-terminated frames pass through untouched (Windows discipline)."""
    requests = (
        frame(initialize_request(1), eol=b"\r\n")
        + frame({"jsonrpc": "2.0", "method": "notifications/initialized"}, eol=b"\r\n")
        + frame(tools_call(2, "echo", {"text": "crlf"}), eol=b"\r\n")
    )
    direct = full_exchange(
        requests, [sys.executable, "-m", "mcp_storm.testing.fake_server"], storm_env(tmp_path)
    )
    proxied = full_exchange(requests, *proxy_argv(tmp_path), n_responses=2)
    assert proxied == direct


def test_1000_message_soak_zero_lost(tmp_path):
    """§14 Sat Oct 10 kill-check: 1,000 messages through the proxy, zero lost."""
    from conftest import read_responses

    n = 1000
    parts = warmup_frames()
    expected_ids = {1, 2}
    for i in range(n):
        parts.append(frame(tools_call(100 + i, "echo", {"text": f"msg-{i}"})))
        expected_ids.add(100 + i)
        if i == 500:  # non-tool traffic interleaved
            parts.append(frame({"jsonrpc": "2.0", "id": 9001, "method": "ping"}))
            expected_ids.add(9001)
    proc = spawn_proxy(tmp_path, storm_env(tmp_path))
    try:
        payload = b"".join(parts)
        assert proc.stdin is not None
        proc.stdin.write(payload)
        proc.stdin.flush()
        # keep stdin OPEN while reading: closing it first races the server's
        # processing of 1000 pipelined frames (it exits on stdin EOF)
        got_list = read_responses(proc, len(expected_ids), timeout_s=90.0)
        got = {m["id"]: m for m in got_list}
        proc.stdin.close()
        proc.wait(timeout=30)
        missing = expected_ids - set(got)
        assert not missing, f"lost frames: {sorted(missing)[:10]}"
        for i in range(n):
            text = got[100 + i]["result"]["content"][0]["text"]
            assert text == f"msg-{i}"
        rows = ledger_rows(tmp_path)
        assert len(rows) == n
        assert [r["call_seq"] for r in rows] == list(range(1, n + 1))
        man = manifest(tmp_path, "fake")
        assert man is not None
        assert man["counters"]["frames_agent_to_child"] == man["counters"]["frames_forwarded_to_child"]
        assert man["counters"]["frames_child_received"] == man["counters"]["frames_forwarded_to_agent"]
        assert man["counters"]["frames_dropped"] == 0
    finally:
        if proc.poll() is None:
            proc.kill()


def test_proxy_passes_sdk_client_roundtrip(tmp_path):
    """The real mcp SDK client works through the proxy (initialize/tools/echo).."""

    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def scenario() -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_storm.proxy", "--server-name", "fake",
                  "--spawn-cmd", sys.executable,
                  "--spawn-args", "-m", "mcp_storm.testing.fake_server"],
            env=storm_env(tmp_path),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as session:
                init = await session.initialize()
                assert init.serverInfo.name == "storm-fake-echo"
                tools = await session.list_tools()
                names = {t.name for t in tools.tools}
                assert {"echo", "echo_json", "sleep_echo", "hang"} <= names
                res = await session.call_tool("echo", {"text": "via sdk"})
                assert not res.isError
                assert res.content[0].text == "via sdk"

    anyio.run(scenario)
