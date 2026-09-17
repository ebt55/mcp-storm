"""Vignette rider: idempotency-key stamping at the proxy (BUILD-GUIDE §3.2, §9).

The one legal request modification: params._meta["idempotency_key"] =
sha256(run_id + tool + canonical_args)[:16]. On replay after a kill the
checkpointed AIMessage replays byte-identical args -> the same key -> a
dedupe-aware server returns the original result instead of double-firing.
"""

from __future__ import annotations

import hashlib
import json


def canonical_args(arguments: dict | None) -> str:
    return json.dumps(arguments or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def args_hash(tool: str, arguments: dict | None) -> str:
    return hashlib.sha256(f"{tool}:{canonical_args(arguments)}".encode("utf-8")).hexdigest()[:16]


def idempotency_key(run_id: str, tool: str, arguments: dict | None) -> str:
    material = f"{run_id}:{tool}:{canonical_args(arguments)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def stamp(params: dict, run_id: str) -> dict:
    """Return a new params dict with _meta.idempotency_key stamped (never mutate)."""
    tool = str(params.get("name", ""))
    key = idempotency_key(run_id, tool, params.get("arguments"))
    meta = dict(params.get("_meta") or {})
    meta["idempotency_key"] = key
    return {**params, "_meta": meta}
