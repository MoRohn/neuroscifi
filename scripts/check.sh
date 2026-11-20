#!/usr/bin/env bash
# One-command quality gate. Exits nonzero on the first failure.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python; fi
fi
echo "== ruff"; "$PY" -m ruff check .
echo "== pytest"; "$PY" -m pytest -q --ignore=tests/e2e
echo "== node"
for f in tests/js/*.mjs; do echo "-- $f"; node "$f"; done
echo "== reproduce"; "$PY" scripts/reproduce.py
echo "ALL CHECKS PASSED"
