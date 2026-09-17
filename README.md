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
| stdio JSON-RPC proxy (5 fault modes, ledger, idempotency stamps) | built; offline tests |
| storm-ops server (escalate, append_ledger, dedupe-aware) | built; offline tests |
| LangGraph supervisor (plan → act → evaluate) | built; offline FakeChatModel e2e |
| 25 objectively-graded tasks + fixtures | built; grader golden 25/25 |
| Matrix runner (manifest resume, spend breaker) | built; offline gate + fake-model dry runs |
| Paid experiment matrix | blocked until `v0.1-prereg` (Fri Oct 9 2026) |
| mcp-meter report/bench, vignette, QLoRA, pgvector, deploy | later (cut order: vignette → pgvector → deploy → post) |
