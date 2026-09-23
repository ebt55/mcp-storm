"""Prereg claim scoring (BUILD-GUIDE §7). committed BEFORE launch; gold-tested
on synthetic curves. Verdicts: confirmed / partial / refuted.

Band semantics (predictions.json strings):
  "never"            estimator never fires through the top dose
  "never-below-70%"  estimator fires only at the 70% dose or never
  "70%" / "40%"      estimator fires exactly at that dose
  "10-20%" etc.      estimator fires inside the closed range
Band strings may carry sub-claims after ";" (e.g. the latency P95>=3x
companion) -- the completion band is the part before the first ";".

Power rule (corrected 2026-09-23, DEVIATIONS #30): a Wilson-UB<50% crossing at
n=50 is confirmable only at observed <= 18/50; a fired crossing whose own cell
rate exceeds 0.30 is weakly supported and scores 'partial'.

Only the preregistered model pair is scored. Exploratory arms (or:*) are
excluded from every claim by name (predictions.json: "NEVER scored against
prereg claims").
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp_storm.analysis.stats import crossing_dose, wilson_ci
from mcp_storm.paths import PREREG_DIR

DOSE_GRID = (0.05, 0.10, 0.20, 0.40, 0.70)
VERDICTS = ("confirmed", "partial", "refuted")
PREREG_MODEL_KEYS = ("gpt-5-nano", "gpt-5-mini")


def load_predictions() -> dict[str, Any]:
    return json.loads((PREREG_DIR / "predictions.json").read_text(encoding="utf-8"))


def model_key(model_name: str) -> str:
    """'openai:gpt-5-nano' -> 'gpt-5-nano' (the predictions.json key)."""
    return str(model_name).split(":")[-1]


def is_prereg_model(model_name: str) -> bool:
    return model_key(model_name) in PREREG_MODEL_KEYS


def split_band(band: str) -> tuple[str, str | None]:
    """'never-below-70%; P95>=3x-dose0-at-40%+' -> ('never-below-70%', 'P95...')."""
    if ";" in band:
        head, _, tail = band.partition(";")
        return head.strip(), tail.strip() or None
    return band, None


def _band_contains(band: str, value: float | None) -> str:
    """Score an observed estimator value against a predicted band."""
    band, _ = split_band(band)

    def grid_index(dose_pct: float) -> int | None:
        for i, grid in enumerate(DOSE_GRID):
            if abs(grid * 100 - dose_pct) < 1e-9:
                return i
        return None

    if band == "never":
        return "confirmed" if value is None else "refuted"
    if band == "never-below-70%":
        if value is None or value >= 0.70:
            return "confirmed"
        return "refuted" if value <= 0.40 else "partial"
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


def score_c1_cost_multiplier(mults_by_model: dict[str, dict[str, dict[float, float | None]]]) -> dict:
    """mults_by_model: model -> mode -> dose -> cost multiplier.
    C1: smallest dose with multiplier > 3, per mode, vs the predicted band.
    Per-model (review 2026-09-23: a single shared dict let the last model
    overwrite the first)."""
    claims = load_predictions()["claims"]["C1_cost_multiplier_gt_3x"]
    out: dict[str, dict[str, dict]] = {}
    for mode, band in claims.items():
        for model, predicted in band.items():
            if model not in PREREG_MODEL_KEYS and model != "both_models":
                continue
            curve = mults_by_model.get(model, {}).get(mode, {})
            fired = next((d for d in sorted(curve) if (curve[d] or 0) > 3), None)
            out.setdefault(mode, {})[model] = {
                "predicted": predicted, "observed_first_dose": fired,
                "verdict": _band_contains(predicted, fired),
            }
    return out


def score_c2_completion(curves_by_model: dict[str, dict[str, dict[str, dict[float, tuple[int, int]]]]],
                        ) -> dict:
    """curves_by_model: model -> mode -> dose-keyed curve (via model_key()).
    C2 crossing estimator per model."""
    claims = load_predictions()["claims"]["C2_completion_below_50_wilson_ub"]
    out: dict[str, dict[str, dict]] = {}
    for mode, band in claims.items():
        for model, predicted in band.items():
            if model not in PREREG_MODEL_KEYS:
                continue
            curve = curves_by_model.get(model, {}).get(mode, {})
            sub_claim = split_band(predicted)[1]
            if not any(n for _, n in curve.values()):
                verdict = "partial"  # no data: not scoreable yet
                fired = None
            else:
                fired = crossing_dose(curve)
                verdict = _band_contains(predicted, fired)
                # power rule (DEVIATIONS #30): a fired crossing is weakly
                # supported when its own cell rate exceeds 0.30 (at n=50 the
                # 80%-power threshold; 19/50 is the first non-confirming count)
                if fired is not None and verdict == "confirmed":
                    k, n = curve[fired]
                    if n and k / n > 0.30:
                        verdict = "partial"
            out.setdefault(mode, {})[model] = {
                "predicted": predicted, "observed_crossing": fired,
                "verdict": verdict, "sub_claim": sub_claim,
            }
    return out


def score_c3_retry_storms(storm_counts: dict[str, dict[str, tuple[int, int]]]) -> dict:
    """C3: pooled retry-storm rate >20% of runs for malformed, both models.
    storm_counts: mode -> model -> (stormy_runs, checked_runs) pooled across
    ALL doses deterministically (review 2026-09-23: per-dose overwrite made
    the verdict depend on index row order)."""
    claims = load_predictions()["claims"]["C3_retry_storm_rate_gt_20pct_of_runs"]
    predicted = claims["malformed"]["both_models"]
    out = {}
    for model, (stormy, checked) in storm_counts.get("malformed", {}).items():
        if model not in PREREG_MODEL_KEYS:
            continue
        rate = stormy / checked if checked else 0.0
        out[model] = {
            "predicted": predicted, "observed_rate": round(rate, 4),
            "stormy": stormy, "checked": checked,
            "verdict": "confirmed" if rate > 0.20 else "refuted",
        }
    return out


def not_yet_scoreable(claim: str, why: str) -> dict:
    return {"claim": claim, "verdict": "pending", "reason": why}


def score_all(index_records: list[dict], ledger_rows_by_run: dict[str, list[dict]]) -> dict:
    """Full prereg scoring from runs/index.jsonl records + per-run ledgers.
    Only preregistered models are aggregated; smoke runs and exploratory arms
    are excluded everywhere. C4/C5/C6 artifacts are scored separately later."""
    import statistics

    from mcp_storm.analysis.stats import retry_metrics

    records = [
        r for r in index_records
        if not str(r.get("run_id", "")).startswith("smoke-")
        and is_prereg_model(r.get("model", ""))
    ]
    cells: dict[tuple, list[dict]] = {}
    for r in records:
        mode = "clean" if r.get("dose") in (0, 0.0) else r.get("mode", "clean")
        cells.setdefault((model_key(r.get("model", "")), mode, float(r.get("dose", 0.0))), []).append(r)

    curves: dict[str, dict[str, dict[str, dict[float, tuple[int, int]]]]] = {}
    mults: dict[str, dict[str, dict[float, float | None]]] = {}
    storm_counts: dict[str, dict[str, tuple[int, int]]] = {}
    for (model, mode, dose), runs in cells.items():
        n = len(runs)
        completed = sum(1 for r in runs if r.get("outcome") == "completed")
        curves.setdefault(model, {}).setdefault(mode, {})[dose] = (completed, n)
        toks = [r.get("tokens_in", 0) + r.get("tokens_out", 0) for r in runs]
        token_mean = statistics.mean(toks) if toks else 0.0
        mults.setdefault(model, {}).setdefault(mode, {})[dose] = token_mean
        stormy = checked = 0
        for r in runs:
            rows = ledger_rows_by_run.get(r.get("run_id", ""), [])
            if rows:
                checked += 1
                if retry_metrics(rows)["storm_run"]:
                    stormy += 1
        if checked:
            prev = storm_counts.setdefault(mode, {}).get(model, (0, 0))
            storm_counts[mode][model] = (prev[0] + stormy, prev[1] + checked)
    # multipliers vs each model's OWN clean baseline (per-model, review fix)
    for model, by_mode in mults.items():
        clean_mean = by_mode.get("clean", {}).get(0.0)
        for mode, by_dose in by_mode.items():
            for dose, mean in by_dose.items():
                by_dose[dose] = mean / clean_mean if clean_mean else None

    return {
        "C1": score_c1_cost_multiplier(mults),
        "C2": score_c2_completion(curves),
        "C3": score_c3_retry_storms(storm_counts),
        "C4": not_yet_scoreable("C4", "mini-benchmark artifacts land with the bench phase"),
        "C5": not_yet_scoreable("C5", "vignette runs land with the vignette phase"),
        "C6": not_yet_scoreable("C6", "QLoRA eval demoted to exploratory (DEVIATIONS); scored if run"),
    }
