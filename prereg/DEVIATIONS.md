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
