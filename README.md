# mcp-storm — where do agents break when their tools do?

**Headline numbers regenerate from committed records via `uv run storm report --all`.
No measured runs exist yet (prereg locks Fri Oct 9 2026; no paid run before that tag).**

- [XX]% of MCP tool calls can fail before gpt-5-mini's completion drops below 50%
  (malformed: [YY]%, 95% CI [lo–hi]). At [ZZ]% dose, malformed results make the token bill
  [N.N]× baseline. Kill-9 between tool call and checkpoint: [K]% of killed runs double-fire
  (storm-ops); 0 duplicate appends with proxy idempotency keys on a dedupe-aware server.
- What it is: a fault-injection proxy at the MCP boundary + the same proxy as a cost meter.
- Numbers table (mode × dose × model: completion w/ Wilson CI, cost multiplier, storms, P95, $/task).
- MCP vs native tool calls: [R]× prompt tokens, +[M]ms median (2 servers × 10 tasks, schema-matched).
- Fine-tuned Qwen3-8B (QLoRA, one RTX 3080) to predict agent retry-vs-abandon after tool faults:
  macro-F1 [0.XX]→[0.YY].
- Reproduce: `uv sync` → `uv run storm selftest` → `uv run storm report --all`.
- Prior art and what this is not: see BUILD-GUIDE §17 (agent-chaos, AgentChaos arXiv 2608.06790,
  benchmark-broccoli, MindStudio 35×, *Stop Means Stop* arXiv 2607.14166).

## Status

| Piece | State |
|---|---|
| stdio JSON-RPC proxy (5 fault modes, per-server ledgers, idempotency stamps) | **built; 4 blind adversarial review rounds passed** |
| storm-ops server (escalate, durable append_ledger, dedupe-aware) | built; kill-resume dedupe verified |
| LangGraph supervisor (plan → act → evaluate, AsyncSqliteSaver) | built; offline FakeChatModel e2e + real-server bring-up |
| 25 objectively-graded tasks + strict graders | built; golden 25/25 pass + 25/25 corrupted-fail |
| Matrix runner (offline gate, resume, 4 workers, spend breaker) | built; fake-model dry runs green; `storm selftest` green |
| Paid experiment matrix | blocked until `v0.1-prereg` tag (Fri Oct 9 2026) — enforced in code |
| mcp-meter bench, vignette, QLoRA, pgvector, deploy | later (cut order: vignette → pgvector → deploy → post) |

Verification: `uv run pytest` (107 offline tests), `uv run storm selftest`,
`uv run pytest -m realserver` (pinned real servers through the proxy).
Bring-up deviations (official servers went TS-only / archived upstream) are
recorded in `prereg/DEVIATIONS.md` ##1-22.
