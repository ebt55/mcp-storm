#!/usr/bin/env bash
# Chained paid phases, run sequentially (matrix lock is exclusive).
# Launched detached; each phase resumes via runs/index.jsonl if interrupted.
cd "$(dirname "$0")/.." || exit 1
echo "=== phase 1: deepseek exploratory remainder $(date)" >> runs/phases.log
uv run storm matrix run --models or:deepseek-v4-flash --modes clean,malformed \
  --doses 0.1,0.4 --replicates 1 --workers 4 >> runs/exploratory-deepseek.log 2>&1
echo "=== phase 2: glm exploratory slice $(date)" >> runs/phases.log
uv run storm matrix run --models or:glm-5.3-flash --modes clean,malformed \
  --doses 0.1,0.4 --replicates 1 --workers 4 >> runs/exploratory-glm.log 2>&1
echo "=== phase 3: main prereg matrix $(date)" >> runs/phases.log
uv run storm matrix run --models openai:gpt-5-nano,openai:gpt-5-mini \
  --workers 4 >> runs/matrix-main.log 2>&1
echo "=== all phases complete $(date)" >> runs/phases.log
