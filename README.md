# mcp-storm

A proxy that sits between a LangGraph agent and its MCP servers and breaks
tool calls on purpose, at controlled rates, while recording everything that
crosses the wire. You get dose-response numbers: how completion, token cost,
retry behavior, wall time, and dollars per finished task change as tool
failures go from 5% to 70% of calls.

The same proxy at 0% dose is a meter: per-call latency, bytes, and token
accounting for every tool call, attributed per graph node.

It wraps real servers on the real stdio wire path — filesystem (TypeScript
reference server), sqlite (archived Python reference server), and a small
side-effect server of ours — so client timeouts, framing, and error surfaces
are the ones you'd hit in production, not simulations of them.

## What it measures

Five failure modes, injected on `tools/call` responses only:

| mode | the agent gets |
|---|---|
| `latency` | the true response, 30 seconds late |
| `hard_error` | `isError: true` with realistic messages ("database is locked") |
| `malformed` | valid envelope, corrupted content (truncated JSON, array collapsed to first item, empty text) |
| `partial` | half the data, no error signal |
| `dropped` | nothing. The 45s client timeout fires and the agent sees an `McpError` |

Fault decisions are deterministic per call: `sha256(run_id:task_id:call_seq)`
against the dose, so any cell replays exactly. Dose = P(fault per tool call),
5/10/20/40/70%.

Metrics per cell: completion rate with Wilson 95% CI, token-cost multiplier
(all runs, and among-completed — failures truncate context, and mixing the
two hides the retry economics), retry storms (>=3 identical consecutive
calls; >=2 reported as secondary), timeout rate, P95 wall of non-timeout
runs, and dollars per completed task at list prices.

## Numbers so far

The full preregistered matrix is running as of 2026-09-25; every number
regenerates from committed records with `uv run storm report --all`
(aggregates land in `runs/summary.md` and `runs/cells.csv`).

One complete exploratory slice (deepseek-v4-flash via OpenRouter, 75 runs,
preregistered tasks, replicate 1):

- clean completion 48% (12/25); malformed 10% dose 36%, 40% dose 36% — the
  failure response saturates by 10% dose and task difficulty dominates after
  that (between-task variance ~29x the dose variance)
- token-cost multiplier 1.33 at both doses among all runs; 1.58 vs 1.04
  among completed runs at 10% vs 40% — agents that survive faults burn
  tokens, agents that give up stop spending
- retry storms (>=3 identical consecutive calls): 0/25 runs at 10% dose,
  4/25 at 40%

Total spend to date: $0.16 of real money (OpenRouter exploratory arm) plus
$0.56 gross on the credit-covered OpenAI arms.

## The 25 tasks

Nine filesystem, eight sqlite, eight cross-server. All graded by pure Python
assertions on the sandbox — file contents, exact lines, numbers parsed as
numbers, SQL rows compared as multisets. No LLM judging. Numeric answers are
graded numerically: `14550` does not pass for max=4550. The answer key is
hand-built (`tests/golden/solutions.py`); 25/25 perfect sandboxes pass, 25/25
corrupted sandboxes fail.

## Try it on your own server

```bash
uv sync
uv run pytest                        # 120 offline tests
uv run storm selftest                # framing + grader + meter + spend goldens

# wrap any stdio MCP server (dose 0 = pure meter):
python -m mcp_storm.proxy --spawn-cmd uvx --spawn-args <server-and-args>   # env: STORM_*

# the experiment:
uv run storm matrix gate             # offline suite green on a clean tree
uv run storm matrix run --models openai:gpt-5-nano,openai:gpt-5-mini
uv run storm report --all
```

## Rigor

Predictions, estimator definitions, refusal phrase list, and exclusions were
locked and tagged `v0.1-prereg` before the first paid run. Corrections and
every judgment call since — 34 entries, including a stale-price fix, a
prereg power-note that was arithmetically wrong, an evaluator bug found by
pilot data, and four scoring bugs found by hostile review — are in
`prereg/DEVIATIONS.md`. The deviations log is honest; read it before trusting
any number here.

Prior art is in `prereg/PRIOR-ART.md`. Short version: agents-degrade-under-
tool-faults is a published genre (Lewandowska 2026; ToolMaze; AgentChaos at
the LLM-response layer; MAS-FIRE). What nobody had measured, and what this
repo measures, is the cost side — token multiplier, retry storms, dollars
per completed task against fault dose, at the protocol boundary,
preregistered.

Scope: stdio transport, pinned SDKs (mcp 1.30), one framework (LangGraph),
two preregistered models plus exploratory flash-tier arms. The 2026-07
spec's remote direction is out of scope. Completion breakpoints are the
soft endpoint here (small cells, saturating response); the cost curves are
the point.

## Layout

```
src/mcp_storm/proxy/     stdio JSON-RPC proxy: framing, 5 fault modes, ledgers, idempotency stamps
src/mcp_storm/agent/     plan -> act -> evaluate supervisor, per-node token meter
src/mcp_storm/runner/    matrix runner: offline gate, resume, spend breakers, watchdog
src/mcp_storm/grading/   objective graders + outcome classification
src/mcp_storm/analysis/  frozen estimators (Wilson CI, crossing dose, Cochran-Armitage,
                         retry storms) + prereg claim scoring, tested on synthetic curves
prereg/                  locked predictions, prior-art position, 34-entry deviations log
tasks/                   25 task definitions + fixtures + the frozen system prompt
```
