#!/usr/bin/env bash
# RUN-MODIFIED launch chain (DEVIATIONS #31-32). set -e: a gate refusal or
# hard failure ABORTS the chain loudly instead of echoing success.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1
note() { echo "=== $1 $(date)" >> runs/phases.log; }

note "phase 1: baselines (nano clean r1 remainder + deepseek xs-07)"
uv run storm matrix run --models openai:gpt-5-nano --modes clean --replicates 1 --workers 4 >> runs/matrix-baselines.log 2>&1
uv run storm matrix run --models or:deepseek-v4-flash --modes clean --replicates 1 --workers 4 >> runs/matrix-baselines.log 2>&1

note "phase 2: nano arm AS LOCKED (all modes x doses, r1+r2)"
uv run storm matrix run --models openai:gpt-5-nano --workers 4 >> runs/matrix-nano.log 2>&1

note "phase 3: mini live modes full (malformed + dropped + hard_error, r1+r2)"
uv run storm matrix run --models openai:gpt-5-mini --modes malformed,dropped,hard_error --workers 4 >> runs/matrix-mini-live.log 2>&1

note "phase 4: mini partial + latency at r1 (DEVIATIONS #31 descope)"
uv run storm matrix run --models openai:gpt-5-mini --modes partial,latency --replicates 1 --workers 4 >> runs/matrix-mini-flat.log 2>&1

note "phase 5 (tail): glm exploratory slice"
uv run storm matrix run --models or:glm-5.3-flash --modes clean,malformed --doses 0.1,0.4 --replicates 1 --workers 4 >> runs/exploratory-glm.log 2>&1

note "ALL PHASES COMPLETE"
