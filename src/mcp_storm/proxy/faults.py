"""Fault modes (BUILD-GUIDE §3.3).

Determinism law: for every tools/call response, the draw is
    rng = sha256(f"{run_id}:{task_id}:{call_seq}") -> uniform in [0,1)
compared against dose. When STORM_SEED is set it is appended (":{seed}") so
replicates can vary while a given (seed, run, task, call_seq) stays reproducible.
With an empty seed the key string is byte-identical to the spec.

All faults replace or delay the RESPONSE to a tools/call the child already
answered. Requests are never modified (exception: idem.py stamps _meta, §3.2).

Envelope hygiene (review day 1): every transform returns an envelope with
EITHER `result` OR `error` -- never both. A child error envelope is converted
to content-first form (its message text becomes the text block) before the
content transform, so malformed/partial of an error stay valid isError-free
responses, and the true payload never leaks via extra content blocks or
structuredContent.
"""

from __future__ import annotations

import hashlib
import json
import math

HARD_ERROR_MESSAGES: dict[str, list[str]] = {
    "filesystem": [
        "FileNotFoundError: [Errno 2] No such file or directory",
        "PermissionError: [Errno 13] Permission denied",
        "OSError: [Errno 22] Invalid argument",
    ],
    "sqlite": [
        "sqlite3.OperationalError: database is locked",
        "sqlite3.OperationalError: no such table: metrics",
        "sqlite3.DatabaseError: database disk image is malformed",
    ],
    "storm-ops": [
        "RuntimeError: ledger backend unavailable",
        "OSError: oracle append failed (device out of space)",
    ],
}
DEFAULT_HARD_ERRORS = [
    "RuntimeError: tool backend failure",
    "OSError: I/O operation on closed file",
]


def fault_key(run_id: str, task_id: str, call_seq: int, seed: str = "") -> str:
    base = f"{run_id}:{task_id}:{call_seq}"
    return f"{base}:{seed}" if seed else base


def uniform_draw(key: str) -> float:
    """sha256-keyed uniform in [0,1). Same key -> same draw, forever, cross-process."""
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def should_fault(dose: float, key: str) -> bool:
    return uniform_draw(key) < dose


def hard_error_message(server: str, call_seq: int) -> str:
    msgs = HARD_ERROR_MESSAGES.get(server, DEFAULT_HARD_ERRORS)
    return msgs[call_seq % len(msgs)]


def apply_hard_error(resp: dict, server: str, call_seq: int, style: str = "iserror") -> dict:
    msg = hard_error_message(server, call_seq)
    if style == "jsonrpc":
        return {
            "jsonrpc": "2.0",
            "id": resp.get("id"),
            "error": {"code": -32603, "message": msg},
        }
    return {
        "jsonrpc": "2.0",
        "id": resp.get("id"),
        "result": {"content": [{"type": "text", "text": msg}], "isError": True},
    }


def _content_first(resp: dict) -> tuple[dict, list[dict]]:
    """Return (result_dict, content_list) with any child error envelope folded
    into a text block, and `error` stripped so transforms emit valid envelopes."""
    result = dict(resp.get("result") or {})
    content = [dict(b) if isinstance(b, dict) else b for b in (result.get("content") or [])]
    if "error" in resp and not content:
        err = resp.get("error") or {}
        text = f"{err.get('code', -32603)}: {err.get('message', 'unknown error')}"
        content = [{"type": "text", "text": text}]
    return result, content


def _truncate_mid_string(text: str, fraction: float = 0.6) -> str:
    """Cut at floor(fraction*len) with no regard for token boundaries (§3.3 malformed a)."""
    return text[: math.floor(len(text) * fraction)]


def apply_malformed(resp: dict, call_seq: int) -> dict:
    """Variant by call_seq % 3 (§3.3):
    0 -> (a) JSON text truncated at 60% mid-string
    1 -> (b) only when the text parses as a JSON array, replace the array with its
          first object; else fall back to (a)
    2 -> (c) empty text ""
    Valid envelope, isError explicitly false. Content is reduced to the single
    corrupted text block (no leaks through sibling blocks/structuredContent).
    """
    out = {"jsonrpc": "2.0", "id": resp.get("id")}
    _result, content = _content_first(resp)
    variant = call_seq % 3
    text = next(
        (b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text"
                 and isinstance(b.get("text"), str)),
        "",
    )
    if variant == 2:
        new_text = ""
    elif variant == 1:
        new_text = _truncate_mid_string(text)
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            parsed = None
        if isinstance(parsed, list) and parsed:
            new_text = json.dumps(parsed[0], separators=(",", ":"), ensure_ascii=False)
    else:
        new_text = _truncate_mid_string(text)
    out["result"] = {
        "content": [{"type": "text", "text": new_text}],
        "isError": False,
    }
    return out


def apply_partial(resp: dict, call_seq: int) -> dict:
    """Keep ceil(50%) of a JSON array's items; else truncate single text at the
    last word boundary <=60%. No error signal (§3.3). Content reduced to the
    single partial text block."""
    out = {"jsonrpc": "2.0", "id": resp.get("id")}
    _result, content = _content_first(resp)
    text = next(
        (b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text"
                 and isinstance(b.get("text"), str)),
        "",
    )
    new_text: str
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        parsed = None
    if isinstance(parsed, list):
        keep = math.ceil(len(parsed) / 2)
        new_text = json.dumps(parsed[:keep], separators=(",", ":"), ensure_ascii=False)
    else:
        prefix = text[: math.floor(len(text) * 0.6)]
        last_ws = max(prefix.rfind(" "), prefix.rfind("\n"), prefix.rfind("\t"))
        new_text = prefix[:last_ws].rstrip() if last_ws > 0 else prefix
    out["result"] = {"content": [{"type": "text", "text": new_text}]}
    return out
