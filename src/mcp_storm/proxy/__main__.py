"""Entry: python -m mcp_storm.proxy --spawn-cmd <exe> --spawn-args <...> (§4)."""

from __future__ import annotations

import argparse
import subprocess
import sys

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
    args = parser.parse_args(argv)

    cfg = ProxyConfig.from_env()
    if args.spawn_cmd or args.spawn_args or args.server_name:
        cfg = cfg.with_overrides(
            target_cmd=args.spawn_cmd,
            target_args=args.spawn_args,
            server_name=args.server_name,
        )
    if not cfg.target_cmd:
        parser.error("no spawn target: pass --spawn-cmd/--spawn-args or STORM_TARGET_CMD")

    child = subprocess.Popen(
        [cfg.target_cmd, *cfg.target_args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    pump = ProxyPump(cfg, child)
    return pump.run()


if __name__ == "__main__":
    sys.exit(main())
