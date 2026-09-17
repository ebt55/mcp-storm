"""Run ledger: NDJSON tool-call records + proxy log + per-proxy manifest (§3.4).

A run has up to three proxy processes (one per server) appending to the SAME
proxy.jsonl and proxy.log. Every write is a single os.write() on a descriptor
opened with O_APPEND, which gives atomic-append semantics on Windows -- lines
never interleave mid-record. fsync happens on proxy shutdown (run end).
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
    return time.perf_counter_ns()


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
        if self.dir is not None:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._ledger_fd = os.open(
                self.dir / "proxy.jsonl",
                os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_BINARY,
            )
            self._log_fd = os.open(
                self.dir / "proxy.log",
                os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_BINARY,
            )
            self._manifest_path = self.dir / f"manifest.{cfg.server_name}.json"

    # ------------------------------------------------------------------ logging
    def log(self, message: str, tee: bool = True) -> None:
        """Append to proxy.log (a file -- never blocks). stderr is teed ONLY for
        low-volume lifecycle events: if our stderr is an undrained pipe, teeing
        per-line child logs would fill it, block this thread while it holds the
        ledger lock, and deadlock the whole pump (found day 1, encoded here)."""
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
                "frames_agent_to_child_equal_forwarded": self.frames_in == self.frames_forwarded_down,
            },
            "exit_reason": exit_reason,
            "written_at": time.time(),
        }
        with self._lock:
            self._manifest_path.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
            )

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
