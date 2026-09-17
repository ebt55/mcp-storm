"""List prices + spend breaker (BUILD-GUIDE §2.3, §6.2).

Prices are LIST prices per 1M tokens with a fetch date; the manifest records
the prices hash so every committed artifact is auditable against them. Live
sum = usage x prices; hard abort at $40, warning at $25, every event appended
to runs/spend.jsonl.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

# Source: BUILD-GUIDE §6.2 assumption table (owner's plan, 17 Sep 2026).
# Verify against the OpenAI pricing page at the model-smoke step (paid, after
# the prereg lock); corrections go to prereg/DEVIATIONS.md, not here.
FETCHED = "2026-09-17"
PRICES: dict[str, dict[str, float]] = {
    # usd per 1M tokens; cached_input is the cached-prompt rate when published
    "openai:gpt-5-nano": {"input": 0.06, "cached_input": 0.015, "output": 0.48},
    "openai:gpt-5-mini": {"input": 0.25, "cached_input": 0.0625, "output": 2.00},
}

HARD_BREAK_USD = 40.0
WARN_USD = 25.0


class BreakerTrip(RuntimeError):
    """Raised when live spend crosses the hard breaker: stop launching runs."""


def prices_hash() -> str:
    material = json.dumps({"FETCHED": FETCHED, "PRICES": PRICES}, sort_keys=True)
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def cost_of(model: str, usage: dict) -> float:
    """usage = usage_metadata dict (input_tokens, output_tokens, and optional
    input_token_details.cached_tokens). Gross (uncached) pricing by default --
    cached savings are reported separately, never assumed."""
    rates = PRICES[model]
    inp = usage.get("input_tokens") or 0
    out = usage.get("output_tokens") or 0
    cached = ((usage.get("input_token_details") or {}).get("cached_tokens")) or 0
    gross = inp / 1e6 * rates["input"] + out / 1e6 * rates["output"]
    # gross-only accounting: cached discount subtracted only when we bill with
    # it explicitly (runner passes bill_cached=True after the pilot decides)
    del cached
    return gross


@dataclass
class SpendTracker:
    runs_dir: Path

    @property
    def _path(self) -> Path:
        return Path(self.runs_dir) / "spend.jsonl"

    def record(self, event: dict) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n"
        fd = os.open(self._path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_BINARY)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)

    def total(self) -> float:
        if not self._path.exists():
            return 0.0
        total = 0.0
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("kind") == "run_cost":
                total += float(event.get("cost_usd", 0.0))
        return total

    def gate(self, run_id: str) -> None:
        """Call before launching each run. Warn at $25; hard-abort at $40."""
        total = self.total()
        if total >= HARD_BREAK_USD:
            self.record({"kind": "ABORT", "run_id": run_id, "total_usd": round(total, 4),
                         "breaker": HARD_BREAK_USD, "ts": __import__("time").time()})
            raise BreakerTrip(f"spend breaker tripped at ${total:.2f} (cap ${HARD_BREAK_USD})")
        if total >= WARN_USD and not getattr(self, "_warned", False):
            self.record({"kind": "WARN", "run_id": run_id, "total_usd": round(total, 4),
                         "threshold": WARN_USD, "ts": __import__("time").time()})
            self._warned = True
