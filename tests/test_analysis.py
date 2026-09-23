"""Gold tests for the frozen estimators + prereg scoring on SYNTHETIC curves
(BUILD-GUIDE §14 Fri Oct 9 KC: 'predictions.json parses; prereg_checks runs
on synthetic data')."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_storm.analysis import prereg_checks as pc
from mcp_storm.analysis.aggregate import aggregate
from mcp_storm.analysis.stats import (
    cochran_armitage_one_sided,
    cost_multiplier,
    crossing_dose,
    p95,
    retry_metrics,
    wilson_ci,
)


# ------------------------------------------------------------------- statistics
def test_wilson_known_values():
    lo, hi = wilson_ci(0, 50)
    assert lo < 1e-12 and hi < 0.075  # 0/50: UB ~7.1%
    lo, hi = wilson_ci(50, 50)
    assert hi == 1.0 and lo > 0.925
    lo, hi = wilson_ci(25, 50)
    assert abs(lo - 0.3664) < 0.002 and abs(hi - 0.6336) < 0.002
    # corrected power note (DEVIATIONS #24): Wilson-UB < 50% at n=50 needs
    # observed <= 18/50; 19/50 is the first non-confirming count
    _, hi18 = wilson_ci(18, 50)
    _, hi19 = wilson_ci(19, 50)
    assert hi18 < 0.5 < hi19


def test_crossing_dose_estimator():
    # completion falls off a cliff between 20% and 40% dose
    curve = {0.05: (48, 50), 0.10: (46, 50), 0.20: (40, 50), 0.40: (10, 50), 0.70: (0, 50)}
    assert crossing_dose(curve) == 0.40
    flat = {d: (49, 50) for d in curve}
    assert crossing_dose(flat) is None  # "never" predictions score confirmations
    assert crossing_dose({}) is None


def test_cochran_armitage_trend():
    strong_up = [(1, 50, 2), (2, 50, 10), (3, 50, 25), (4, 50, 40)]
    p_up = cochran_armitage_one_sided(strong_up)
    assert p_up < 0.001
    no_trend = [(1, 50, 25), (2, 50, 24), (3, 50, 26), (4, 50, 25)]
    p_flat = cochran_armitage_one_sided(no_trend)
    assert p_flat > 0.3
    down = [(1, 50, 40), (2, 50, 25), (3, 50, 10), (4, 50, 2)]
    assert cochran_armitage_one_sided(down) > 0.99


def test_p95_and_retry_metrics():
    values = [float(i) for i in range(1, 101)]
    assert p95(values) == 95.0
    assert p95([5.0]) == 5.0
    rows = [
        {"tool": "write_file", "args_hash": "a", "t_request_ns": i}
        for i, _ in enumerate(range(4))  # 4 identical calls -> 1 storm, 3 wasted
    ]
    m = retry_metrics(rows)
    assert m == {"tool_calls": 4, "wasted_calls": 3, "retry_storms": 1, "storm_run": True}
    mixed = [
        {"tool": "t", "args_hash": "1", "t_request_ns": 1},
        {"tool": "t", "args_hash": "1", "t_request_ns": 2},
        {"tool": "t", "args_hash": "2", "t_request_ns": 3},
        {"tool": "t", "args_hash": "1", "t_request_ns": 4},
        {"tool": "t", "args_hash": "1", "t_request_ns": 5},
    ]
    m2 = retry_metrics(mixed)
    assert m2["retry_storms"] == 0 and m2["wasted_calls"] == 2 and not m2["storm_run"]
    assert cost_multiplier(60.0, 20.0) == 3.0


# ------------------------------------------------- prereg scoring, synthetic data
def _record(model, mode, dose, outcome, tokens=3000, run_id="r", wall=90.0):
    return {"run_id": run_id, "model": model, "mode": mode, "dose": dose,
            "outcome": outcome, "escalated": False, "refusal": False,
            "tokens_in": tokens, "tokens_out": 200, "cost_usd": 0.001,
            "wall_s": wall}


def test_predictions_parse_and_bands(tmp_path: Path, monkeypatch):
    preds = pc.load_predictions()
    for key in ("C1_cost_multiplier_gt_3x", "C2_completion_below_50_wilson_ub",
                "C3_retry_storm_rate_gt_20pct_of_runs"):
        assert key in preds["claims"]
    # band matcher table behavior
    assert pc._band_contains("never", None) == "confirmed"
    assert pc._band_contains("never", 0.40) == "refuted"
    assert pc._band_contains("40%", 0.40) == "confirmed"
    assert pc._band_contains("40%", 0.20) == "partial"   # adjacent grid point
    assert pc._band_contains("40%", 0.70) == "partial"   # adjacent grid point
    assert pc._band_contains("40%", 0.05) == "refuted"   # two steps away
    assert pc._band_contains("20-40%", 0.30) == "confirmed"
    assert pc._band_contains("10-20%", 0.05) == "partial"  # one grid step from 0.10
    assert pc._band_contains("20-40%", 0.05) == "refuted"  # two steps from 0.20
    assert pc._band_contains("never-below-70%", None) == "confirmed"
    assert pc._band_contains("never-below-70%", 0.70) == "confirmed"
    assert pc._band_contains("never-below-70%", 0.40) == "refuted"


def test_score_all_on_synthetic_curves():
    records = []
    # gpt-5-nano, clean: 50/50 completed, 3k tokens
    for i in range(50):
        records.append(_record("openai:gpt-5-nano", "clean", 0.0, "completed", run_id=f"c{i}"))
    # malformed: completion collapses at 40%; tokens climb 3k->9.6k at 40 (>3x)
    dose_outcomes = {0.05: (45, 50), 0.10: (42, 50), 0.20: (38, 50), 0.40: (8, 50), 0.70: (1, 50)}
    dose_tokens = {0.05: 4200, 0.10: 5200, 0.20: 6400, 0.40: 9800, 0.70: 12800}
    for dose, (done, _) in dose_outcomes.items():
        for i in range(50):
            outcome = "completed" if i < done else "wrong_answer"
            records.append(_record("openai:gpt-5-nano", "malformed", dose, outcome,
                                   tokens=dose_tokens[dose], run_id=f"m{dose}-{i}"))
    scored = pc.score_all(records, ledger_rows_by_run={})
    c2 = scored["C2"]["malformed"]["gpt-5-nano"]
    assert c2["observed_crossing"] == 0.40
    assert c2["verdict"] in ("confirmed", "partial")  # 8/50 far below 36 -> stable
    c1 = scored["C1"]["malformed"]["gpt-5-nano"]
    # first dose with multiplier > 3: 9800/~3200 = 3.06 at 0.40
    assert c1["observed_first_dose"] == 0.40
    assert c1["verdict"] in ("confirmed", "partial")
    # latency never multiplies tokens -> 'never' confirmed
    assert scored["C1"]["latency"]["gpt-5-nano"]["verdict"] == "confirmed"


def test_aggregate_regenerates_from_index():
    records = [
        _record("openai:gpt-5-nano", "clean", 0.0, "completed", run_id="a"),
        _record("openai:gpt-5-nano", "clean", 0.0, "completed", run_id="b"),
        _record("openai:gpt-5-nano", "clean", 0.0, "wrong_answer", run_id="c"),
        _record("openai:gpt-5-nano", "malformed", 0.4, "wrong_answer", tokens=9000, run_id="d"),
        # smoke runs must be excluded from every number
        _record("openai:gpt-5-nano", "clean", 0.0, "completed", run_id="smoke-x"),
    ]
    rows = aggregate(records, ledger_rows_by_run=None)
    by = {(r["mode"], r["dose"]): r for r in rows}
    clean = by[("clean", 0.0)]
    assert clean["n"] == 3 and clean["completed"] == 2
    assert clean["completion_rate"] == pytest.approx(2 / 3, abs=1e-4)
    mal = by[("malformed", 0.4)]
    mean_clean = (3200 + 3200 + 3200) / 3
    assert mal["cost_multiplier"] == pytest.approx(9200 / mean_clean, abs=2e-3)


def test_aggregate_with_ledger_storms():
    records = [_record("openai:gpt-5-nano", "malformed", 0.4, "wrong_answer", run_id="s1"),
               _record("openai:gpt-5-nano", "malformed", 0.4, "wrong_answer", run_id="s2")]
    ledgers = {
        "s1": [{"tool": "t", "args_hash": "x", "t_request_ns": i} for i in range(4)],
        "s2": [{"tool": "t", "args_hash": str(i), "t_request_ns": i} for i in range(3)],
    }
    rows = aggregate(records, ledger_rows_by_run=ledgers)
    assert rows[0]["retry_storm_runs"] == 1


# ------------------------- scorer regression tests (design review 2026-09-23)
def _rec(model, mode, dose, outcome, tokens, run_id):
    return {"run_id": run_id, "model": model, "mode": mode, "dose": dose,
            "outcome": outcome, "escalated": False, "refusal": False,
            "tokens_in": tokens, "tokens_out": 0, "cost_usd": 0.0, "wall_s": 60.0}


def test_c1_no_model_collision():
    """Two prereg models with different multipliers must score independently."""
    records = []
    # nano: multiplier stays 1.2 (never >3)
    records += [_rec("openai:gpt-5-nano", "clean", 0.0, "completed", 1000, f"nc{i}") for i in range(5)]
    records += [_rec("openai:gpt-5-nano", "malformed", 0.4, "completed", 1200, f"nm{i}") for i in range(5)]
    # mini: multiplier 7.0 at 10% dose (>3)
    records += [_rec("openai:gpt-5-mini", "clean", 0.0, "completed", 1000, f"mc{i}") for i in range(5)]
    records += [_rec("openai:gpt-5-mini", "malformed", 0.1, "completed", 7000, f"mm{i}") for i in range(5)]
    # exploratory arm with an extreme multiplier: must NEVER touch prereg scoring
    records += [_rec("or:deepseek-v4-flash", "clean", 0.0, "completed", 1000, f"ec{i}") for i in range(5)]
    records += [_rec("or:deepseek-v4-flash", "malformed", 0.05, "completed", 99000, f"em{i}") for i in range(5)]
    scored = pc.score_all(records, ledger_rows_by_run={})
    nano = scored["C1"]["malformed"]["gpt-5-nano"]
    mini = scored["C1"]["malformed"]["gpt-5-mini"]
    assert nano["observed_first_dose"] is None and nano["verdict"] == "refuted"
    assert mini["observed_first_dose"] == 0.10 and mini["verdict"] in ("confirmed", "partial")
    assert "deepseek-v4-flash" not in scored["C1"]["malformed"]


def test_c1_order_independent():
    records = ([_rec("openai:gpt-5-nano", "clean", 0.0, "completed", 1000, f"a{i}") for i in range(3)]
               + [_rec("openai:gpt-5-nano", "malformed", 0.1, "completed", 5000, f"b{i}") for i in range(3)]
               + [_rec("openai:gpt-5-mini", "clean", 0.0, "completed", 1000, f"c{i}") for i in range(3)]
               + [_rec("openai:gpt-5-mini", "malformed", 0.1, "completed", 1100, f"d{i}") for i in range(3)])
    forward = pc.score_all(list(records), ledger_rows_by_run={})
    reverse = pc.score_all(list(reversed(records)), ledger_rows_by_run={})
    assert forward["C1"] == reverse["C1"]
    assert forward["C2"] == reverse["C2"]


def test_c3_pooled_across_doses_not_order_dependent():
    """C3 pools storm counts across ALL doses deterministically (was: last
    dose's rate won, verdicts flipped with index order)."""
    base = ([_rec("openai:gpt-5-nano", "clean", 0.0, "completed", 1000, f"c{i}") for i in range(3)])
    mal = ([_rec("openai:gpt-5-nano", "malformed", d, "completed", 1000, f"m{d}-{i}")
            for d in (0.05, 0.40) for i in range(3)])
    ledgers = {}
    # exactly ONE stormy run (of 6 malformed): pooled 1/6 -> refuted (<20%)
    for r in mal:
        first_stormy = r["run_id"] == "m0.05-0"
        rows = ([{"tool": "t", "args_hash": "x", "t_request_ns": i} for i in range(3)]
                if first_stormy else
                [{"tool": "t", "args_hash": str(i), "t_request_ns": i} for i in range(3)])
        ledgers[r["run_id"]] = rows
    scored = pc.score_all(base + mal, ledger_rows_by_run=ledgers)
    entry = scored["C3"]["gpt-5-nano"]
    assert entry["stormy"] == 1 and entry["checked"] == 6
    assert entry["observed_rate"] == pytest.approx(1 / 6, abs=1e-4)
    assert entry["verdict"] == "refuted"
    # reversed order must give the identical pooled verdict
    scored_rev = pc.score_all(list(reversed(base + mal)), ledger_rows_by_run=ledgers)
    assert scored_rev["C3"] == scored["C3"]


def test_latency_band_with_subclaim_parses():
    band = "never-below-70%; P95>=3x-dose0-at-40%+"
    assert pc.split_band(band) == ("never-below-70%", "P95>=3x-dose0-at-40%+")
    assert pc._band_contains(band, None) == "confirmed"  # used to crash
    assert pc._band_contains(band, 0.70) == "confirmed"
    assert pc._band_contains(band, 0.40) == "refuted"
    assert pc._band_contains("never", None) == "confirmed"


def test_c2_power_rule_reachable():
    """A fired crossing whose cell rate > 0.30 is weakly supported -> partial.
    (The weak zone exists at n=50: k=18/50 fires Wilson-UB<0.5 at rate 0.36.)"""
    records = ([_rec("openai:gpt-5-mini", "clean", 0.0, "completed", 1000, f"c{i}") for i in range(3)])
    for d, done in [(0.05, 48), (0.10, 45), (0.20, 40), (0.40, 18), (0.70, 5)]:
        for i in range(50):
            records.append(_rec("openai:gpt-5-mini", "malformed", d,
                                "completed" if i < done else "wrong_answer", 1000,
                                f"m{d}-{i}"))
    scored = pc.score_all(records, ledger_rows_by_run={})
    entry = scored["C2"]["malformed"]["gpt-5-mini"]
    assert entry["observed_crossing"] == 0.40
    assert entry["verdict"] == "partial"  # 18/50=0.36 > 0.30: weak crossing
    # a hard crossing (4/50 = 0.08 at 0.40) confirms per band
    records2 = ([_rec("openai:gpt-5-mini", "clean", 0.0, "completed", 1000, f"c{i}") for i in range(3)])
    for d, done in [(0.05, 48), (0.10, 45), (0.20, 30), (0.40, 4), (0.70, 0)]:
        for i in range(50):
            records2.append(_rec("openai:gpt-5-mini", "malformed", d,
                                 "completed" if i < done else "wrong_answer", 1000,
                                 f"n{d}-{i}"))
    entry2 = pc.score_all(records2, ledger_rows_by_run={})["C2"]["malformed"]["gpt-5-mini"]
    assert entry2["observed_crossing"] == 0.40 and entry2["verdict"] == "confirmed"
