# mcp-storm — what tool failures cost your agent

**What is the price of fragility? mcp-storm injects controlled doses of five
tool-failure modes (latency, hard errors, malformed content, partial results,
dropped responses) at the MCP stdio protocol boundary under a LangGraph
supervisor, and measures completion, token-cost multiplier, retry storms, P95
wall clock, and dollars per completed task — with Wilson CIs and preregistered
predictions (tag `v0.1-prereg`).**

Headline numbers regenerate from committed records via `uv run storm report
--all`; the main matrix has not shipped yet — measured cells so far are the
smoke, pilot, and exploratory slices.

Planned headline (regenerates): *at [X]% tool-failure dose, the same tasks
cost [N.N]× more tokens and [$M] more per completed task; [K]% of runs hit
retry storms (>=3 identical consecutive calls). Completion breakpoint (smallest
dose whose Wilson-UB < 50%): [mode-ordering] — secondary endpoint, per
prereg.*

- What it is: a fault-injection proxy at the MCP boundary + the same proxy as
  a per-call cost meter (`storm wrap -- <any stdio server>` to try it on your
  own server).
- Numbers table (mode × dose × model: completion w/ Wilson CI, cost
  multiplier, storms, P95, $/task) — regenerated into `runs/summary.md`.
- Prior art (verified 2026-09-23, `prereg/PRIOR-ART.md`): robustness-under-
  tool-faults is a published genre (Lewandowska 2026; ToolMaze arXiv:2606.05806
  — implicit failures hurt most; AgentChaos arXiv:2608.06790 — LLM-response
  side; MAS-FIRE; AgentNoiseBench). **Unmeasured by any of them, and ours: the
  cost side — token-cost multiplier, retry-storm rates, and dollars-per-
  completed-task as a function of injected fault dose, through a real MCP
  stdio wire-path proxy over real reference servers.**
- Scope, stated plainly: stdio transport, pinned SDKs (mcp 1.30), reference
  servers (one vendored TS, one archived-Python — `prereg/DEVIATIONS.md`
  #12-13). The 2026-07-28 spec's remote/stateless direction is explicitly out
  of scope; per-node token costs also exist in LangSmith — mcp-storm's meter
  is the vendor-independent protocol-boundary one, and it is the same binary
  as the injector.
- Fine-tuned Qwen3-8B (QLoRA, one RTX 3080) to predict agent retry-vs-abandon
  after tool faults: macro-F1 [0.XX]→[0.YY] vs zero-shot 8B and a zero-shot
  frontier baseline row.
- Reproduce: `uv sync` → `uv run storm selftest` → `uv run storm report --all`.
- Raw data: every run's per-call ledger, meter, messages, and manifest under
  `runs/` (regenerates every number above; CSV bundle at release).

## Status

| Piece | State |
|---|---|
| stdio JSON-RPC fault-injection proxy (5 modes, per-server ledgers, idempotency stamps) | built; 4 blind adversarial review rounds passed |
| storm-ops server (escalate, durable append_ledger, dedupe-aware) | built; kill-resume dedupe verified |
| LangGraph supervisor (plan → act → evaluate, AsyncSqliteSaver) | built; offline FakeChatModel e2e + real-server bring-up |
| 25 objectively-graded tasks + strict graders | built; golden 25/25 pass + 25/25 corrupted-fail |
| Matrix runner (offline gate, resume, 4 workers, spend breaker) | built; smoke + pilot + deepseek slice measured; main matrix pending |
| Exploratory value arms (or:deepseek-v4-flash, or:glm-5.3-flash) | deepseek slice done (75 runs); glm pending |
| mcp-meter bench, kill-9 vignette, QLoRA, deploy | later (pgvector rider CUT — no differentiation in 2026) |

Verification: `uv run pytest` (115 offline tests), `uv run storm selftest`,
`uv run pytest -m realserver` (pinned reference servers through the proxy).
Bring-up deviations are recorded in `prereg/DEVIATIONS.md` (#1-29); the
prior-art position is `prereg/PRIOR-ART.md` (dated pre-main-matrix).
