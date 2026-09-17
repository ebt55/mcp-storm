# PREREGISTRATION — mcp-storm (v0 SKELETON, predictions only)

> Status: **DRAFT** — skeleton due Wed Sep 23 2026; **locked + tagged `v0.1-prereg`
> Fri Oct 9 2026. No paid run before the lock.** After the tag this file is
> frozen; corrections go to DEVIATIONS.md only.
> Window: prereg locked Fri Oct 9 2026 → post Fri Nov 13 2026. Hard breaker $40.

## Design (frozen at lock)

- 5 fault modes at the MCP stdio boundary, dose = P(fault per tools/call):
  latency (fixed 30s delay), hard_error, malformed, partial, dropped.
  Doses 5/10/20/40/70%, one shared dose-0 cell per model. Deterministic
  sha256(run_id:task_id:call_seq) draws.
- Models: openai:gpt-5-nano, openai:gpt-5-mini (OpenRouter declared fallback).
  25 objectively-graded tasks (9 fs / 8 sqlite / 8 cross-server), 2 replicates:
  2,600 runs total. `reasoning_effort` pinned in agent/models.py.
- Metrics: completion rate (Wilson 95% CI), token-cost multiplier, retry storms
  (≥3 consecutive identical (tool, args-hash)), P95 wall clock, escalation rate,
  $ per completed task. Breakpoint estimator: crossing dose = smallest dose
  whose Wilson upper bound < 50%. Pooled one-sided Cochran-Armitage per
  (mode × model). Refusal confound column (phrase list below), never excluded.

## Predictions (scoring table)

| Claim | gpt-5-nano | gpt-5-mini |
|---|---|---|
| C1 cost multiplier >3×, `malformed` | 10–20% | 10–20% |
| C1 >3×, `dropped` | 20–40% | 20–40% |
| C1 >3×, `hard_error` | 20–40% | 20–40% |
| C1 >3×, `partial` | 40–70% | 40–70% |
| C1 >3×, `latency` | never (token multiplier ≈1.0 at all doses) | same |
| C2 completion <50% (Wilson-UB estimator), `malformed` | 40% | 20–40% |
| C2 <50%, `dropped` | 40% | 40% |
| C2 <50%, `hard_error` | never below 70% | 70% |
| C2 <50%, `partial` | never | never |
| C2 <50%, `latency` | never below 70%; P95 wall ≥3× dose-0 at 40%+ | same |
| C3 retry-storm rate >20% of runs | `malformed` ≥20% (both) | same |
| C4 meter: MCP-arm prompt tokens ≥1.3× native-arm | confirm/direction only | — |
| C5 vignette: arm 1 (no keys) ≥50% of killed runs show ≥1 duplicate oracle append; arm 2 (keys, dedupe-aware server) 0 duplicates | after arXiv 2607.14166 | — |
| C6 QLoRA macro-F1 (3-class post-fault next-action) ≥ +0.10 over zero-shot Qwen3-8B AND ≥ +0.15 over majority class | Qwen3-8B | — |

Scoring: `confirmed / partial / refuted` against the pre-registered estimators.
"Never" predictions are confirmed by curves staying under threshold through 70%.
At n=50 per cell, below-50% is confirmable only when observed completion ≤36/50;
borderline cells score 'partial' by design.

## Exclusions pre-declared

Harness bugs (re-run cell); API 5xx >5% (re-run once); smoke + pilot; refusals =
confound column. Descope per measured cost: mini cells ≥40% dose drop to R=1 if
live spend >$30 at half-done.

## Refusal phrase list (confound column; exact substring match, case-insensitive)

- "I can't help with that"
- "I cannot help with that"
- "I'm sorry, but I can't"
- "I am sorry, but I cannot"
- "against my guidelines"
- "I'm not able to help"
