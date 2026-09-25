#!/usr/bin/env bash
# FREE-CREDIT launch chain (DEVIATIONS #33): OpenAI models only (owner's
# data-sharing credits cover usage); NO OpenRouter phases (real money closed).
# set -e: a gate refusal or hard failure aborts loudly.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1
note() { echo "=== $1 $(date)" >> runs/phases.log; }

note "phase A: nano arm AS LOCKED (all modes x doses, r1+r2) -- credit-covered"
uv run storm matrix run --models openai:gpt-5-nano --workers 4 >> runs/matrix-nano.log 2>&1

note "phase B: mini live modes full (malformed + dropped + hard_error, r1+r2)"
uv run storm matrix run --models openai:gpt-5-mini --modes malformed,dropped,hard_error --workers 4 >> runs/matrix-mini-live.log 2>&1

note "phase C: mini partial + latency at r1 (DEVIATIONS #31 descope)"
uv run storm matrix run --models openai:gpt-5-mini --modes partial,latency --replicates 1 --workers 4 >> runs/matrix-mini-flat.log 2>&1

note "phase D: glm exploratory tail (owner-approved <=\ real; expected ~\/usr/bin/bash.3)"
uv run storm matrix run --models or:glm-5.3-flash --modes clean,malformed --doses 0.1,0.4 --replicates 1 --workers 4 >> runs/exploratory-glm.log 2>&1

note "ALL PHASES COMPLETE"
