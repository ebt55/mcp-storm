"""Tiny .env loader (no dependency): real environment always wins."""

from __future__ import annotations

import os
from pathlib import Path

from mcp_storm.paths import REPO_ROOT


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    """Load KEY=VALUE lines from .env into os.environ (existing env wins).
    Returns the keys that were injected."""
    env_path = Path(path) if path else REPO_ROOT / ".env"
    if not env_path.exists():
        return {}
    injected: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            injected[key] = value
    return injected
