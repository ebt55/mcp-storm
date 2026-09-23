# DEVIATIONS — observed departures from BUILD-GUIDE v2

Locked prereg edits forbidden after `v0.1-prereg` (Fri Oct 9 2026); this file is
the permanent correction log. Entries dated; day-1 falsification results live here.

## 2026-09-17/18 — day-1 falsification + bring-up (before prereg lock)

1. **Dropped-mode client behavior (§3.6, required recording).** With the pinned
   `mcp` 1.30.0 SDK, a `tools/call` that never gets a response surfaces inside
   the client as **`mcp.types.McpError`** wrapping
   `"Timed out while waiting for response to ClientRequest. Waited 45.0 seconds."`
   — NOT `TimeoutError`. Verified at read_timeout_seconds=45: McpError raised at
   ~45.0s (≤50s bound from §3.6 holds; no redesign of `dropped` needed).
   This exception is what `dropped` means to the agent; through
   langchain-mcp-adapters it propagates out of the tool coroutine and ToolNode
   (handle_tool_errors=True default) converts it to an error ToolMessage.

2. **`ClientSession(read_timeout_seconds=...)` requires `datetime.timedelta`**
   in mcp 1.30.0; a float raises `AttributeError` inside `send_request`.

3. **langchain-mcp-adapters 0.3.2 stdio config shape (§4).** There is no
   `timeout` key on a stdio connection. The 45s client timeout is configured as
   `"session_kwargs": {"read_timeout_seconds": timedelta(seconds=45)}`. Verified
   against installed package source, 2026-09-17.

4. **langchain-mcp-adapters 0.3.2 `get_tools()` is per-call-session (§4).**
   `MultiServerMCPClient.get_tools()` spawns a NEW stdio session (new subprocess
   of proxy+server) for every tool call, which would reset `call_seq` to 1 each
   call and degenerate the §3.3 sha256 keying (all-or-nothing faulting per run).
   Fix: persistent sessions — `async with AsyncExitStack() as stack:
   session = stack.enter_async_context(client.session(name));
   tools = await load_mcp_tools(session, server_name=name)` per server. The
   §3.1 topology (proxy ×3, per-run processes) is preserved.

5. **Windows CRLF from the mcp stdio server (§3.5).** The pinned SDK server
   terminates frames with `\r\n` on Windows (text-mode stdout translation).
   Proxy behavior: untouched frames forwarded byte-for-byte (CRLF preserved);
   proxy-generated envelopes end with `\n`. Byte-exact golden test compares
   direct vs proxied full streams and covers both.

6. **Proxy stderr deadlock hardening (§3.5.2).** Teing every child-stderr line
   to the proxy's own stderr can fill an undrained stderr pipe; the tee thread
   then blocks holding the ledger lock and deadlocks the whole pump (found in
   the 1,000-message soak). Fix: child-stderr lines go to `proxy.log` (a file)
   only; lifecycle events still tee to stderr. Production clients drain stderr,
   but the proxy no longer depends on it.

7. **`--spawn-args` parsing.** argparse `nargs="+"` cannot accept child argv
   starting with `-` (e.g. `python -m ...`); the proxy uses `argparse.REMAINDER`
   and `--spawn-args` must be the last flag. Documented in `--help`.

8. **Per-server ledger files (adversarial review, 2026-09-18).** Windows
   O_APPEND writes are seek-then-write with no cross-process atomicity (3
   proxies appending one shared proxy.jsonl produced torn lines under load).
   Each proxy process therefore writes `proxy.<server>.jsonl` /
   `proxy.<server>.log` exclusively; readers merge + sort by `t_request_ns`
   (`mcp_storm.proxy.ledger.read_run_ledger`). §3.4's single-file wording is
   satisfied at the run-directory level.

9. **Wall-clock ledger stamps (adversarial review).** `t_request_ns` /
   `t_response_ns` / `t_child_ns` are `time.time_ns()` (epoch), not
   `perf_counter_ns`, so §5.1's meter join (nearest timestamp ±2s) and any
   post-hoc timeline work across artifacts.

10. **Fault-key hygiene (adversarial review).** STORM_SEED extends the spec key
    as `f"{run_id}:{task_id}:{call_seq}:{seed}"` (empty seed byte-identical to
    spec); run_id/task_id/seed containing `:` are rejected at config load.
    Known and accepted: the spec's key has no server term, so the three
    per-server proxies of a run draw identically at equal call_seq (correlated
    fault decisions across servers); this is the preregistered formula.

11. **Envelope hygiene (adversarial review).** malformed/partial of a child
    JSON-RPC error envelope fold the error message into the text block and emit
    a content-only envelope (never `error`+`result` both); both transforms
    reduce content to the single corrupted text block (no leak via sibling
    blocks or structuredContent). Latency holds get their full delay as
    teardown grace; holds lost to hard kills are counted in
    `manifest.counters.latency_holds_lost_at_teardown`.

## 2026-09-18 — real-server bring-up (§3.6, §5.2)

12. **The official filesystem server is TypeScript-only.** The Python
    `mcp-server-filesystem` package is gone from the registry (the guide's
    `uvx mcp-server-filesystem@<pin>` is stale). Fix: the pinned npm package
    `@modelcontextprotocol/server-filesystem@2025.7.1` is vendored under
    `vendor/filesystem/` and spawned as `node <dist/index.js> <sandbox>` —
    still a direct .exe invocation, no npx, no shell. Positional allowed-dir
    args verified live. Its tool is `read_file` (not the old Python server's
    `read_text_file`); task authoring and the offline fake server use the real
    names. The TS server does NOT create parent directories on `write_file`.

13. **The official sqlite server is archived and SDK-rotted.**
    `mcp-server-sqlite@2025.4.25` crashes under the current mcp SDK
    (`'Server' object has no attribute 'list_resources'`). Fix: its uvx
    environment pins the era's SDK — `uvx --with mcp==1.9.4
    mcp-server-sqlite@2025.4.25 --db-path <sandbox>/task.db`. `--db-path`
    arg verified live.

14. **Real TS filesystem server resolves relative tool paths against its own
    cwd**, so the proxy takes `--spawn-cwd <sandbox>` (env `STORM_SPAWN_CWD`)
    and the runner sets it to the run's sandbox; otherwise relative paths from
    task prompts resolve into the repo and are denied by the allowed-dirs
    check.

15. **storm-ops `_meta` access (mcp 1.30 bundled FastMCP).** Inside tool
    handlers `ctx.request_context.request` is None; the stamped idempotency key
    is on `ctx.request_context.meta` (a pydantic Meta model, key in
    `model_extra`). Proxy stamping itself verified byte-level on the wire.

16. **Proxy shutdown uses `os._exit`.** Daemon pump threads hold the stdin
    buffered-reader lock; normal interpreter finalization fatal-errors
    (`_enter_buffered_busy`). All frames are flushed per write and the ledger
    is fsynced in `Ledger.close()` before the exit, so skipping finalization
    is safe and silences the fatal error.

## 2026-09-18 — runner hardening (adversarial review, pre-launch)

17. **Node attribution via tags.** langgraph 1.2 does not surface
    `langgraph_node` in callback metadata for handlers attached at the graph
    level; per-call `metadata` does not propagate up either. The plan/evaluate
    nodes stamp `config={"tags": ["storm-node:<name>"]}` on the model call
    (tags DO propagate to inherited callbacks); MeterCallbackHandler parses
    the `storm-node:` tag. Verified through the real graph (§5.1 per-node
    attribution).

18. **Proxy startup manifests.** The per-proxy manifest is now written the
    moment the child spawns (proxy_pid + child_pid) and rewritten at graceful
    exit with frame counters. A hard-killed proxy never reaches close(), and
    the runner's watchdog (`taskkill /F /T`) needs pids on disk immediately.
    Graceful-exit counters remain the frame-equality source (§3.4); startup
    manifests exist for kills.

19. **Cancellation is not a dataset row.** Ctrl-C/drain (asyncio
    CancelledError) kills recorded process trees and re-raises WITHOUT
    appending index.jsonl -- resume retries the run. Previously up to 4
    in-flight runs per interrupt were permanently recorded `crashed` and then
    skipped by resume.

20. **Re-running a run_id wipes its directory first** (checkpoints, ledgers,
    sandboxes, artifacts). Re-runs (§7 harness-bug exclusion) previously
    inherited poisoned threads, leftover agent artifacts, and appended
    per-proxy ledgers, corrupting both the grade and retry-storm metrics.

21. **Worker isolation + record salvage.** Matrix workers catch per-run
    exceptions (a poisoned run logs HARD-FAILED and the matrix continues);
    refusal phrases load strictly BEFORE any run starts (fail fast).
    messages.json for timeout/loop/crashed runs is harvested from the
    AsyncSqliteSaver checkpointer before teardown -- the QLoRA task needs
    agent-side text precisely for fault-heavy runs. read_index tolerates torn
    tail lines. The offline gate refuses dirty working trees. One-matrix lock
    file prevents double-spend from concurrent windows. Zero-tool runs are not
    join failures (flagged `zero_tool_run`).

22. **Spend accounting notes.** Worst-case breaker overshoot with 4 in-flight
    mini runs is bounded ≈ $1.44 by recursion_limit 40 + the 600s wall (total
    < $42, under the $50 cap). The pilot, vignette, and mini-benchmark MUST
    route through `run_matrix` (or record into spend.jsonl) -- direct `run_one`
    calls bypass the breaker by design (offline tests).

## 2026-09-18 — pre-launch corrections + early lock

23. **gpt-5-nano list price corrected.** Guide §6.2 assumed $0.06/$0.48 per M;
    the live table (developers.openai.com, fetched 2026-09-18) is
    $0.05 in / $0.005 cached / $0.40 out. Budget arithmetic improves ~15%.
    gpt-5-mini $0.25/$0.025/$2.00 matches the guide exactly.

24. **Power note corrected.** The guide's "at n=50 per cell, below-50% is
    confirmable only when observed completion <= 36/50" is arithmetically
    wrong for the preregistered estimator (Wilson UB < 50%): 36/50 has
    UB ≈ 0.83. The correct threshold is **<= 18/50** (18/50 UB ≈ 0.494;
    19/50 UB ≈ 0.517). The estimator itself is unchanged; the note in
    predictions.json now carries the corrected threshold.

25. **Prereg locked early (2026-09-18, not Oct 9), with owner consent.**
    Locking earlier only strengthens prereg (predictions frozen further from
    any data). Preregistered pair unchanged (gpt-5-nano + gpt-5-mini).
    Owner-approved EXPLORATORY arms added before the lock: OpenRouter
    or:deepseek-v4-flash ($0.05/$0.10 per M) and or:glm-5.3-flash
    ($0.075/$0.25 per M) — dose-0 + malformed slices only, never scored
    against prereg claims. Owner holds OpenAI data-sharing credits and an
    OpenRouter key; spend.jsonl records gross list cost as the ceiling.

26. **Pre-pilot gate interpretation (written BEFORE pilot data, 2026-09-18).**
    The Oct-22 pilot gate "dose-0 completion >=90%" exists to catch HARNESS
    bugs. Model-capability failures are not harness bugs: the real filesystem
    server's path validation rejects creating nested dirs whose parent chain
    is missing (agents must create `out/` before `out/reports`; verified live
    -- nano escalated on this once, mini/deepseek/glm adapted and completed).
    If gpt-5-nano's dose-0 completion lands <90% WITHOUT any harness defect
    (all failures graded, no crashes/timeouts caused by tooling), the verdict
    is 'model-capability baseline', recorded here with the dose-0 rate; the
    matrix proceeds because every metric is RELATIVE to that model's own
    dose-0 cell. Harness-caused failures still trip the gate and block.

27. **Fake-server fidelity fix (create_directory).** The offline fake created
    parents recursively; the real TS server's create_directory fails when the
    parent chain does not exist (validatePath realpath). Fake now matches
    (offline e2e and matrix runs see identical filesystem semantics).

28. **Harness fix after pilot (§7 harness-bug exclusion invoked; pilot cells
    excluded and re-run).** Pilot evidence: gpt-5-mini completed every tool
    step of db-01 (table, 3 rows, verified count, directory) and db-02 (all
    inputs read) and the evaluator then answered "done" BEFORE the required
    output artifact existed -> wrong_answer. Same signature drove gpt-5-nano's
    17% dose-0 completion. Root cause: the evaluator prompt did not require
    artifact verification before "done" -- an authoring defect in the harness
    (the supervisor design's evaluate node, §5.1), not a preregistered
    variable. Fix: evaluate prompt now requires verifying every required
    artifact before answering done (graph code only; tasks/PROMPT.md and the
    task set untouched). Pilot cells (nano 75 + mini 5) excluded via
    scripts/exclude_runs.py and re-run from scratch. Nano's low completion may
    persist (capability floor, #26) -- the re-run separates the two causes.

## 2026-09-23 — pre-main-matrix impact review (external, hostile)

29. **Framing correction (pre-data, main matrix NOT yet launched).** A hostile
    prior-art review (verified citations in prereg/PRIOR-ART.md) found the
    robustness-under-tool-faults genre is now published (Lewandowska 2026;
    ToolMaze arXiv:2606.05806; AgentChaos arXiv:2608.06790 faults the
    model-response side; MAS-FIRE; AgentNoiseBench), so the guide §17 "first
    ... completion characterization" wording would die under scrutiny.
    Corrections applied BEFORE the main matrix: (a) the project's first-claim
    is re-aimed to the unmeasured cost corner -- token-cost multiplier,
    retry-storm rates, $/completed-task vs dose, at the MCP stdio boundary,
    preregistered; (b) benchmark-broccoli citation removed (unfindable in any
    index); (c) AgentChaos differentiation reworded to tool-response side vs
    model-response side; (d) README leads with cost/storm/$ numbers,
    completion breakpoint demoted to secondary (it is also the statistically
    soft endpoint per the corrected power note #24); (e) "reference servers"
    replaces "real servers" externally; (f) scope statement for stdio vs the
    2026-07-28 remote spec. Locked predictions, tasks, and harness are
    UNCHANGED. SHOULD-tier corrections queued: storm wrap quickstart, QLoRA
    frontier-baseline row, vignette reframe to measurement+spec-discussion,
    pgvector rider cut (done in README status).

## 2026-09-23 — design review (internal, hostile) → RUN-MODIFIED plan

30. **Scorer fixes (analysis code only; predictions.json untouched).** The
    frozen prereg_checks had four defects found by hostile review with
    synthetic data: (a) C1 multipliers shared one dict across models — the
    last-iterating model (including EXPLORATORY arms) overwrote the
    preregistered pair's curves; (b) C3 storm rates took the last dose cell's
    rate (order-dependent verdicts) instead of pooling; (c) the latency band
    string "never-below-70%; P95>=3x-dose0-at-40%+" crashed float parsing
    (sub-claims are now split off and reported separately); (d) the
    "straddles 0.5 → partial" power rule was unreachable (a fired crossing
    has UB<0.5 by definition) — replaced with the reachable rule: a fired
    crossing whose own cell rate exceeds 0.30 scores partial (at n=50,
    18/50=0.36 fires weakly; the 80%-power zone is ≤0.30). Aggregate metrics
    upgraded: cost_multiplier_completed (isolates retry economics from
    failure truncation), timeout_rate + P95-of-non-timeout (P95 was a
    600s-cap indicator), retry2_runs (>=2-consecutive secondary; locked C3
    stays >=3), all dollars labeled GROSS LIST. 120 offline tests green
    incl. regression tests for every defect.

31. **Run-plan modification, declared BEFORE mini fault cells or nano fault
    cells run (only clean baselines + deepseek exploratory exist in the
    index).** Power analysis on early data: (a) C1 >3x multipliers are
    structurally capped by recursion_limit=40 (~2-3.5x ceiling observed) —
    verdicts will be scored honestly, expected refuted-by-cap; (b) malformed
    saturates at <=10% dose for deepseek (36% vs 36% at 10/40, McNemar
    p=0.64, task-variance 29x dose-variance) — the interesting economics are
    expected in dropped/hard_error (explicit error ToolMessages are what
    agents actually retry). Plan: nano runs AS LOCKED (predictions must score
    against the frozen pair; its verdicts are annotated floor-confounded per
    #26 — the floor is a reportable capability baseline); mini malformed +
    dropped + hard_error run full 5-dose x 25 x 2 (the live claims); mini
    partial + latency descoped to r=1 (their "never" bands remain confirmable
    at n=25 — a true 50% rate has Wilson UB 69% — and crossings become
    partial-by-low-n, which is honest); if spend < $15 at the half-way
    check, add replicates on mini malformed {5,10,40} where the only observed
    gradient (0→10%) lives. Exploratory arms never enter prereg scoring.

32. **Phases-launcher incident (2026-09-18).** The first chained launcher
    ran all three phases in 6 seconds: each `storm matrix run` refused on
    the offline gate (dirty tree — the launcher script itself was
    uncommitted) and the shell script echoed success regardless. The glm
    arm and the main matrix never ran; deepseek clean is n=24 (xs-07 never
    recorded — re-queued). Fix: gate refusal now aborts the chain
    (set -e), and launches verify index growth before reporting success.

## 2026-09-24 — budget regime change (owner directive: free-credit plan)

33. **Real-money budget closed to ~zero; matrix continues on provider
    credits.** Owner directive 2026-09-24: the plan's budget is free credits
    (OpenAI data-sharing program), not cash. Changes, effective immediately:
    (a) OpenRouter arms are CLOSED at sunk cost -- real out-of-pocket to date
    \$0.164 (deepseek slice 79 runs incl. its 3 smoke; glm 3 smoke runs only;
    the glm tail is cancelled; deepseek clean stays n=24 with xs-07 never
    recorded -- noted, cell reported at its achieved n); (b) all further runs
    use the preregistered OpenAI pair only, whose usage is credit-covered --
    nano AS LOCKED (already partially run) and the RUN-MODIFIED mini phases
    (#31) are unchanged in design; (c) a separate REAL-MONEY breaker now
    gates the matrix: hard abort at \$1.00 real (warn \$0.50), computed from
    index records with OpenRouter models as the only real-money lines, in
    addition to the \$40 gross-list tracker; (d) sustained API rate limiting
    (5+ consecutive 429-class crashes) aborts the chain before data
    pollution, protecting the free-limit quota; crashed-run records now
    carry the exception text. Gross-list costs continue to be tracked and
    reported for transparency -- they are ledger entries, not out-of-pocket.
