"""Prereg claim scoring (BUILD-GUIDE §7). committed BEFORE launch; gold-tested
on synthetic curves. Verdicts: confirmed / partial / refuted.

Band semantics (predictions.json strings):
  "never"            estimator never fires through the top dose
  "never-below-70%"  estimator fires only at the 70% dose or never
  "70%" / "40%"      estimator fires exactly at that dose
  "10-20%" etc.      estimator fires inside the closed range

Power rule (§7): at n=50 per cell, a below-50% crossing is confirmable only
when observed completion <= 36/50; a crossing whose cell estimate is not
sign-stable (Wilson interval straddles 0.5) scores 'partial' by design.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp_storm.analysis.stats import crossing_dose, wilson_ci
from mcp_storm.paths import PREREG_DIR

DOSE_GRID = (0.05, 0.10, 0.20, 0.40, 0.70)
VERDICTS = ("confirmed", "partial", "refuted")


def load_predictions() -> dict[str, Any]:
    return json.loads((PREREG_DIR / "predictions.json").read_text(encoding="utf-8"))


def _band_contains(band: str, value: float | None) -> str:
    """Score an observed estimator value against a predicted band."""
    if band == "never":
        return "confirmed" if value is None else "refuted"
    if band == "never-below-70%":
        if value is None or value >= 0.70:
            return "confirmed"
        return "refuted" if value <= 0.40 else "partial"
    def grid_index(dose_pct: float) -> int | None:
        for i, grid in enumerate(DOSE_GRID):
            if abs(grid * 100 - dose_pct) < 1e-9:
                return i
        return None

    if value is None:
        return "refuted"
    if band.endswith("%") and "-" not in band:
        target = float(band[:-1])
        if abs(value * 100 - target) < 1e-9:
            return "confirmed"
        v_i, t_i = grid_index(value * 100), grid_index(target)
        return "partial" if None not in (v_i, t_i) and abs(v_i - t_i) == 1 else "refuted"
    if "-" in band:
        lo, hi = (float(x) for x in band[:-1].split("-"))
        if lo <= value * 100 <= hi:
            return "confirmed"
        v_i = grid_index(value * 100)
        if v_i is None:
            return "refuted"
        for g in DOSE_GRID:
            if lo <= g * 100 <= hi and abs(DOSE_GRID.index(g) - v_i) <= 1:
                return "partial"
        return "refuted"
    return "partial"


def score_c1_cost_multiplier(mults: dict[str, dict[float, float | None]]) -> dict:
    """mults: mode -> dose -> cost multiplier (tokens-at-dose / clean).
    C1: smallest dose with multiplier > 3, per mode, vs the predicted band."""
    claims = load_predictions()["claims"]["C1_cost_multiplier_gt_3x"]
    out: dict[str, dict[str, str]] = {}
    for mode, band in claims.items():
        for model, predicted in band.items():
            if model not in ("gpt-5-nano", "gpt-5-mini") and model != "both_models":
                continue
            curve = mults.get(mode, {})
            fired = next((d for d in sorted(curve) if (curve[d] or 0) > 3), None)
            out.setdefault(mode, {})[model] = {
                "predicted": predicted, "observed_first_dose": fired,
                "verdict": _band_contains(predicted, fired),
            }
    return out


def score_c2_completion(curves: dict[str, dict[str, dict[float, tuple[int, int]]]],
                        ) -> dict:
    """curves: mode -> model -> dose -> (completed, n). C2 crossing estimator."""
    claims = load_predictions()["claims"]["C2_completion_below_50_wilson_ub"]
    out: dict[str, dict[str, dict[str, str]]] = {}
    for mode, band in claims.items():
        for model, predicted in band.items():
            curve = curves.get(mode, {}).get(model, {})
            if not any(n for _, n in curve.values()):
                verdict = "partial"  # no data: not scoreable yet
                fired = None
            else:
                fired = crossing_dose(curve)
                verdict = _band_contains(predicted, fired)
                # power rule: a crossing whose own cell straddles 0.5 is partial
                if fired is not None:
                    k, n = curve[fired]
                    lo, hi = wilson_ci(k, n)
                    if lo < 0.5 < hi and verdict == "confirmed":
                        verdict = "partial"
            out.setdefault(mode, {})[model] = {
                "predicted": predicted, "observed_crossing": fired, "verdict": verdict,
            }
    return out


def score_c3_retry_storms(storm_rate_by_mode_model: dict[str, dict[str, float]]) -> dict:
    """C3: retry-storm rate >20% of runs for malformed, both models."""
    claims = load_predictions()["claims"]["C3_retry_storm_rate_gt_20pct_of_runs"]
    predicted = claims["malformed"]["both_models"]
    out = {}
    for model, rate in storm_rate_by_mode_model.get("malformed", {}).items():
        observed = rate > 0.20
        out[model] = {
            "predicted": predicted, "observed_rate": round(rate, 4),
            "verdict": "confirmed" if observed else "refuted",
        }
    return out


def not_yet_scoreable(claim: str, why: str) -> dict:
    return {"claim": claim, "verdict": "pending", "reason": why}


def score_all(index_records: list[dict], ledger_rows_by_run: dict[str, list[dict]],
              models: tuple[str, ...] = ("gpt-5-nano", "gpt-5-mini")) -> dict:
    """Full prereg scoring from runs/index.jsonl records + per-run ledgers.
    C4/C5/C6 artifacts (bench, vignette, QLoRA) are scored separately later."""
    import statistics

    def key(r: dict) -> str:
        return str(r.get("model", "")).split(":")[-1]

    # ---- C2 curves + C1 multipliers
    curves: dict[str, dict[str, dict[float, tuple[int, int]]]] = {}
    token_means: dict[str, dict[str, dict[float, float]]] = {}
    storm_rates: dict[str, dict[str, float]] = {}
    records = [r for r in index_records if not str(r.get("run_id", "")).startswith("smoke-")]
    cells: dict[tuple, list[dict]] = {}
    for r in records:
        mode = "clean" if r.get("dose") in (0, 0.0) else r.get("mode", "clean")
        cells.setdefault((key(r), mode, float(r.get("dose", 0.0))), []).append(r)
    for (model, mode, dose), runs in cells.items():
        n = len(runs)
        completed = sum(1 for r in runs if r.get("outcome") == "completed")
        curves.setdefault(mode, {}).setdefault(model, {})[dose] = (completed, n)
        toks = [r.get("tokens_in", 0) + r.get("tokens_out", 0) for r in runs]
        token_means.setdefault(mode, {}).setdefault(model, {})[dose] = statistics.mean(toks)
        if ledger_rows_by_run:
            stormy = 0
            checked = 0
            for r in runs:
                rows = ledger_rows_by_run.get(r.get("run_id", ""), [])
                if rows:
                    checked += 1
                    from mcp_storm.analysis.stats import retry_metrics

                    if retry_metrics(rows)["storm_run"]:
                        stormy += 1
            if checked:
                rate = stormy / checked
                storm_rates.setdefault(mode, {})[model] = rate
    mults: dict[str, dict[float, float | None]] = {}
    for mode, by_model in token_means.items():
        for model, by_dose in by_model.items():
            clean = token_means.get("clean", {}).get(model, {}).get(0.0)
            if clean is None:
                continue  # no dose-0 baseline for this model yet
            for dose, mean in by_dose.items():
                mults.setdefault(mode, {})[dose] = mean / clean if clean else None
    return {
        "C1": score_c1_cost_multiplier(mults),
        "C2": score_c2_completion(curves),
        "C3": score_c3_retry_storms(storm_rates),
        "C4": not_yet_scoreable("C4", "mini-benchmark artifacts land Sat Oct 25"),
        "C5": not_yet_scoreable("C5", "vignette runs land Sun Nov 1"),
        "C6": not_yet_scoreable("C6", "QLoRA eval lands Sun Nov 8"),
    }
