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
