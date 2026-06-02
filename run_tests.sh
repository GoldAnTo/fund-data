#!/usr/bin/env bash
# Run the full unit-test suite from the repo root.
#
# The test files compute their own sys.path from
# `Path(__file__).resolve().parents[1]`, which points at
# `fund-data/scripts/`. To resolve `from scripts import fund_data`
# correctly when invoked from any cwd, `fund-data` itself must be
# on PYTHONPATH.
#
# Usage:
#   ./run_tests.sh                  # all 227 tests
#   ./run_tests.sh scripts.tests.test_fund_data   # one module
#   ./run_tests.sh scripts.tests.test_fund_data.TestCase.test_method
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH=fund-data
exec python3 -m unittest discover -s fund-data/scripts/tests -t . -p 'test_*.py' "$@"
