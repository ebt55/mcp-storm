"""Run ledger: NDJSON tool-call records + proxy log + per-proxy manifest (§3.4).

One file PER PROXY PROCESS (proxy.<server>.jsonl / proxy.<server>.log) inside
runs/<run_id>/: Windows append writes are seek-then-write with NO cross-process
atomicity (verified day 1: 3 proxies appending one shared file tore lines), so
each process owns its file exclusively and readers merge + sort by
t_request_ns. fsync happens on proxy shutdown (run end).

Timestamps are WALL CLOCK (time.time_ns) -- they exist for temporal joins
against agent-side records (§5.1 meter join, nearest timestamp +-2s). Latency
is derived from the same clock source (valid: both stamps from this process).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

from .config import ProxyConfig

REQUIRED_FIELDS = (
    "run_id",
    "task_id",
    "server",
    "tool",
    "args_bytes",
    "result_bytes",
    "n_content_blocks",
    "is_error",
    "fault_mode_injected",
    "t_request_ns",
    "t_response_ns",
    "latency_ms",
    "jsonrpc_id",
    "call_seq",
)


def now_ns() -> int:
    return time.time_ns()


def read_run_ledger(run_dir: Path) -> list[dict]:
    """Merge every per-server ledger file, sorted oldest-request first."""
    rows: list[dict] = []
    for path in sorted(Path(run_dir).glob("proxy.*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    rows.sort(key=lambda r: (r.get("t_request_ns") or 0, r.get("server", "")))
    return rows


class Ledger:
    """Always-on meter + fault record for one proxy process."""

    def __init__(self, cfg: ProxyConfig) -> None:
        self.cfg = cfg
        self._lock = threading.Lock()
        self.dir: Path | None = Path(cfg.ledger_dir) if cfg.ledger_dir else None
        self._ledger_fd: int | None = None
        self._log_fd: int | None = None
        self._manifest_path: Path | None = None
        self.frames_in = 0  # frames received from the agent
        self.frames_child = 0  # frames received from the child server
        self.frames_forwarded_up = 0  # frames sent to the agent
        self.frames_forwarded_down = 0  # frames sent to the child
        self.frames_dropped = 0
        self.tool_calls = 0
        self.faulted_calls = 0
        self.latency_holds_lost = 0  # holds still pending at teardown (rows never written)
        if self.dir is not None:
            self.dir.mkdir(parents=True, exist_ok=True)
            tag = cfg.server_name.replace("/", "_")
            self._ledger_fd = os.open(
                self.dir / f"proxy.{tag}.jsonl",
                os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_BINARY,
            )
            self._log_fd = os.open(
                self.dir / f"proxy.{tag}.log",
                os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_BINARY,
            )
            self._manifest_path = self.dir / f"manifest.{tag}.json"

    # ------------------------------------------------------------------ logging
    def log(self, message: str, tee: bool = True) -> None:
        """Append to this proxy's log file (a file -- never blocks). stderr is
        teed ONLY for low-volume lifecycle events: if our stderr is an undrained
        pipe, teeing per-line child logs would fill it, block this thread while
        it holds the ledger lock, and deadlock the whole pump (found day 1,
        encoded here)."""
        line = f"[{self.cfg.server_name} {time.strftime('%H:%M:%S')}] {message}\n"
        stamped = line.encode("utf-8", "replace")
        with self._lock:
            if self._log_fd is not None:
                os.write(self._log_fd, stamped)
        if tee:
            try:
                sys.stderr.write(line)
                sys.stderr.flush()
            except OSError:
                pass

    # -------------------------------------------------------------- tool record
    def record_tool_call(self, row: dict) -> None:
        missing = [f for f in REQUIRED_FIELDS if f not in row]
        if missing:
            raise ValueError(f"ledger row missing required fields: {missing}")
        row.setdefault("server", self.cfg.server_name)
        row.setdefault("run_id", self.cfg.run_id)
        row.setdefault("task_id", self.cfg.task_id)
        row.setdefault("mode", self.cfg.mode)
        row.setdefault("dose", self.cfg.dose)
        row.setdefault("seed", self.cfg.seed)
        row.setdefault("proxy_pid", os.getpid())
        data = (json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        if self._ledger_fd is None:
            return
        with self._lock:
            os.write(self._ledger_fd, data)

    # ----------------------------------------------------------------- manifest
    def write_startup_manifest(self, child_pid: int) -> None:
        """Written the moment the proxy spawns its child, BEFORE any traffic:
        a hard-killed proxy never reaches close(), and the runner's watchdog
        (taskkill /F /T by proxy_pid) needs the pid on disk immediately."""
        if self._manifest_path is None:
            return
        manifest = {
            "run_id": self.cfg.run_id,
            "task_id": self.cfg.task_id,
            "server": self.cfg.server_name,
            "proxy_pid": os.getpid(),
            "child_pid": child_pid,
            "started": True,
            "mode": self.cfg.mode,
            "dose": self.cfg.dose,
        }
        tmp = self._manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self._manifest_path)

    def write_manifest(self, exit_reason: str) -> None:
        if self._manifest_path is None:
            return
        manifest = {
            "run_id": self.cfg.run_id,
            "task_id": self.cfg.task_id,
            "server": self.cfg.server_name,
            "mode": self.cfg.mode,
            "dose": self.cfg.dose,
            "seed": self.cfg.seed,
            "idem": self.cfg.idem,
            "target_cmd": self.cfg.target_cmd,
            "target_args": list(self.cfg.target_args),
            "proxy_pid": os.getpid(),
            "counters": {
                "frames_agent_to_child": self.frames_in,
                "frames_child_received": self.frames_child,
                "frames_forwarded_to_agent": self.frames_forwarded_up,
                "frames_forwarded_to_child": self.frames_forwarded_down,
                "frames_dropped": self.frames_dropped,
                "tool_calls": self.tool_calls,
                "faulted_calls": self.faulted_calls,
                "latency_holds_lost_at_teardown": self.latency_holds_lost,
                "frames_agent_to_child_equal_forwarded": self.frames_in == self.frames_forwarded_down,
            },
            "exit_reason": exit_reason,
            "written_at": time.time(),
        }
        tmp = self._manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self._manifest_path)

    def close(self, exit_reason: str) -> None:
        self.write_manifest(exit_reason)
        with self._lock:
            for fd in (self._ledger_fd, self._log_fd):
                if fd is not None:
                    try:
                        os.fsync(fd)
                    except OSError:
                        pass
                    os.close(fd)
            self._ledger_fd = None
            self._log_fd = None
