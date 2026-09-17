"""Two-thread stdio pump with byte line framing (BUILD-GUIDE §3.2, §3.5).

Windows discipline baked in:
- stdout is reconfigured to utf-8 and used ONLY for JSON-RPC frames, written as
  bytes under a lock, flushed per frame; logs go to stderr + proxy.log.
- Both directions are read as BYTES and split on b"\\n"; untouched frames are
  forwarded byte-for-byte including their original terminator (CRLF preserved).
- The child is spawned with inherited environment (§3.5.5 -- replacing the env
  breaks uv/uvx spawns on Windows).

Direction handling (§3.2):
- agent -> child: register id -> (method, params) for requests; forward
  untouched, except idem stamping of tools/call params when STORM_IDEM=1.
- child -> agent: faults apply ONLY to responses whose request was tools/call.
  initialize/tools/list/pings/notifications pass untouched in both directions.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from typing import Any

from . import faults, idem
from .config import ProxyConfig
from .ledger import Ledger, now_ns


def parse_frame(payload: bytes) -> dict | None:
    """Parse one line's payload; None if not a JSON object (forwarded untouched)."""
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def dump_frame(obj: dict) -> bytes:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def split_line(raw: bytes) -> tuple[bytes, bytes]:
    """Split a readline() result into (payload, eol). eol preserves CRLF."""
    if raw.endswith(b"\r\n"):
        return raw[:-2], b"\r\n"
    if raw.endswith(b"\n"):
        return raw[:-1], b"\n"
    return raw, b""


class ProxyPump:
    def __init__(self, cfg: ProxyConfig, child: subprocess.Popen) -> None:
        self.cfg = cfg
        self.child = child
        self.ledger = Ledger(cfg)
        self._out_lock = threading.Lock()  # guards our stdout (JSON-RPC only)
        self._counter_lock = threading.Lock()
        # id -> (method, params, t_request_ns) for agent->child requests
        self._pending: dict[Any, tuple[str, dict, int]] = {}
        self._pending_lock = threading.Lock()
        self._call_seq = 0
        self._child_dead = threading.Event()
        self._outstanding_delayed = 0
        self._delayed_zero = threading.Event()
        self._delayed_zero.set()
        self._stdout = sys.stdout.buffer

    # ------------------------------------------------------------------ output
    def _write_frame(self, frame: bytes) -> None:
        """Write one message to stdout under the lock. `frame` must already carry
        its terminator: raw child bytes (byte-exact passthrough) or
        dump_frame(obj) + b"\\n" for proxy-generated envelopes."""
        with self._out_lock:
            self._stdout.write(frame)
            self._stdout.flush()

    def _bump(self, ledger_attr: str, by: int = 1) -> None:
        with self._counter_lock:
            setattr(self.ledger, ledger_attr, getattr(self.ledger, ledger_attr) + by)

    # ------------------------------------------------------------ fault engine
    def _handle_tool_response(
        self, resp: dict, payload: bytes, eol: bytes, method: str, params: dict, t_req: int
    ) -> None:
        """Called on the child->agent thread for responses to tools/call."""
        cfg = self.cfg
        with self._counter_lock:
            self._call_seq += 1
            call_seq = self._call_seq
            self.ledger.tool_calls += 1
        t_child = now_ns()
        tool = str(params.get("name", ""))

        injected = "none"
        out_payload: bytes | None = payload  # bytes to forward, or None to drop
        delay_s = 0.0
        if cfg.faulting and faults.should_fault(
            cfg.dose, faults.fault_key(cfg.run_id, cfg.task_id, call_seq, cfg.seed)
        ):
            injected = cfg.mode
            with self._counter_lock:
                self.ledger.faulted_calls += 1
            if cfg.mode == "dropped":
                out_payload = None
            elif cfg.mode == "latency":
                delay_s = cfg.latency_delay_s  # true response, forwarded late
            elif cfg.mode == "hard_error":
                out_payload = dump_frame(
                    faults.apply_hard_error(resp, cfg.server_name, call_seq, cfg.hard_error_style)
                )
            elif cfg.mode == "malformed":
                out_payload = dump_frame(faults.apply_malformed(resp, call_seq))
            elif cfg.mode == "partial":
                out_payload = dump_frame(faults.apply_partial(resp, call_seq))

        result_obj = resp.get("result") if isinstance(resp.get("result"), dict) else {}
        n_blocks = len(result_obj.get("content") or [])
        is_error = bool(result_obj.get("isError")) or "error" in resp
        if injected in ("hard_error", "malformed", "partial") and out_payload is not None:
            # meter the envelope the agent actually received, not the child's answer
            try:
                fwd = json.loads(out_payload)
                fwd_result = fwd.get("result") if isinstance(fwd.get("result"), dict) else {}
                n_blocks = len(fwd_result.get("content") or [])
                is_error = bool(fwd_result.get("isError")) or "error" in fwd
            except ValueError:
                pass
        canonical = idem.canonical_args(params.get("arguments"))

        def ledger_row(final_payload: bytes | None) -> dict:
            t_resp = now_ns()
            return {
                # required fields (§3.4)
                "run_id": cfg.run_id,
                "task_id": cfg.task_id,
                "server": cfg.server_name,
                "tool": tool,
                "args_bytes": len(canonical.encode("utf-8")),
                "result_bytes": len(final_payload) if final_payload is not None else 0,
                "n_content_blocks": n_blocks,
                "is_error": is_error,
                "fault_mode_injected": injected,
                "t_request_ns": t_req,
                "t_response_ns": t_resp if final_payload is not None else None,
                "latency_ms": (t_resp - t_req) / 1e6 if final_payload is not None else None,
                "jsonrpc_id": resp.get("id"),
                "call_seq": call_seq,
                # additive fields (join keys, retry-storm metric, diagnostics)
                "t_child_ns": t_child,
                "args_hash": idem.args_hash(tool, params.get("arguments")),
                "forwarded": final_payload is not None,
                "child_result_bytes": len(payload),
                "delay_s": delay_s,
            }

        if out_payload is None:  # dropped
            with self._counter_lock:
                self.ledger.frames_dropped += 1
            self.ledger.record_tool_call(ledger_row(None))
            self.ledger.log(
                f"dropped tools/call seq={call_seq} tool={tool} id={resp.get('id')}"
            )
            return

        terminator = eol if injected == "none" else b"\n"
        out_frame = out_payload + terminator

        if delay_s > 0:  # latency: hold the envelope, forward from a timer thread
            with self._counter_lock:
                self._outstanding_delayed += 1
                self._delayed_zero.clear()

            def _later(frame: bytes = out_frame, frame_payload: bytes = out_payload) -> None:
                try:
                    time.sleep(delay_s)
                    self._write_frame(frame)
                    self._bump("frames_forwarded_up")
                    self.ledger.record_tool_call(ledger_row(frame_payload))
                finally:
                    with self._counter_lock:
                        self._outstanding_delayed -= 1
                        if self._outstanding_delayed == 0:
                            self._delayed_zero.set()

            threading.Thread(target=_later, daemon=True, name=f"storm-latency-{call_seq}").start()
            return

        self._write_frame(out_frame)
        self._bump("frames_forwarded_up")
        self.ledger.record_tool_call(ledger_row(out_payload))

    # ------------------------------------------------------------------ threads
    def _pump_agent_to_child(self) -> None:
        stdin = sys.stdin.buffer
        try:
            for raw in iter(stdin.readline, b""):
                try:
                    self._forward_downstream(raw)
                except Exception as exc:  # noqa: BLE001 - one bad frame must not kill the pump
                    self.ledger.log(f"downstream frame error ({exc!r}); forwarding raw", tee=False)
                    try:
                        if self.child.stdin is not None:
                            self.child.stdin.write(raw)
                            self.child.stdin.flush()
                            self._bump("frames_forwarded_down")
                    except OSError:
                        pass
        except OSError:
            pass
        finally:
            if self.child.stdin is not None:
                try:
                    self.child.stdin.close()
                except OSError:
                    pass

    def _forward_downstream(self, raw: bytes) -> None:
        payload, eol = split_line(raw)
        self._bump("frames_in")
        out = raw
        msg = parse_frame(payload)
        if (
            msg is not None
            and msg.get("method") is not None
            and msg.get("id") is not None
            and msg["method"] == "tools/call"
        ):
            params = msg.get("params") or {}
            if self.cfg.idem:
                params = idem.stamp(params, self.cfg.run_id)
                msg = {**msg, "params": params}
                out = dump_frame(msg) + b"\n"
            with self._pending_lock:
                self._pending[msg["id"]] = (str(msg["method"]), params, now_ns())
        if self.child.stdin is not None:
            self.child.stdin.write(out)
            self.child.stdin.flush()
            self._bump("frames_forwarded_down")

    def _pump_child_to_agent(self) -> None:
        assert self.child.stdout is not None
        try:
            for raw in iter(self.child.stdout.readline, b""):
                payload, eol = split_line(raw)
                self._bump("frames_child")
                msg = parse_frame(payload)
                handled = False
                if msg is not None and msg.get("id") is not None and msg.get("method") is None:
                    with self._pending_lock:
                        entry = self._pending.pop(msg["id"], None)
                    if entry is not None and entry[0] == "tools/call":
                        method, params, t_req = entry
                        self._handle_tool_response(msg, payload, eol, method, params, t_req)
                        handled = True
                if not handled:
                    # byte-exact passthrough: keep the child's exact terminator
                    self._write_frame(raw if eol else payload + b"\n")
                    self._bump("frames_forwarded_up")
        except OSError:
            pass
        finally:
            self._child_dead.set()

    def _pump_child_stderr(self) -> None:
        if self.child.stderr is None:
            return
        try:
            for raw in iter(self.child.stderr.readline, b""):
                # file-only: never let child log volume block the JSON-RPC path
                self.ledger.log(
                    f"child stderr: {raw.decode('utf-8', 'replace').rstrip()}", tee=False
                )
        except OSError:
            pass

    # ------------------------------------------------------------------ driver
    def run(self) -> int:
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
        sys.stderr.reconfigure(encoding="utf-8", newline="\n")
        self.ledger.log(
            f"proxy up pid={os.getpid()} child={self.cfg.target_cmd} "
            f"{list(self.cfg.target_args)} mode={self.cfg.mode} dose={self.cfg.dose} "
            f"idem={self.cfg.idem} timeout_hint={self.cfg.client_timeout_hint_s}s"
        )
        threads = [
            threading.Thread(target=self._pump_agent_to_child, daemon=True, name="storm-down"),
            threading.Thread(target=self._pump_child_to_agent, daemon=True, name="storm-up"),
            threading.Thread(target=self._pump_child_stderr, daemon=True, name="storm-err"),
        ]
        for t in threads:
            t.start()
        self._child_dead.wait()  # server gone: nothing more to relay upward
        try:
            self.child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.child.kill()
            self.child.wait(timeout=5)
        rc = self.child.returncode or 0
        # Give in-flight latency-held envelopes their full delay (+ slack) to
        # land: losing one loses both the frame AND its ledger row.
        grace = self.cfg.latency_delay_s + 2.0
        if not self._delayed_zero.wait(timeout=grace):
            with self._counter_lock:
                self.ledger.latency_holds_lost = self._outstanding_delayed
        self.ledger.close("child-exit")
        return rc
