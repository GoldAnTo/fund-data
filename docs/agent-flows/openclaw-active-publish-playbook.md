# OpenClaw Active Publish Playbook

Publishing is the **only** OpenClaw step that is not safe to run inside
the completion runner. It is a separate operator action that
`fund_data.completion` explicitly refuses to do. This playbook walks an
operator through the manual steps in order.

The runner is what generates the data; the operator is what publishes
it. Until you have done at least one full **assisted** fill that
returned `rows_changed > 0` and `doctor_ok: true`, do not publish.

## When to Publish

Run ``fund_cli completion-verify`` first. Only publish if **all** are
true:

- ``executed: true`` (the run actually mutated rows)
- ``refusal_reason: null`` (no policy failure)
- ``rows_changed > 0`` (something actually filled)
- ``new_failures == 0`` (the P3 stale bucket did not grow)
- ``doctor_ok: true`` (the post-run health check passed)
- ``publish_recommended: true`` (the project flagged it safe)

If any of these fail, fix the underlying issue (refresh a different
dataset, narrow the policy budgets, re-run doctor) and re-verify.
**Do not bypass the gate to "ship it faster".**

## Step 1: Build the Bundle

```bash
.venv-akshare/bin/python fund-data/scripts/fund_cli.py cloud build-bundle \
    --source-db fund-data/data/fund_data.sqlite \
    --output-dir dist/openclaw-release \
    --base-url https://fund-data-public-l.oss-cn-shanghai.aliyuncs.com/fund-data/releases/openclaw \
    --version openclaw-$(date -u +%Y%m%dT%H%M%SZ)
```

The version string is human-readable (``openclaw-20260603T220000Z``)
and matches the per-run directory under
``fund-data/data/openclaw_runs/``.

## Step 2: Upload Release Artifacts

```bash
ossutil cp -r dist/openclaw-release/ \
    oss://fund-data-public-l/fund-data/releases/openclaw/openclaw-20260603T220000Z/
```

Use ``ossutil cp -f`` to overwrite an existing version (non-interactive
shells silently fail on overwrite without ``-f``).

## Step 3: Upload the Manifest Last

```bash
ossutil cp -f dist/openclaw-release/manifest.json \
    oss://fund-data-public-l/fund-data/releases/openclaw/openclaw-20260603T220000Z/manifest.json
```

The manifest goes up last so that consumers pulling
``current/manifest.json`` see the new ``releases/openclaw/...`` only
once every artifact is in place.

## Step 4: Update the ``current/`` Pointer

```bash
ossutil cp -f dist/openclaw-release/manifest.json \
    oss://fund-data-public-l/fund-data/current/manifest.json
```

The ``current/manifest.json`` is the file
``fund_cli cloud pull`` reads to discover the latest version, so this
is the line that flips the world onto the new bundle.

## Step 5: Pull the Fresh Bundle Locally

```bash
.venv-akshare/bin/python fund-data/scripts/fund_cli.py cloud pull
```

This re-downloads ``current/manifest.json`` and writes the SQLite
query bundle to ``~/.cache/fund-data/releases/<version>/``.

## Step 6: Run Doctor Against the Pulled Bundle

```bash
.venv-akshare/bin/python fund-data/scripts/doctor.py --skip-network --quiet
```

Doctor reads from ``fund_data.default_db_path()``, which after a
successful pull now points at the freshly downloaded
``fund_data_query.sqlite``. Confirm:

- ``database.ok: true``
- ``cloud_cache.installed: true``
- ``cloud_cache.update_available: false`` (we are on the version we
  just published)
- ``default_db.source: cloud_cache``

## Step 7: Confirm OpenClaw Now Reads the New Version

In an OpenClaw session, run:

```text
fund_cloud_status
```

The ``installed_version`` field should equal
``openclaw-20260603T220000Z``. If it does not, the cache pointer
(``~/.cache/fund-data/current.json``) is stale; delete it and re-run
``cloud pull``.

## Step 8: Roll Back If Something Went Wrong

If a downstream consumer reports issues:

```bash
# restore the previous good manifest
ossutil cp -f \
    oss://fund-data-public-l/fund-data/releases/<previous-version>/manifest.json \
    oss://fund-data-public-l/fund-data/current/manifest.json
```

The release artifacts under ``releases/openclaw/<bad-version>/`` stay
in place for forensics; only the ``current/`` pointer changes.

## What This Playbook Will Never Do

- It does not run from inside ``fund_completion_run``. The runner
  refuses to call ``cloud build-bundle``, ``cloud upload``, or
  ``cloud archive-full``.
- It does not skip doctor. A failed doctor blocks publish.
- It does not bypass the ``publish.min_rows_changed`` budget. The
  operator decides when to lower the budget; the runner never lowers
  it on its own.

## What Comes After Several Successful Manual Publishes

Once you have done at least three full assisted-fill → manual-publish
cycles without an incident, you can consider:

1. Tightening the publish gate thresholds in
   ``fund-data/config/openclaw-active-completion.json``.
2. Adding a CI workflow that wraps the manual steps and runs
   ``publish_recommended`` as the final gate.
3. Promoting ``mode`` from ``assisted`` to ``autonomous`` for trusted
   operator accounts only.

These are *future* work and intentionally not in the first version.
