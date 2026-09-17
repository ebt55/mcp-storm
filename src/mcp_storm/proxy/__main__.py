"""Entry: python -m mcp_storm.proxy --spawn-cmd <exe> --spawn-args <...> (§4)."""

from __future__ import annotations

import argparse
import os
import subprocess

from .config import ProxyConfig
from .pump import ProxyPump


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m mcp_storm.proxy",
        description="stdio JSON-RPC fault-injection proxy + meter for one MCP server",
    )
    parser.add_argument("--spawn-cmd", help="executable of the real MCP server child")
    # REMAINDER, not "+": child argv may itself start with "-" (e.g. "-m").
    # --spawn-args must therefore be the LAST flag on the command line.
    parser.add_argument("--spawn-args", nargs=argparse.REMAINDER, help="argv of the real MCP server child")
    parser.add_argument("--server-name", help="logical server name for the ledger")
    parser.add_argument("--spawn-cwd", help="working directory for the child server")
    args = parser.parse_args(argv)

    cfg = ProxyConfig.from_env()
    if any((args.spawn_cmd, args.spawn_args, args.server_name, args.spawn_cwd)):
        cfg = cfg.with_overrides(
            target_cmd=args.spawn_cmd,
            target_args=args.spawn_args,
            server_name=args.server_name,
            spawn_cwd=args.spawn_cwd,
        )
    if not cfg.target_cmd:
        parser.error("no spawn target: pass --spawn-cmd/--spawn-args or STORM_TARGET_CMD")

    child = subprocess.Popen(
        [cfg.target_cmd, *cfg.target_args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cfg.spawn_cwd or None,
    )
    pump = ProxyPump(cfg, child)
    rc = pump.run()
    # Skip interpreter finalization: daemon pump threads still hold the stdin
    # buffered-reader lock and would fatal-error at shutdown. All frames are
    # flushed per write and the ledger is fsynced in Ledger.close().
    os._exit(rc)


if __name__ == "__main__":
    main()
