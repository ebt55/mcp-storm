"""Graph nodes: plan -> act -> evaluate (BUILD-GUIDE §5.1).

Node factories bind the model in closures (the model rides in the graph, not in
runner config, so checkpoint serde never touches a chat-model object).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage
from langgraph.prebuilt import ToolNode

from mcp_storm.paths import TASKS_DIR

EVALUATE_KEYWORDS = ("continue", "done", "escalate")
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


def make_plan_node(model: BaseChatModel) -> NodeFn:
    async def plan_node(state: dict[str, Any]) -> dict[str, Any]:
        messages: list[AnyMessage] = state["messages"]
        if state.get("step_count", 0) == 0:
            steer = "Produce a short plan, then act using the tools."
        else:
            steer = "Continue executing your plan with the tools."
        ai = await model.ainvoke([("system", system_prompt() + "\n\n" + steer), *messages])
        return {"messages": [ai], "step_count": state.get("step_count", 0) + 1}

    return plan_node


def make_evaluate_node(model: BaseChatModel) -> NodeFn:
    async def evaluate_node(state: dict[str, Any]) -> dict[str, Any]:
        ai = await model.ainvoke([*state["messages"], ("system", evaluate_prompt())])
        text = ai.content if isinstance(ai.content, str) else str(ai.content)
        lowered = text.lower()
        verdict = next((k for k in EVALUATE_KEYWORDS if k in lowered), "continue")
        update: dict[str, Any] = {"messages": [ai], "eval_verdict": verdict}
        if verdict == "escalate":
            update["escalated"] = True
        return update

    return evaluate_node


def route_after_plan(state: dict[str, Any]) -> str:
    last = state["messages"][-1]
    return "act" if getattr(last, "tool_calls", None) else "evaluate"


def route_after_evaluate(state: dict[str, Any]) -> str:
    return "plan" if state.get("eval_verdict", "continue") == "continue" else "__end__"


def build_act_node(tools: list) -> ToolNode:
    return ToolNode(tools, handle_tool_errors=True)
