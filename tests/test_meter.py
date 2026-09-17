"""Meter: callback accumulation + ledger join (BUILD-GUIDE §5.1, §8)."""

from __future__ import annotations

import json
import time
from pathlib import Path

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage

from mcp_storm.agent.callbacks import MeterCallbackHandler
from mcp_storm.meter.report import join_meter_to_ledger, write_report
from mcp_storm.prices import cost_of


def _handler_with(calls: list[tuple[str, dict | None]]) -> tuple[MeterCallbackHandler, float]:
    h = MeterCallbackHandler(run_id="m-run", model="openai:gpt-5-nano")
    for node, usage in calls:
        h.calls.append({"ts": time.time(), "run_id": "m-run", "node": node,
                        "llm_run_id": "x", "usage": usage, "model": h.model})
    return h, h.cost_usd(__import__("mcp_storm.prices", fromlist=["prices"]))


def test_usage_accumulation_per_node():
    h, cost = _handler_with([
        ("plan", {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110,
                  "input_token_details": {"cached_tokens": 50}}),
        ("plan", {"input_tokens": 200, "output_tokens": 20, "total_tokens": 220}),
        ("evaluate", {"input_tokens": 300, "output_tokens": 5, "total_tokens": 305}),
        ("plan", None),  # FakeChatModel without usage: tolerated, counted as 0
    ])
    summary = h.summary()
    assert summary["totals"]["input_tokens"] == 600
    assert summary["totals"]["output_tokens"] == 35
    assert summary["totals"]["cached_input_tokens"] == 50
    assert summary["totals"]["llm_calls"] == 4
    assert summary["per_node"]["plan"]["llm_calls"] == 3
    assert summary["per_node"]["evaluate"]["input_tokens"] == 300
    # gross pricing: 600/1M*0.06 + 35/1M*0.48
    assert abs(cost - (600 / 1e6 * 0.06 + 35 / 1e6 * 0.48)) < 1e-12


def test_cost_of_math():
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert abs(cost_of("openai:gpt-5-nano", usage) - (0.06 + 0.48)) < 1e-9
    assert abs(cost_of("openai:gpt-5-mini", usage) - (0.25 + 2.00)) < 1e-9


def _ledger_row(seq: int, tool: str, t_ns: int) -> dict:
    return {"run_id": "m-run", "task_id": "t", "server": "filesystem", "tool": tool,
            "args_bytes": 10, "result_bytes": 20, "n_content_blocks": 1,
            "is_error": False, "fault_mode_injected": "malformed",
            "t_request_ns": t_ns, "t_response_ns": t_ns + 5_000_000,
            "latency_ms": 5.0, "jsonrpc_id": seq, "call_seq": seq}


def test_join_ok_and_mismatch_fails(tmp_path: Path):
    now_ns = time.time_ns()
    h, _ = _handler_with([("plan", {"input_tokens": 10, "output_tokens": 1,
                                    "total_tokens": 11})] * 3)
    meter = h.summary()
    ledger = [_ledger_row(i, "write_file", now_ns - 1_000_000_000 // 2) for i in range(1, 4)]
    joined = join_meter_to_ledger(meter, ledger)
    assert joined["join_ok"] is True, joined
    assert joined["tool_calls"] == 3 and joined["llm_calls"] == 3
    assert joined["per_tool"]["write_file"]["calls"] == 3
    assert joined["per_tool"]["write_file"]["faulted"] == 3
    report = write_report(tmp_path, meter, joined)
    assert (tmp_path / "meter.md").exists() and (tmp_path / "meter_report.json").exists()
    assert report["join"]["join_ok"] is True

    # >5% volume mismatch must fail the join (test_meter_join gate, §5.1)
    h2, _ = _handler_with([("plan", None)] * 10)
    joined2 = join_meter_to_ledger(h2.summary(), ledger)  # 3 tools vs 10 llm calls
    assert joined2["join_ok"] is False


def test_join_window_violation_fails():
    now_ns = time.time_ns()
    h, _ = _handler_with([("plan", None)])
    meter = h.summary()
    far_past = now_ns - 3600 * 1_000_000_000  # an hour before any LLM call
    ledger = [_ledger_row(1, "write_file", far_past)]
    joined = join_meter_to_ledger(meter, ledger)
    assert joined["join_ok"] is False
    assert joined["window_violations"]


async def test_callback_via_real_invoke(tmp_path: Path):
    """Usage rides through a real ainvoke path (langchain 1.x LLMResult)."""
    model = GenericFakeChatModel(messages=iter([
        AIMessage(content="hi", usage_metadata={"input_tokens": 7, "output_tokens": 3,
                                                "total_tokens": 10}),
        AIMessage(content="again", usage_metadata={"input_tokens": 5, "output_tokens": 2,
                                                   "total_tokens": 7}),
    ]))
    handler = MeterCallbackHandler(run_id="cb-run", model="fake")
    await model.ainvoke([HumanMessage("x")], config={"callbacks": [handler]})
    await model.ainvoke([HumanMessage("y")], config={"callbacks": [handler]})
    summary = handler.summary()
    assert summary["totals"]["llm_calls"] == 2
    assert summary["totals"]["input_tokens"] == 12
    assert summary["totals"]["output_tokens"] == 5
