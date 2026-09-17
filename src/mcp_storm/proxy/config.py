"""Proxy configuration from STORM_* environment (BUILD-GUIDE §4).

The agent-side MCP client config spawns:
    uv run --directory <repo> python -m mcp_storm.proxy \
        --spawn-cmd uvx --spawn-args mcp-server-filesystem@<pin> <sandbox>
Env carries run identity (run_id/task_id/mode/dose/seed/ledger dir). CLI flags
override env for the spawn target only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

MODES = ("passthrough", "latency", "hard_error", "malformed", "partial", "dropped")

# BUILD-GUIDE §3.3: 30s < 45s client timeout, cleanly separating latency from dropped.
DEFAULT_LATENCY_DELAY_S = 30.0
DEFAULT_CLIENT_TIMEOUT_HINT_S = 45.0


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class ProxyConfig:
    target_cmd: str
    target_args: tuple[str, ...] = ()
    mode: str = "passthrough"
    dose: float = 0.0
    seed: str = ""
    run_id: str = "adhoc"
    task_id: str = "adhoc"
    server_name: str = "server"
    ledger_dir: Path | None = None
    idem: bool = False
    client_timeout_hint_s: float = DEFAULT_CLIENT_TIMEOUT_HINT_S
    hard_error_style: str = "iserror"  # "iserror" (default) | "jsonrpc" (-32603), §3.3
    latency_delay_s: float = DEFAULT_LATENCY_DELAY_S

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            msg = f"STORM_MODE must be one of {MODES}, got {self.mode!r}"
            raise ValueError(msg)
        if not 0.0 <= self.dose <= 1.0:
            msg = f"STORM_DOSE must be in [0,1], got {self.dose}"
            raise ValueError(msg)
        if self.hard_error_style not in ("iserror", "jsonrpc"):
            msg = "STORM_HARD_ERROR_STYLE must be 'iserror' or 'jsonrpc'"
            raise ValueError(msg)
        # ':' would collide keys in sha256(f"{run_id}:{task_id}:{call_seq}")
        for name in ("run_id", "task_id", "seed"):
            if ":" in getattr(self, name):
                raise ValueError(f"STORM_{name.upper()} must not contain ':' (fault-key collisions)")

    @property
    def faulting(self) -> bool:
        """Dose 0 means the proxy is a pure passthrough meter (mcp-meter rider)."""
        return self.mode != "passthrough" and self.dose > 0.0

    @staticmethod
    def from_env() -> ProxyConfig:
        ledger = _env("STORM_LEDGER_DIR")
        return ProxyConfig(
            target_cmd=_env("STORM_TARGET_CMD", "python"),
            target_args=tuple(_env("STORM_TARGET_ARGS").split()) if _env("STORM_TARGET_ARGS") else (),
            mode=_env("STORM_MODE", "passthrough"),
            dose=_env_float("STORM_DOSE", 0.0),
            seed=_env("STORM_SEED"),
            run_id=_env("STORM_RUN_ID", "adhoc"),
            task_id=_env("STORM_TASK_ID", "adhoc"),
            server_name=_env("STORM_SERVER_NAME", "server"),
            ledger_dir=Path(ledger) if ledger else None,
            idem=_env("STORM_IDEM", "0") == "1",
            client_timeout_hint_s=_env_float("STORM_CLIENT_TIMEOUT_HINT_S", DEFAULT_CLIENT_TIMEOUT_HINT_S),
            hard_error_style=_env("STORM_HARD_ERROR_STYLE", "iserror"),
            latency_delay_s=_env_float("STORM_LATENCY_DELAY_S", DEFAULT_LATENCY_DELAY_S),
        )

    def with_overrides(
        self,
        target_cmd: str | None = None,
        target_args: list[str] | None = None,
        server_name: str | None = None,
    ) -> ProxyConfig:
        return ProxyConfig(
            target_cmd=target_cmd or self.target_cmd,
            target_args=tuple(target_args) if target_args is not None else self.target_args,
            mode=self.mode,
            dose=self.dose,
            seed=self.seed,
            run_id=self.run_id,
            task_id=self.task_id,
            server_name=server_name or self.server_name,
            ledger_dir=self.ledger_dir,
            idem=self.idem,
            client_timeout_hint_s=self.client_timeout_hint_s,
            hard_error_style=self.hard_error_style,
            latency_delay_s=self.latency_delay_s,
        )
