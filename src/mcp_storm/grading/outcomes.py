"""Outcome classification (BUILD-GUIDE §5.4).

completed | wrong_answer | loop | timeout | crashed, plus the escalated and
refusal columns. Precedence: crashed > timeout > loop > grading. Refusals are
a CONFOUND COLUMN (prereg phrase list, case-insensitive substring on the final
assistant text message) -- never excluded, never change the outcome.
"""

from __future__ import annotations

from typing import Any

OUTCOMES = ("completed", "wrong_answer", "loop", "timeout", "crashed")


def load_refusal_phrases() -> list[str]:
    """The committed prereg list is the single source of truth (§5.4)."""
    import json

    from mcp_storm.paths import PREREG_DIR

    data = json.loads((PREREG_DIR / "predictions.json").read_text(encoding="utf-8"))
    phrases = data.get("refusal_phrases")
    if not isinstance(phrases, list) or not phrases:
        raise ValueError("prereg/predictions.json has no refusal_phrases list")
    return [str(p) for p in phrases]


def is_refusal(final_messages: list[Any], phrases: list[str]) -> bool:
    """Match the prereg refusal phrase list against the last assistant TEXT message."""
    last_text = None
    for message in reversed(final_messages or []):
        if getattr(message, "type", "") == "ai":
            content = getattr(message, "content", "")
            if isinstance(content, str) and content.strip():
                last_text = content
                break
            if isinstance(content, list):
                texts = [p.get("text", "") for p in content if isinstance(p, dict)]
                joined = " ".join(t for t in texts if t).strip()
                if joined:
                    last_text = joined
                    break
    if not last_text:
        return False
    lowered = last_text.lower()
    return any(phrase.lower() in lowered for phrase in phrases)


def classify(
    *,
    exception: BaseException | None = None,
    timed_out: bool = False,
    recursion_error: bool = False,
    escalated: bool = False,
    grader_result: dict | None = None,
    final_messages: list[Any] | None = None,
    refusal_phrases: list[str] | None = None,
) -> dict[str, Any]:
    from langgraph.errors import GraphRecursionError

    if refusal_phrases is None:
        try:
            refusal_phrases = load_refusal_phrases()
        except (OSError, ValueError):
            refusal_phrases = []

    if exception is not None and not isinstance(exception, GraphRecursionError):
        outcome = "crashed"
    elif timed_out:
        outcome = "timeout"
    elif recursion_error or isinstance(exception, GraphRecursionError):
        outcome = "loop"
    elif grader_result is not None and grader_result.get("passed"):
        outcome = "completed"
    else:
        outcome = "wrong_answer"
    refusal = is_refusal(final_messages or [], refusal_phrases or [])
    return {"outcome": outcome, "escalated": escalated, "refusal": refusal}
