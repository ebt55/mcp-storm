"""StormState + graph assembly (BUILD-GUIDE §5.1).

START -> plan -> {act -> evaluate | evaluate} -> {plan | END}
recursion_limit=40 => an over-looping run surfaces as outcome `loop`.
Runner uses plain ainvoke (no streaming; usage_metadata reliability).
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from . import nodes

RECURSION_LIMIT = 40


class StormState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    plan: str
    step_count: int
    escalated: bool
    run_id: str
    task_id: str
    eval_verdict: str


def build_graph(
    model: BaseChatModel,
    tools: list,
    checkpointer: BaseCheckpointSaver | None = None,
) -> Any:
    """Compile the supervisor graph for one run (model bound in node closures).

    The plan node uses the model WITH bound tools (real models can then emit
    tool_calls; FakeChatModel scripts them regardless). The evaluate node uses
    the raw model: a verdict must never carry tool_calls, and an evaluator that
    cannot call tools cannot smuggle actions past the router."""
    try:
        bound = model.bind_tools(tools)
    except NotImplementedError:  # fake chat models script tool_calls themselves
        bound = model
    builder = StateGraph(StormState)
    builder.add_node("plan", nodes.make_plan_node(bound))
    builder.add_node("act", ToolNode(tools, handle_tool_errors=True))
    builder.add_node("evaluate", nodes.make_evaluate_node(model))
    builder.add_edge(START, "plan")
    builder.add_conditional_edges(
        "plan", nodes.route_after_plan, {"act": "act", "evaluate": "evaluate"}
    )
    builder.add_edge("act", "evaluate")
    builder.add_conditional_edges(
        "evaluate", nodes.route_after_evaluate, {"plan": "plan", END: END}
    )
    return builder.compile(checkpointer=checkpointer)


def invoke_config(run_id: str, task_id: str, thread_id: str) -> dict:
    """Config for ainvoke: identity + a fresh thread per run."""
    return {
        "configurable": {
            "thread_id": thread_id,
            "run_id": run_id,
            "task_id": task_id,
        },
        "recursion_limit": RECURSION_LIMIT,
    }
