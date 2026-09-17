"""storm-ops: our side-effectful MCP server (BUILD-GUIDE §5.2).

Two tools:
- escalate(reason): give up on the task, hand to a human. Recorded in the oracle.
- append_ledger(note): durable side effect -- fsync'd append to oracle.jsonl.

Dedupe-aware (§9): at startup the oracle is scanned into a key->result table.
A call carrying params._meta.idempotency_key (stamped by the proxy when
STORM_IDEM=1) that matches an existing entry returns the ORIGINAL result
without appending. Unkeyed calls always append -- that is arm 1 of the
vignette: unmodified replay double-fires. This is a cooperative fix, not
transparent protection (BUILD-GUIDE §1, §17).

Run: python -m mcp_storm.servers.storm_ops --oracle <path>/oracle.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP


def _read_idempotency_key(ctx: Context) -> str:
    """The stamped key arrives on ctx.request_context.meta as a pydantic Meta
    model with extra="allow" (mcp 1.30 bundled FastMCP: .request is None inside
    tool handlers), so the key lives in model_extra -- never a plain dict."""
    try:
        meta = getattr(ctx.request_context, "meta", None)
        if meta is None:
            request = getattr(ctx.request_context, "request", None)
            params = getattr(request, "params", None) if request is not None else None
            meta = getattr(params, "_meta", None)
        if meta is None:
            return ""
        if isinstance(meta, dict):
            return str(meta.get("idempotency_key", ""))
        extra = getattr(meta, "model_extra", None) or {}
        if isinstance(extra, dict) and extra.get("idempotency_key"):
            return str(extra["idempotency_key"])
        return str(getattr(meta, "idempotency_key", "") or "")
    except Exception:  # noqa: BLE001 - _meta access must never break the append
        return ""


def build(oracle_path: Path) -> FastMCP:
    mcp = FastMCP("storm-ops")
    lock = threading.Lock()
    oracle_path = Path(oracle_path)

    dedupe: dict[str, str] = {}  # idempotency_key -> original result text
    total_appends = 0
    if oracle_path.exists():
        for line in oracle_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                total_appends += 1
                key = rec.get("idempotency_key")
                if key:
                    dedupe[key] = rec.get("result", "")

    def _append(record: dict) -> None:
        data = (json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        oracle_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(oracle_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_BINARY)
        try:
            os.write(fd, data)
            os.fsync(fd)  # server-side durability: survives any kill
        finally:
            os.close(fd)

    @mcp.tool()
    def escalate(reason: str) -> str:
        """Give up on the current task and hand it to a human operator."""
        with lock:
            _append({"ts": time.time(), "kind": "escalate", "reason": reason})
        return f"ESCALATED: {reason}"

    @mcp.tool()
    async def append_ledger(note: str, ctx: Context) -> str:  # type: ignore[empty-body]
        """Append a note to the durable ops ledger (this is a real side effect)."""
        nonlocal total_appends
        key = _read_idempotency_key(ctx)
        with lock:
            if key and key in dedupe:
                return dedupe[key]
            total_appends += 1
            result = f"appended#{total_appends}: {note[:60]}"
            _append(
                {
                    "ts": time.time(),
                    "kind": "append",
                    "note": note,
                    "idempotency_key": key,
                    "result": result,
                }
            )
            if key:
                dedupe[key] = result
        return result

    return mcp


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="storm-ops")
    parser.add_argument("--oracle", required=True, help="path to oracle.jsonl")
    args = parser.parse_args(argv)
    build(Path(args.oracle)).run()


if __name__ == "__main__":
    main()
