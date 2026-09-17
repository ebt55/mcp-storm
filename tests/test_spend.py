"""Spend breaker unit checks (BUILD-GUIDE §2.3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_storm import prices
from mcp_storm.prices import BreakerTrip, SpendTracker, cost_of, prices_hash


def test_prices_table_complete_and_hash_stable():
    for model in ("openai:gpt-5-nano", "openai:gpt-5-mini"):
        assert set(prices.PRICES[model]) == {"input", "cached_input", "output"}
    h1, h2 = prices_hash(), prices_hash()
    assert h1 == h2 and len(h1) == 16
    assert prices.PRICES["openai:gpt-5-nano"]["input"] == 0.06
    assert prices.PRICES["openai:gpt-5-mini"]["output"] == 2.00


def test_tracker_totals_and_events(tmp_path: Path):
    t = SpendTracker(runs_dir=tmp_path)
    assert t.total() == 0.0
    t.record({"kind": "run_cost", "run_id": "a", "cost_usd": 10.0})
    t.record({"kind": "run_cost", "run_id": "b", "cost_usd": 5.0})
    t.record({"kind": "WARN", "run_id": "b", "total_usd": 15.0})  # not a cost
    assert abs(t.total() - 15.0) < 1e-9
    events = [json.loads(x) for x in (tmp_path / "spend.jsonl").read_text().splitlines()]
    assert [e["kind"] for e in events] == ["run_cost", "run_cost", "WARN"]


def test_breaker_warns_then_aborts(tmp_path: Path):
    t = SpendTracker(runs_dir=tmp_path)
    t.record({"kind": "run_cost", "run_id": "a", "cost_usd": 20.0})
    t.gate("next")  # below warn: fine
    t.record({"kind": "run_cost", "run_id": "b", "cost_usd": 6.0})  # 26 >= 25
    t.gate("next")  # warns once (event recorded, no raise)
    warns = [json.loads(x) for x in (tmp_path / "spend.jsonl").read_text().splitlines()
             if '"WARN"' in x]
    assert len(warns) == 1
    t.record({"kind": "run_cost", "run_id": "c", "cost_usd": 15.0})  # 41 >= 40
    with pytest.raises(BreakerTrip):
        t.gate("next")
    aborts = [json.loads(x) for x in (tmp_path / "spend.jsonl").read_text().splitlines()
              if '"ABORT"' in x]
    assert len(aborts) == 1


def test_cost_of_cached_details_ignored_gross():
    usage = {"input_tokens": 1_000_000, "output_tokens": 0,
             "input_token_details": {"cached_tokens": 1_000_000}}
    assert abs(cost_of("openai:gpt-5-nano", usage) - 0.06) < 1e-9  # gross, not cached
