#!/usr/bin/env bash
#
# run.sh — reproduce the pipeline from a clean checkout.
#
#   ./run.sh setup      create venv + install pinned deps
#   ./run.sh build      compile harness (default + control + roundtrip)
#   ./run.sh baseline   run the naive Step-3 baseline through the harness
#   ./run.sh check      quick crash/reject/valid classification on known inputs
#   ./run.sh test       all four suites (harness, signal, triage, loop)
#   ./run.sh all        setup + build + test + baseline   (default)
#
# The agentic loop (Step 4) is driven separately via fuzz/loop/agent.py.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"

setup() {
  [ -d .venv ] || python3 -m venv .venv
  ./.venv/bin/pip -q install --upgrade pip >/dev/null
  ./.venv/bin/pip -q install -r requirements.txt
  echo "[run] setup ok"
}

build() {
  bash harness/build.sh
  MODE=control   bash harness/build.sh
  MODE=roundtrip bash harness/build.sh
}

check() {
  require_venv
  echo "[run] classification checks:"
  printf '{"a":[1,2,3]}' | "$PY" fuzz/runner.py build/harness      # -> valid
  printf '{"a":'         | "$PY" fuzz/runner.py build/harness      # -> reject
  printf '"__CRASH__"'   | "$PY" fuzz/runner.py build/harness_control 2>/dev/null  # -> crash
}

require_venv() {
  [ -x "$PY" ] || { echo "[run] no venv — run './run.sh setup' first" >&2; exit 1; }
}

test_all() {
  require_venv
  "$PY" fuzz/test_pipeline.py    # harness/runner branches + sanitizers
  "$PY" fuzz/test_signal.py      # proxy-signal actionability
  "$PY" fuzz/test_triage.py      # crash dedup / minimize / verify
  "$PY" fuzz/test_loop.py        # loop/regression tests from adversarial review
}

baseline() { require_venv; "$PY" fuzz/baseline_strategy.py build/harness 300; }

cmd="${1:-all}"
case "$cmd" in
  setup) setup ;;
  build) build ;;
  check) check ;;
  test) test_all ;;
  baseline) baseline ;;
  all) setup; build; test_all; baseline ;;
  *) echo "usage: $0 {setup|build|check|test|baseline|all}" >&2; exit 1 ;;
esac
