"""MeterCallbackHandler: per-node, per-run LLM token accounting (§5.1, §8).

on_llm_end accumulates response.usage_metadata keyed (run_id, langgraph_node).
usage_metadata=None is tolerated (FakeChatModel offline tests) -- those calls
are counted with zeros and flagged synthetic=False... rather: recorded with
usage=None so joins/tests can inject synthetic values (§5.1). Written to
runs/<run_id>/meter.json by the worker at run end.
"""

from __future__ import annotations

import time
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler


class MeterCallbackHandler(AsyncCallbackHandler):
    def __init__(self, run_id: str, model: str) -> None:
        self.run_id = run_id
        self.model = model
        self.calls: list[dict[str, Any]] = []

    async def on_llm_end(self, response, *, run_id, parent_run_id=None, tags=None,
                         metadata=None, **kwargs) -> None:
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            # ainvoke paths deliver an LLMResult: usage lives on the generation
            # message (langchain 1.x)
            try:
                gens = response.generations[0]
                usage = getattr(gens[0].message, "usage_metadata", None) if gens else None
            except (AttributeError, IndexError, TypeError):
                usage = None
        node = (metadata or {}).get("langgraph_node") or (metadata or {}).get("tags", ["?"])[0]
        self.calls.append(
            {
                "ts": time.time(),
                "run_id": self.run_id,
                "node": node,
                "llm_run_id": str(run_id),
                "usage": dict(usage) if usage else None,
                "model": self.model,
            }
        )

    # ------------------------------------------------------------- aggregation
    def summary(self) -> dict[str, Any]:
        per_node: dict[str, dict[str, int]] = {}
        totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                  "cached_input_tokens": 0, "llm_calls": 0}
        for call in self.calls:
            node = call["node"]
            bucket = per_node.setdefault(
                node, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                       "cached_input_tokens": 0, "llm_calls": 0},
            )
            usage = call.get("usage") or {}
            inp = usage.get("input_tokens") or 0
            out = usage.get("output_tokens") or 0
            tot = usage.get("total_tokens") or (inp + out)
            cached = ((usage.get("input_token_details") or {}).get("cached_tokens")) or 0
            for target in (bucket, totals):
                target["input_tokens"] += inp
                target["output_tokens"] += out
                target["total_tokens"] += tot
                target["cached_input_tokens"] += cached
                target["llm_calls"] += 1
        return {"run_id": self.run_id, "model": self.model,
                "totals": totals, "per_node": per_node, "calls": self.calls}

    def cost_usd(self, prices_module) -> float:
        from mcp_storm.prices import cost_of

        if self.model not in prices_module.PRICES:
            return 0.0  # fake models cost nothing
        return sum(
            cost_of(self.model, c["usage"] or {}) for c in self.calls if c.get("usage")
        )
