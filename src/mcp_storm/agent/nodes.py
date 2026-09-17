"""Graph nodes: plan -> act -> evaluate (BUILD-GUIDE §5.1).

Node factories bind the model in closures (the model rides in the graph, not in
runner config, so checkpoint serde never touches a chat-model object).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage

from mcp_storm.paths import TASKS_DIR

NodeFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def system_prompt() -> str:
    return (TASKS_DIR / "PROMPT.md").read_text(encoding="utf-8")


def evaluate_prompt() -> str:
    return (
        "You are the supervisor's evaluator. Look at the conversation so far and "
        "reply with exactly one word on the first line: continue (more tool work "
        "is needed), done (the task is complete), or escalate (the task cannot "
        "be completed; a human must take over)."
    )


def parse_verdict(text: str) -> str:
    """First word of the first line only; priority escalate > done > continue.
    Substring matching anywhere in the reply would turn 'I cannot continue --
    escalate' into 'continue' and 'abandoned' into 'done' (CP-B review)."""
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    normalized = first_line.lower().replace(",", " ").replace(".", " ").replace("!", " ")
    words = normalized.split()
    if not words:
        return "continue"
    first = words[0]
    for keyword in ("escalate", "done", "continue"):
        if first.startswith(keyword):
            return keyword
    return "continue"


def make_plan_node(model: BaseChatModel) -> NodeFn:
    async def plan_node(state: dict[str, Any]) -> dict[str, Any]:
        messages: list[AnyMessage] = state["messages"]
        if state.get("step_count", 0) == 0:
            steer = "Produce a short plan, then act using the tools."
        else:
            steer = "Continue executing your plan with the tools."
        # storm-node tags propagate to inherited callbacks (metadata does not
        # reach handlers attached at the graph level in langgraph 1.2)
        ai = await model.ainvoke(
            [("system", system_prompt() + "\n\n" + steer), *messages],
            config={"tags": ["storm-node:plan"]},
        )
        return {"messages": [ai], "step_count": state.get("step_count", 0) + 1}

    return plan_node


def make_evaluate_node(model: BaseChatModel) -> NodeFn:
    async def evaluate_node(state: dict[str, Any]) -> dict[str, Any]:
        # raw (unbound) model: a verdict must never carry tool_calls
        ai = await model.ainvoke(
            [("system", evaluate_prompt()), *state["messages"]],
            config={"tags": ["storm-node:evaluate"]},
        )
        text = ai.content if isinstance(ai.content, str) else str(ai.content)
        verdict = parse_verdict(text)
        update: dict[str, Any] = {
            "messages": [AIMessage(content=str(ai.content))],  # strip any tool_calls
            "eval_verdict": verdict,
        }
        if verdict == "escalate":
            update["escalated"] = True
        # the escalate TOOL is a side effect; surface it in state as well
        if any(m.type == "tool" and getattr(m, "name", "") == "escalate"
               for m in state["messages"]):
            update["escalated"] = True
        return update

    return evaluate_node


def route_after_plan(state: dict[str, Any]) -> str:
    last = state["messages"][-1]
    return "act" if getattr(last, "tool_calls", None) else "evaluate"


def route_after_evaluate(state: dict[str, Any]) -> str:
    return "plan" if state.get("eval_verdict", "continue") == "continue" else "__end__"
