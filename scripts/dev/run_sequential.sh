#!/usr/bin/env bash
# Sequential per-fund runner for a single dataset, using
# the per-dataset subcommand (bonds / industries / holdings)
# so a NAV failure on one fund does not abort the whole run.
#
# Args (positional):
#   1. path to codes file (one fund_code per line)
#   2. subcommand: bonds | industries | holdings
#   3. log path
set -u
CODES="$1"
CMD="$2"
LOG="$3"

# Resolve project root from this script's location.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ="$(cd "$SCRIPT_DIR/../.." && pwd)"

TOTAL=$(wc -l < "$CODES")
i=0
while read -r code; do
  i=$((i+1))
  echo "[$i/$TOTAL] fund=$code"
  PYTHONPATH="$PROJ/fund-data" \
  FUND_DATA_DB="$PROJ/fund-data/data/fund_data.sqlite" \
  "$PROJ/.venv-akshare/bin/python3" -u "$PROJ/fund-data/scripts/fund_cli.py" "$CMD" "$code" \
    --provider akshare --report-year 2024 \
    >> "$LOG" 2>&1 || echo "FAIL on $code (see $LOG)"
done < "$CODES"
echo "done"
