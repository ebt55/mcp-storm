"""Shared helpers for offline proxy tests: raw-byte MCP clients and proxy spawners."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def storm_env(tmp_path: Path | str, **overrides: Any) -> dict[str, str]:
    """Merged env (never replaced -- §3.5.5) with STORM_* test values."""
    env = os.environ.copy()
    run_id = str(overrides.pop("run_id", "test-run"))
    task_id = str(overrides.pop("task_id", "test-task"))
    env.update(
        {
            "STORM_RUN_ID": run_id,
            "STORM_TASK_ID": task_id,
            "STORM_LEDGER_DIR": str(Path(tmp_path) / "runs" / run_id),
        }
    )
    for key, value in overrides.items():
        env[f"STORM_{key.upper()}"] = str(value)
    return env


def spawn(cmd: list[str], env: dict[str, str]) -> subprocess.Popen:
    """stderr -> temp file (never an undrained pipe; it can fill and deadlock
    the child). Diagnostics land in the file if a human needs them."""
    err_dir = Path(env.get("STORM_LEDGER_DIR", ".")).parent
    err_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=open(err_dir / "spawn.stderr", "ab"),
        env=env,
    )


def spawn_proxy(tmp_path: Path, env: dict[str, str], server_name: str = "fake") -> subprocess.Popen:
    """Proxy wrapping the offline fake echo server."""
    return spawn(
        [
            sys.executable,
            "-m",
            "mcp_storm.proxy",
            "--server-name",
            server_name,
            "--spawn-cmd",
            sys.executable,
            "--spawn-args",
            "-m",
            "mcp_storm.testing.fake_server",
        ],
        env,
    )


def frame(obj: dict, eol: bytes = b"\n") -> bytes:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + eol


def initialize_request(id_: int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": id_,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "storm-raw-test", "version": "0.0.0"},
        },
    }


def tools_call(id_: int, name: str, arguments: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": id_,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def _line_queue(proc: subprocess.Popen):
    """One feeder thread per process, created lazily and cached on the object.
    Multiple readers must share it or they steal each other's lines. Also keeps
    every raw line on proc._storm_lines for byte-exact stream comparisons."""
    import queue
    import threading

    existing = getattr(proc, "_storm_q", None)
    if existing is not None:
        return existing
    q: queue.Queue[bytes | None] = queue.Queue()
    lines: list[bytes] = []
    proc._storm_q = q
    proc._storm_lines = lines
    assert proc.stdout is not None

    def _feed() -> None:
        try:
            for line in iter(proc.stdout.readline, b""):  # type: ignore[union-attr]
                lines.append(line)
                q.put(line)
        except OSError:
            pass
        q.put(None)

    threading.Thread(target=_feed, daemon=True).start()
    return q


def exchange_and_collect(proc: subprocess.Popen, payload: bytes, n_responses: int,
                         timeout_s: float = 60.0) -> bytes:
    """Write payload, wait for n_responses responses with stdin still OPEN (EOF
    races pipelined processing), then close stdin, drain to EOF, kill if needed,
    and return the FULL raw stdout byte stream (byte-exact comparisons)."""
    import time

    q = _line_queue(proc)
    assert proc.stdin is not None
    proc.stdin.write(payload)
    proc.stdin.flush()
    read_responses(proc, n_responses, timeout_s=timeout_s)
    proc.stdin.close()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            item = q.get(timeout=1.0)
        except Exception:  # queue.Empty
            if proc.poll() is not None:
                break
            continue
        if item is None:
            break
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
    return b"".join(proc._storm_lines)


def read_responses(proc: subprocess.Popen, count: int, timeout_s: float = 30.0) -> list[dict]:
    """Read `count` response frames (skip notifications/blank lines) from a raw
    process. Bounded by a real deadline: blocking readline can't be trusted on
    Windows pipes, so a feeder thread + queue enforces the timeout."""
    import time

    q = _line_queue(proc)
    responses: list[dict] = []
    deadline = time.monotonic() + timeout_s
    while len(responses) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            line = q.get(timeout=remaining)
        except Exception:  # queue.Empty
            break
        if line is None:  # EOF
            break
        try:
            msg = json.loads(line.strip())
        except ValueError:
            continue
        if isinstance(msg, dict) and "method" not in msg and "id" in msg:
            responses.append(msg)
    return responses


def ledger_rows(tmp_path: Path, run_id: str = "test-run") -> list[dict]:
    """All rows across per-server ledger files, oldest-request first."""
    from mcp_storm.proxy.ledger import read_run_ledger

    run_dir = Path(tmp_path) / "runs" / run_id
    if not run_dir.exists():
        return []
    return read_run_ledger(run_dir)


def manifest(tmp_path: Path, server_name: str, run_id: str = "test-run") -> dict | None:
    path = Path(tmp_path) / "runs" / run_id / f"manifest.{server_name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def warmup_frames() -> list[bytes]:
    """initialize handshake + tools/list; responses are ids 1 and 2."""
    return [
        frame(initialize_request(1)),
        frame({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        frame({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
    ]


N_WARMUP_RESPONSES = 2


@pytest.fixture()
def tmp_run(tmp_path: Path) -> Path:
    return tmp_path
