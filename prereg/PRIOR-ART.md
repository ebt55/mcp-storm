# PRIOR-ART ADDENDUM — mcp-storm (dated, pre-main-matrix)

> Written and committed 2026-09-23, BEFORE the main preregistered matrix
> launched (the main matrix's first run will carry a git SHA that descends
> from this commit). Purpose: record what the landscape looked like when the
> locked claims were made and where this project's measured contribution
> actually sits. This file does not modify the locked predictions.

## Verified landscape (checked 2026-09-23)

- **Lewandowska et al., "Beyond Success Rate: Benchmarking Robustness in
  Tool-Using Language Agents"** (OpenReview 3Snm5cHdRu, 2026) — injects a
  taxonomy of realistic faults (including corrupted MCP tool results, API
  errors, timeouts) into tool-use benchmarks. Closest prior for
  robustness-under-tool-faults; does NOT measure token-cost multipliers,
  retry storms, or $/completed-task, and does not inject through a
  protocol-boundary proxy.
- **ToolMaze, "When Tools Fail"** (arXiv:2606.05806) — runtime interception
  perturbs tool results; reports ~37% performance drop and that implicit
  semantic failures hurt most. Pre-publishes the qualitative ordering our C2
  table tests (partial/malformed vs hard_error); no cost axis, no MCP wire
  path, not preregistered.
- **AgentChaos** (arXiv:2608.06790, ASE 2026) — 65 fault configurations at
  the **LLM-API response interface** (model-response side). mcp-storm faults
  the **tool-response side at the MCP stdio boundary** — the two are
  complementary protocol layers, not the same boundary.
- **AgentNoiseBench** (2026) — automated noise injection for agent benchmarks.
- **MAS-FIRE** (2026), **FAIL-TALMs** (ACL 2025), **ToolMisuseBench**
  (arXiv:2604.01508), **"Retry, Switch, or Abstain?"** (arXiv:2608.11977) —
  fault injection / reliability evaluation for (multi-)agent systems.
- **agent-chaos** (github.com/deepankarm/agent-chaos) — LLM-API fault
  injection, LLM-as-judge; no MCP modes, no dose curves, no cost metrics.
- **MCP vs native token benchmarks** (saturated genre): MindStudio (35x),
  Scalekit (10-32x, open repo), OnlyCLI, Tyk, twilio-labs/mcp-te-benchmark;
  Firecrawl's rebuttal ("35x only for naive servers") is the standard reply.
- **Idempotency at the MCP boundary**: public spec discussion (May 2026) and
  middleware/blog treatments exist; no spec field yet. The MEASUREMENT of
  replay double-execution at the boundary and a proxy-level implementation
  remain unclaimed.
- **benchmark-broccoli**: NOT FOUND in any index (citation removed; it was
  carried from the guide's draft).
- **MCP spec 2026-07-28** (stateless core, Extensions, async Tasks) — remote
  HTTP is the ecosystem direction; mcp-storm's scope is explicitly the stdio
  transport on pinned SDKs.

## Where mcp-storm's measured contribution actually sits

The robustness-degradation genre (completion drops under injected faults) is
published. What no found work measures, and what this project preregistered:

1. **Token-cost multiplier as a function of injected tool-fault dose**
   (C1: >3x thresholds per mode, per model).
2. **Retry storms (>=3 consecutive identical calls) as a measured rate**,
   not just vocabulary.
3. **Dollars per completed task vs dose** on real list prices.
4. Dose-response (graded P(fault) x 5 doses) through a **real MCP stdio
   wire-path proxy wrapping real reference servers**, exercising real client
   timeouts (McpError at 45s, DEVIATIONS #1).
5. The whole thing **preregistered** (locked v0.1-prereg, 2026-09-18) with
   prediction bands scored honestly — including predicted-but-confirmed
   orderings that ToolMaze published independently after our lock.

Completion-rate breakpoints (C2) are kept as preregistered secondary
endpoints; the README leads with the cost/storm/$ numbers because they are
both ours alone and monotone by construction.
