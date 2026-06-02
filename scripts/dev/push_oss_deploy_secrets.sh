#!/usr/bin/env bash
# Push the deploy-key AK from ~/.ossutilconfig to the
# GoldAnTo/fund-data GitHub repo as two repo secrets.
#
# Pre-req: `gh auth login` has been run in this shell. The two
# secret names match what .github/workflows/nightly.yml expects.
#
# We never write the AK to disk outside of `gh secret set`'s own
# encrypted transport. The values are read once from
# ~/.ossutilconfig, piped straight into gh, and immediately
# forgotten (no env-var export, no log, no shell history).
set -euo pipefail

REPO="GoldAnTo/fund-data"
CFG="${HOME}/.ossutilconfig"

ak=$(awk -F= '/^accessKeyId[[:space:]]*=/{print $2}' "$CFG" | tr -d ' \r\n')
sk=$(awk -F= '/^accessKeySecret[[:space:]]*=/{print $2}' "$CFG" | tr -d ' \r\n')

if [ -z "$ak" ] || [ -z "$sk" ]; then
  echo "error: cannot read accessKeyId/accessKeySecret from $CFG" >&2
  exit 1
fi

echo "Setting OSS_DEPLOY_KEY_ID on ${REPO}..."
printf '%s' "$ak" | gh secret set OSS_DEPLOY_KEY_ID --repo "$REPO" -

echo "Setting OSS_DEPLOY_KEY_SECRET on ${REPO}..."
printf '%s' "$sk" | gh secret set OSS_DEPLOY_KEY_SECRET --repo "$REPO" -

echo "Done. Verifying:"
gh secret list --repo "$REPO" | grep -E "OSS_DEPLOY_KEY_(ID|SECRET)" || {
  echo "warning: secrets not visible to gh (token may lack repo scope)" >&2
}
