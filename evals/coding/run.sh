#!/bin/bash
# Sage V6 coding-harness benchmark (informational, not a CI gate).
# Usage: bash evals/coding/run.sh
# Runs 10 deterministic scenarios with a ScriptedApiClient and writes a report
# under evals/coding/results/. Exits 0 regardless of pass/fail.
set -u
cd "$(dirname "$0")/../.."
python -m evals.coding.runner
