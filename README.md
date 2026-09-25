# mcp-storm

A proxy that sits between a LangGraph agent and its MCP servers and breaks
tool calls on purpose. You pick a failure mode and a failure rate, it rewrites
`tools/call` responses accordingly, and it records everything that crosses the
wire. The output is dose-response data: what happens to completion, token
cost, retry behavior, and dollars per finished task as tool failures climb
from 5% to 70% of calls.

At 0% dose the same proxy is a meter: per-call latency, bytes, and token
accounting for every tool call, attributed per graph node.

It wraps real servers on the real stdio path. The TypeScript filesystem
reference server, the archived sqlite reference server, and a small
side-effect server written for this project. Client timeouts, framing, and
error surfaces are the ones you would hit in production.

## What it measures

Five failure modes, injected on `tools/call` responses only:

| mode | the agent gets |
|---|---|
| `latency` | the true response, 30 seconds late |
| `hard_error` | `isError: true` with realistic messages ("database is locked") |
| `malformed` | valid envelope, corrupted content (truncated JSON, array collapsed to its first item, empty text) |
| `partial` | half the data, no error signal |
| `dropped` | nothing. The 45s client timeout fires and the agent sees an `McpError` |

The fault decision is deterministic per call: `sha256(run_id:task_id:call_seq)`
compared against the dose. Any cell replays exactly. Dose is the probability
that a given tool call gets faulted: 5/10/20/40/70%.

Per cell it reports: completion rate with Wilson 95% CI, token-cost multiplier
over all runs and among completed runs (failed runs stop spending, so mixing
the two hides the retry economics), retry storms (3 or more identical
consecutive calls, with a 2-plus count as a secondary metric), timeout rate,
P95 wall time of non-timeout runs, and dollars per completed task at list
prices.

## Numbers so far

The preregistered matrix is running as of 2026-09-25. Every number regenerates
from committed records with `uv run storm report --all` into `runs/summary.md`
and `runs/cells.csv`.

One complete exploratory slice (deepseek-v4-flash over OpenRouter, 75 runs,
replicate 1):

- clean completion 48% (12/25). Malformed at 10% dose: 36%. At 40% dose: 36%.
  The response to malformed output saturates by the 10% dose. After that,
  task difficulty dominates: between-task variance runs about 29 times the
  dose variance.
- token-cost multiplier 1.33 at both doses across all runs. Among completed
  runs only: 1.58 at 10% dose against 1.04 at 40%. Agents that survive
  faults keep spending. Agents that give up stop.
- retry storms (3+ identical consecutive calls): 0 of 25 runs at 10% dose,
  4 of 25 at 40%.

Total spend to date: $0.16 of real money (the OpenRouter arm) plus $0.56 gross
on the credit-covered OpenAI arms.

## The 25 tasks

Nine filesystem, eight sqlite, eight cross-server. Graded by plain Python
assertions on the sandbox: file contents, exact lines, numbers parsed as
numbers, SQL rows compared as multisets. No LLM judging. A numeric answer is
graded numerically, so writing 14550 does not pass when the answer is 4550.
The answer key is hand built (`tests/golden/solutions.py`). All 25 perfect
sandboxes pass. All 25 corrupted sandboxes fail.

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

Predictions, estimator definitions, the refusal phrase list, and exclusions
were locked and tagged `v0.1-prereg` before the first paid run. Everything
since is recorded in `prereg/DEVIATIONS.md`: a stale price table, a prereg
power note that did not add up, an evaluator bug the pilot exposed, four
scoring bugs a hostile review found. Read that file before you trust a
number here.

Prior art is in `prereg/PRIOR-ART.md`. Agents degrading under tool faults is
a published genre (Lewandowska 2026, ToolMaze, AgentChaos at the LLM response
layer, MAS-FIRE). None of them measured the cost side. This project measures
token multiplier, retry storms, and dollars per completed task against fault
dose, preregistered, at the protocol boundary.

Scope: stdio transport, pinned SDKs (mcp 1.30), LangGraph, two preregistered
models plus exploratory flash arms. Remote MCP is out of scope. Completion
breakpoints are the soft endpoint here. Small cells and a saturating response
limit what they can tell you. The cost curves are the point.

## Layout

```
src/mcp_storm/proxy/     stdio JSON-RPC proxy: framing, 5 fault modes, ledgers, idempotency stamps
src/mcp_storm/agent/     plan -> act -> evaluate supervisor, per-node token meter
src/mcp_storm/runner/    matrix runner: offline gate, resume, spend breakers, watchdog
src/mcp_storm/grading/   objective graders + outcome classification
src/mcp_storm/analysis/  frozen estimators (Wilson CI, crossing dose, Cochran-Armitage,
                         retry storms) + prereg claim scoring, tested on synthetic curves
prereg/                  locked predictions, prior-art notes, 34-entry deviations log
tasks/                   25 task definitions + fixtures + the frozen system prompt
```
