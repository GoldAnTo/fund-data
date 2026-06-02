#!/usr/bin/env bash
# Watch the three sequential runners; when all of them have
# exited, build a fresh query bundle and publish it to the
# project OSS bucket.  One-shot: exits after one successful
# publish so the cron `local-fill-backup-monitor` can take
# over the next round.
#
# Usage:
#   nohup scripts/dev/finalize_and_publish.sh > /tmp/finalize.log 2>&1 &
set -u

# Resolve project root from this script's location so the path
# does not need to be hard-coded for one operator's machine.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ="$(cd "$SCRIPT_DIR/../.." && pwd)"
DB="$PROJ/fund-data/data/fund_data.sqlite"
PYTHONPATH="$PROJ/fund-data"
export PYTHONPATH
export FUND_DATA_DB="$DB"

VERSION=$(date -u +%Y-%m-%dT%H%M)
BUNDLE_DIR=/tmp/bundle-${VERSION}
mkdir -p "$BUNDLE_DIR"

# Wait until all 3 run_sequential processes are gone.
echo "[finalize] waiting for run_sequential.sh to exit..."
while pgrep -fl "run_sequential" > /dev/null; do
  sleep 30
done
echo "[finalize] all 3 runners exited at $(date -u +%H:%M:%S)"

# Build
echo "[finalize] build-bundle version=$VERSION"
"$PROJ/.venv-akshare/bin/python3" -u \
  "$PROJ/fund-data/scripts/fund_cli.py" cloud build-bundle \
    --source-db "$DB" \
    --output-dir "$BUNDLE_DIR" \
    --base-url "https://fund-data-public-l.oss-cn-shanghai.aliyuncs.com/fund-data/releases/${VERSION}/" \
    --version "$VERSION" \
    --manifest-output "$BUNDLE_DIR/manifest.json" \
    --output "$BUNDLE_DIR/build.json" > /tmp/finalize-build.log 2>&1
if [ $? -ne 0 ]; then
  echo "[finalize] build failed; see /tmp/finalize-build.log"
  exit 1
fi

# Upload
echo "[finalize] uploading to oss://fund-data-public-l/fund-data/releases/${VERSION}/"
ossutil cp "$BUNDLE_DIR/fund_data_query.sqlite.gz" \
  "oss://fund-data-public-l/fund-data/releases/${VERSION}/" 2>&1 | tail -2
ossutil cp "$BUNDLE_DIR/fund_data_query.sqlite.gz.sha256" \
  "oss://fund-data-public-l/fund-data/releases/${VERSION}/" 2>&1 | tail -2
ossutil cp "$BUNDLE_DIR/manifest.json" \
  "oss://fund-data-public-l/fund-data/current/manifest.json" -f 2>&1 | tail -2

# Verify current/manifest.json
echo "[finalize] current/manifest.json now points to:"
curl -s "https://fund-data-public-l.oss-cn-shanghai.aliyuncs.com/fund-data/current/manifest.json" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('  version:', d['version'])"

echo "[finalize] done at $(date -u +%H:%M:%S)"
