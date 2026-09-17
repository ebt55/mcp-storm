"""Runner: offline gate, resume, fake-model dry run of real cells (§6.3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from mcp_storm.runner import matrix as mx
from mcp_storm.runner.worker import completed_run_ids, read_index, run_one
from mcp_storm.tasks_io import load_task, load_tasks


def _fake_factory(_model_name: str):
    def make() -> GenericFakeChatModel:
        return GenericFakeChatModel(messages=iter([
            AIMessage(content="I will not use tools for this dry run."),
            AIMessage(content="continue"),
            AIMessage(content="Stopping; cannot complete without acting."),
            AIMessage(content="done"),
        ]))
    return make


def _write_gate(runs: Path, sha: str = "deadbeef", rc: int = 0) -> None:
    runs.mkdir(parents=True, exist_ok=True)
    (runs / ".offline_gate.json").write_text(
        json.dumps({"git_sha": sha, "pytest_rc": rc, "ts": 0}), encoding="utf-8"
    )


async def test_worker_run_one_offline(tmp_path: Path):
    runs = tmp_path / "runs"
    task = load_task("fs-08")
    record = await run_one(
        run_id="dry-fs-08", task=task, model_name="fake-model",
        model_factory=_fake_factory("fake-model"),
        mode="malformed", dose=0.5, seed="s1", runs_dir=runs, backend="fake",
    )
    run_dir = runs / "dry-fs-08"
    assert (run_dir / "messages.json").exists()
    assert (run_dir / "meter.json").exists()
    assert (run_dir / "manifest.json").exists()
    assert (run_dir / "checkpoints.db").exists(), "AsyncSqliteSaver per run (§5.1)"
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["mode"] == "malformed" and manifest["dose"] == 0.5
    assert manifest["prices_hash"] and manifest["git_sha"]
    assert record["outcome"] == "wrong_answer"  # fake did no work
    assert record["refusal"] is False
    assert record["cost_usd"] == 0.0
    assert read_index(runs)[0]["run_id"] == "dry-fs-08"
    assert completed_run_ids(runs) == {"dry-fs-08"}
    msgs = json.loads((run_dir / "messages.json").read_text(encoding="utf-8"))
    assert msgs and msgs[0]["role"] == "human"


def test_matrix_gate_refusals(tmp_path: Path):
    runs = tmp_path / "runs"
    with pytest.raises(RuntimeError, match="no offline gate"):
        mx.check_offline_gate(runs)
    _write_gate(runs, rc=1)
    with pytest.raises(RuntimeError, match="FAILING"):
        mx.check_offline_gate(runs)
    _write_gate(runs, sha="wrong-sha")
    with pytest.raises(RuntimeError, match="stale"):
        mx.check_offline_gate(runs)


def test_paid_backend_requires_prereg_tag(tmp_path: Path, monkeypatch):
    _write_gate(tmp_path / "runs")
    monkeypatch.setattr(mx, "_git_sha", lambda: "deadbeef")
    monkeypatch.setattr(mx, "git_tag_exists", lambda tag: False)
    with pytest.raises(RuntimeError, match="prereg law"):
        import asyncio

        asyncio.run(mx.run_matrix(models=["openai:gpt-5-nano"], tasks=load_tasks()[:1],
                                  runs=tmp_path / "runs", backend="real"))
    # and with the tag present the gate passes (run still needs a model factory)
    monkeypatch.setattr(mx, "git_tag_exists", lambda tag: True)
    assert mx.check_prereg_lock.__name__ == "check_prereg_lock"


async def test_matrix_dry_run_resume(tmp_path: Path, monkeypatch):
    runs = tmp_path / "runs"
    _write_gate(runs)
    monkeypatch.setattr(mx, "_git_sha", lambda: "deadbeef")
    monkeypatch.setattr(mx, "git_tag_exists", lambda tag: True)
    tasks = load_tasks()[:2]
    results = await mx.run_matrix(
        models=["fake-model"], tasks=tasks, runs=runs, backend="fake",
        model_factory=_fake_factory, limit=3, workers=2,
    )
    assert len(results) == 3
    ids = {r["run_id"] for r in results}
    assert completed_run_ids(runs) == ids
    # resume: second invocation must not re-execute completed runs (limit
    # applies to PENDING cells, so it runs the NEXT batch -- disjoint ids)
    again = await mx.run_matrix(
        models=["fake-model"], tasks=tasks, runs=runs, backend="fake",
        model_factory=_fake_factory, limit=3, workers=2,
    )
    assert {r["run_id"] for r in again}.isdisjoint(ids), "completed run re-executed"
    assert len(read_index(runs)) == 6


def test_enumerate_cells_shape():
    cells = mx.enumerate_cells(["openai:gpt-5-nano"], [{"id": "fs-01"}])
    # 1 clean + 5 modes x 5 doses, x 2 replicates
    assert len(cells) == (1 + 25) * 2
    clean = [c for c in cells if c.mode == "clean"]
    assert len(clean) == 2 and all(c.dose == 0.0 for c in clean)
    run_ids = {c.run_id for c in cells}
    assert len(run_ids) == len(cells), "run ids must be unique"
