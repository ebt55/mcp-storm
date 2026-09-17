"""Frozen statistics (BUILD-GUIDE §6.4). Pure stdlib; gold-tested on synthetic
curves. Everything the README reports flows through here.

- Wilson 95% CI everywhere (completion tables, headline).
- Breakpoint estimator (preregistered): crossing dose = smallest dose whose
  Wilson UPPER bound < 50%.
- Pooled one-sided Cochran-Armitage trend test on dose per (mode x model).
- Retry storms: >=3 consecutive identical (tool, args-hash) calls per run.
"""

from __future__ import annotations

import math
from typing import Any

Z95 = 1.959963984540054


def wilson_ci(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def crossing_dose(curve: dict[float, tuple[int, int]]) -> float | None:
    """curve: dose -> (completed, n). Smallest dose whose Wilson UB < 0.5.
    Doses must be the preregistered grid; returns None if it never crosses."""
    for dose in sorted(curve):
        completed, n = curve[dose]
        _, upper = wilson_ci(completed, n)
        if upper < 0.5:
            return dose
    return None


def cochran_armitage_one_sided(groups: list[tuple[float, int, int]]) -> float:
    """groups: (dose_score, n_trials, events). One-sided p for an INCREASING
    trend in failure probability with dose (completion decreasing => events =
  failures). Standard CA trend statistic, normal approximation."""
    n_total = sum(n for _, n, _ in groups)
    r_total = sum(a for _, _, a in groups)
    if n_total == 0 or r_total in (0, n_total):
        return 1.0
    p_bar = r_total / n_total
    sum_nx = sum(n * x for x, n, _ in groups)
    sum_nx2 = sum(n * x * x for x, n, _ in groups)
    x_bar = sum_nx / n_total
    var = p_bar * (1 - p_bar) * (sum_nx2 - sum_nx * sum_nx / n_total)
    if var <= 0:
        return 1.0
    t_stat = sum(x * (a - n * p_bar) for x, n, a in groups)
    z = t_stat / math.sqrt(var)
    return 0.5 * (1 - math.erf(z / math.sqrt(2)))  # P(Z >= z), one-sided


def p95(values: list[float]) -> float:
    """Nearest-rank P95 (deterministic, no interpolation)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(0.95 * len(ordered)))
    return ordered[rank - 1]


def retry_metrics(ledger_rows: list[dict]) -> dict[str, Any]:
    """Per-run retry metrics from proxy ledger rows (sorted by t_request_ns).
    Storm = >=3 consecutive identical (tool, args_hash) calls; wasted = any
    consecutive identical repeat beyond the first."""
    rows = sorted(ledger_rows, key=lambda r: r.get("t_request_ns") or 0)
    storms = 0
    wasted = 0
    run_length = 1
    for prev, curr in zip(rows, rows[1:]):
        if (prev.get("tool"), prev.get("args_hash")) == (curr.get("tool"), curr.get("args_hash")):
            run_length += 1
            wasted += 1
            if run_length == 3:
                storms += 1
        else:
            run_length = 1
    return {"tool_calls": len(rows), "wasted_calls": wasted,
            "retry_storms": storms, "storm_run": storms > 0}


def cost_multiplier(dose_tokens: float, clean_tokens: float) -> float | None:
    if not clean_tokens:
        return None
    return dose_tokens / clean_tokens
