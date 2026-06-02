# Local Fill-Missing — macOS Sequential Backfill

This playbook covers the case where a developer on **macOS** needs to
backfill the actionable-missing rows for stock_holdings /
bond_holdings / industry_allocations against the local full
SQLite (the 5.4 GB `fund-data/data/fund_data.sqlite`).

## Why a separate playbook from `data-fill-missing.yml`?

`data-fill-missing.yml` runs on a Linux GitHub Actions runner.
It is the canonical fill pipeline and should be the default
choice. The macOS local runner exists because:

- **P2P-CDN pin** — on a home network, the AkShare-backed
  Eastmoney hostname can resolve to a LAN-resident IP
  (`192.168.1.252`) that SYN-parks forever. The Linux runner
  is unaffected because it has no such local resolver. The
  multi-thread `fund_cli batch-sync` reads the DNS answer
  *once* and pins the session to that IP for the lifetime of
  the process, so once the LAN answer is cached the whole
  process stalls on `SYN_SENT`.
- **libmini_racer crash** — `fund_cli batch-sync
  --concurrency 4` against AkShare triggers a native
  `libmini_racer.dylib` assertion failure
  (`Check failed: !pool->IsInitialized()`) on Python 3.13 +
  macOS + multi-thread. Concurrency 1 still crashes, so the
  thread count is not the root cause -- a fresh process per
  fund sidesteps it.

## The two scripts

Both live in `scripts/dev/` and resolve the project root
from `BASH_SOURCE[0]`, so they work no matter where the
operator's checkout lives.

### `run_sequential.sh`

```bash
# Per-dataset sequential runner. One process per fund via the
# per-dataset subcommand (bonds / industries / holdings) so
# a single NAV failure cannot abort the run.
scripts/dev/run_sequential.sh /tmp/missing-bond.txt bonds /tmp/seq-bond.log
```

The three recommended concurrent runs:

```bash
nohup scripts/dev/run_sequential.sh /tmp/missing-bond.txt bonds \
  /tmp/seq-bond.log > /tmp/seq-bond-meta.log 2>&1 &
disown
nohup scripts/dev/run_sequential.sh /tmp/missing-industry.txt industries \
  /tmp/seq-industry.log > /tmp/seq-industry-meta.log 2>&1 &
disown
nohup scripts/dev/run_sequential.sh /tmp/missing-stock.txt holdings \
  /tmp/seq-stock.log > /tmp/seq-stock-meta.log 2>&1 &
disown
```

**Cost shape** — 11k + 13k + 13k = 37k funds. At ~2 s per AkShare
call the three runners together finish in roughly the
single-runner time (3-4 hours), because each runner is
already at one-fund-per-process and the macOS file handle /
DNS-cache contention is the dominant cost, not CPU.

**Idempotency** — `fund_cli bonds/industries/holdings` does
`INSERT OR REPLACE` into the same tables, so re-running a
finished runner (after a crash + cron restart) just re-queries
funds whose rows are already present. The downside is wasted
AkShare calls; the upside is no manual state-tracking.

### `finalize_and_publish.sh`

```bash
# Wait for all run_sequential processes to exit, then build
# a fresh query bundle and publish it to OSS.
nohup scripts/dev/finalize_and_publish.sh > /tmp/finalize.log 2>&1 &
disown
```

What it does:

1. Polls `pgrep -fl "run_sequential"` every 30 s until empty.
2. Runs `fund_cli cloud build-bundle` against the local
   full DB.
3. `ossutil cp` the gzip + sha256 to
   `oss://fund-data-public-l/fund-data/releases/<version>/`.
4. `ossutil cp -f` the manifest to
   `oss://fund-data-public-l/fund-data/current/manifest.json`.
5. `curl` the public manifest URL and print the new `version`
   to confirm the publish landed.

One-shot — exits after a successful publish, so the cron
`local-fill-backup-monitor` can take over the next round.

## How it fits with the cron layer

The `local-fill-backup-monitor` cron (every 15 min) is the
recovery layer:

- If a `run_sequential` process dies, the cron
  re-launches it from `run_sequential.sh` against the
  same codes file. The script re-reads the file from
  line 1, so funds that already have rows are re-queried
  (idempotent) but the loop eventually catches up.
- If `finalize_and_publish.sh` exits (one-shot), the cron
  does **not** relaunch it — the operator re-runs the
  watcher manually once a new round of sequential
  runners is in flight. This is intentional: a stale
  `finalize` should not re-publish against a half-finished
  round.

## Pre-flight checklist

```bash
# 1. Confirm net_compat patches are in fund_cli.py
PYTHONPATH=fund-data .venv-akshare/bin/python3 -c "
from _net_compat import apply; apply()
import requests
s = requests.Session()
print('proxies after apply:', s.proxies)
"
# Expect: {'http': '', 'https': ''}

# 2. Generate actionable-missing lists
DB=fund-data/data/fund_data.sqlite
for ds in bonds industries holdings; do
  PYTHONPATH=fund-data .venv-akshare/bin/python3 \
    fund-data/scripts/find_actionable_missing.py \
    --dataset "$ds" --db "$DB" --output "/tmp/missing-${ds}.txt"
done

# 3. Pick a known-good AkShare test (sanity)
PYTHONPATH=fund-data .venv-akshare/bin/python3 -c "
from _net_compat import apply; apply()
import akshare as ak
df = ak.fund_portfolio_industry_allocation_em(symbol='000001')
print('rows:', len(df))
"
# Expect: rows: 48 (anything > 0 means libmini_racer is alive)
```

If step 3 returns `libmini_racer` assertion, you are
already too deep into the broken state and the only fix is
to restart the shell / re-install AkShare.

## Gotchas worth knowing

- **Funds with no public holdings** (pure bond / money-market
  / index-固收 / FOF / REIT / QDII for industry) raise
  `Length mismatch: Expected axis has 1 elements, new values
  have 17 elements` in AkShare. The script catches and logs
  `FAIL on <code>` then moves on. ~80 % of the funds will
  fail in this way; that is the data, not a code bug.
- **AkShare 1.x`fund_open_fund_info_em:nav` returns HTML on
  the LAN-pinned IP** — not a JSON parse error, an HTML body
  the python json parser rejects. If you see
  `SyntaxError: Unexpected token '<'` in the log, the runner
  is hitting the P2P CDN and you should stop and restart
  once the resolver flips back. The per-dataset subcommands
  avoid this because they do not touch the NAV endpoint.
- **`set -u` + first FAIL kills the script** — the
  `run_sequential.sh` does **not** set `-e`, so a single
  fund failure does not abort the whole loop. The `|| echo
  FAIL on $code` is the safety net.
