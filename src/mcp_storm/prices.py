"""List prices + spend breaker (BUILD-GUIDE §2.3, §6.2).

Prices are LIST prices per 1M tokens with a fetch date; the manifest records
the prices hash so every committed artifact is auditable against them. Live
sum = usage x prices; hard abort at $40, warning at $25, every event appended
to runs/spend.jsonl.

Fetch 2026-09-18: OpenAI from developers.openai.com/api/docs/pricing (the
guide's §6.2 nano numbers $0.06/$0.48 were stale -- actual list is
$0.05/$0.40; DEVIATIONS #23); OpenRouter from openrouter.ai model pages
(exploratory arms). Data-sharing/free-limit credits are NOT netted here:
spend.jsonl records gross list cost as the conservative ceiling.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

FETCHED = "2026-09-18"
PRICES: dict[str, dict[str, float]] = {
    # usd per 1M tokens; cached_input is the cached-prompt rate when published
    "openai:gpt-5-nano": {"input": 0.05, "cached_input": 0.005, "output": 0.40},
    "openai:gpt-5-mini": {"input": 0.25, "cached_input": 0.025, "output": 2.00},
    # exploratory OpenRouter arms (owner-approved value-flash picks)
    "or:deepseek-v4-flash": {"input": 0.05, "cached_input": 0.01, "output": 0.10},
    "or:glm-5.3-flash": {"input": 0.075, "cached_input": 0.014, "output": 0.25},
}

HARD_BREAK_USD = 40.0
WARN_USD = 25.0

# Budget regime (owner directive 2026-09-24: free-credit plan, DEVIATIONS #33):
# models whose usage is covered by provider credits (OpenAI data sharing) cost
# no real money; OpenRouter arms are closed. The REAL-MONEY breaker is a
# separate, much tighter cap computed from index records.
CREDITED_MODEL_PREFIXES = ("openai:",)
REAL_HARD_BREAK_USD = 1.0
REAL_WARN_USD = 0.50


def split_gross(index_records: list[dict]) -> tuple[float, float]:
    """(real_money_usd, credited_gross_usd) from index run records."""
    real = credited = 0.0
    for r in index_records:
        cost = float(r.get("cost_usd", 0.0))
        model = str(r.get("model", ""))
        if model.startswith(CREDITED_MODEL_PREFIXES):
            credited += cost
        else:
            real += cost
    return real, credited


class BreakerTrip(RuntimeError):
    """Raised when live spend crosses the hard breaker: stop launching runs."""


def prices_hash() -> str:
    material = json.dumps({"FETCHED": FETCHED, "PRICES": PRICES}, sort_keys=True)
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def cost_of(model: str, usage: dict) -> float:
    """usage = usage_metadata dict (input_tokens, output_tokens, and optional
    input_token_details.cached_tokens). Gross (uncached) list pricing -- cached
    savings and any provider credits are reported separately, never assumed."""
    rates = PRICES[model]
    inp = usage.get("input_tokens") or 0
    out = usage.get("output_tokens") or 0
    return inp / 1e6 * rates["input"] + out / 1e6 * rates["output"]


@dataclass
class SpendTracker:
    runs_dir: Path

    @property
    def _path(self) -> Path:
        return Path(self.runs_dir) / "spend.jsonl"

    def record(self, event: dict) -> None:
        import time as _time

        event = {"ts": _time.time(), **event}
        Path(self.runs_dir).mkdir(parents=True, exist_ok=True)
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
                         "breaker": HARD_BREAK_USD})
            raise BreakerTrip(f"spend breaker tripped at ${total:.2f} (cap ${HARD_BREAK_USD})")
        if total >= WARN_USD and not getattr(self, "_warned", False):
            self.record({"kind": "WARN", "run_id": run_id, "total_usd": round(total, 4),
                         "threshold": WARN_USD})
            self._warned = True
