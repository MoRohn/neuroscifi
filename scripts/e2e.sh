#!/usr/bin/env bash
# Browser tests for Pocket. Set NSF_CHROMIUM=/usr/bin/chromium to use a system browser instead of Playwright's.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python; fi
fi
[ -d node_modules/axe-core ] || npm ci --no-audit --no-fund
"$PY" -m pytest -q tests/e2e "$@"
