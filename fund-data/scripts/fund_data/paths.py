"""Path resolver + project-level constants.

Lifted from ``fund_data.py`` in the 0.3.0 split (RFC
``docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md``).
Holds the on-disk SQLite path, the four provider id strings,
and the ``default_db_path()`` resolver that honours the
``FUND_DATA_CACHE_DIR`` / ``FUND_DATA_DB`` env vars before
falling back to the on-disk file. Nothing else in the
package depends on the rest of ``fund_data``; this module is
the leaf of the dependency graph.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

# On-disk fallback for the full local DB. The cloud query DB
# (downloaded via ``fund_cloud.pull_bundle``) is preferred when
# the cache pointer is present; see ``default_db_path()`` for
# the precedence. ``parents[2]`` is the ``fund-data/`` folder
# (paths.py lives at ``fund-data/scripts/fund_data/paths.py``,
# so parents[0] = scripts/fund_data/, parents[1] = scripts/,
# parents[2] = fund-data/), resolving to
# ``fund-data/data/fund_data.sqlite``.
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "fund_data.sqlite"

# Provider id strings used by the auto-chain builder
# (``build_providers``) and the per-capability test fixtures.
PROVIDER_AUTO = "auto"
PROVIDER_EASTMONEY = "eastmoney"
PROVIDER_AKSHARE = "akshare"
PROVIDER_INVESTODAY = "investoday"
PROVIDER_TUSHARE = "tushare"


def default_db_path() -> Path:
    """Resolve the on-disk path to use when no ``db_path=`` is passed.

    Precedence (intentionally narrow):
      1. ``FUND_DATA_CACHE_DIR`` — explicit cloud-cache override,
         useful when an agent/CI also sets a temporary ``FUND_DATA_DB``.
      2. ``FUND_DATA_DB`` env var — explicit local override
         (typically test or one-off dev runs).
      3. ``fund_cloud.ensure_project_bundle()`` — install or reuse
         the project OSS query DB unless ``FUND_DATA_AUTO_PULL=0``.
         ``FUND_DATA_CACHE_DIR`` controls where that cache lives.
      4. ``fund_cloud.current_db_path()`` — the installed query DB,
         picked up automatically when the bundle has a current.json.
      5. ``DEFAULT_DB_PATH`` — the on-disk fallback
         (``fund-data/data/fund_data.sqlite``).
    """
    cache_dir = os.environ.get("FUND_DATA_CACHE_DIR")
    configured = os.environ.get("FUND_DATA_DB")
    if configured and not cache_dir:
        return Path(configured)
    try:
        from .. import fund_cloud
    except ImportError:  # pragma: no cover - direct script execution / top-level package import
        import fund_cloud  # type: ignore
    bootstrap = fund_cloud.ensure_project_bundle(cache_dir=cache_dir)
    bootstrap_db = bootstrap.get("db_path")
    if bootstrap_db and Path(bootstrap_db).is_file():
        return Path(bootstrap_db)
    cloud_db = fund_cloud.current_db_path()
    return cloud_db or DEFAULT_DB_PATH


def utc_now() -> str:
    """ISO-8601 UTC timestamp with second precision.

    Used as the default for ``fetched_at`` columns and for
    log lines. Second precision matches what the rest of the
    schema writes (microsecond == 0 is enforced because
    AkShare and the Eastmoney endpoints both give second
    precision, and a mixed-precision schema causes the
    ``UNIQUE (fund_code, nav_date)`` constraint to fire
    spuriously when an existing row's timestamp is at
    microsecond 0 and the new one is at microsecond 0 too).
    """
    return datetime.now(UTC).replace(microsecond=0).isoformat()


__all__ = [
    "DEFAULT_DB_PATH",
    "PROVIDER_AUTO",
    "PROVIDER_EASTMONEY",
    "PROVIDER_AKSHARE",
    "PROVIDER_INVESTODAY",
    "PROVIDER_TUSHARE",
    "default_db_path",
    "utc_now",
]
