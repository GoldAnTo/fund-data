"""Convenience fetch_* functions: thin wrappers over the four
provider classes plus the store.

Lifted from ``fund_data.py`` in the 0.3.0 split (RFC
``docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md``).
Each function takes a fund code (and the usual
``db_path`` / ``client`` / ``provider`` kwargs) and returns
a list of dicts that ``FundDataStore.upsert_*`` can persist.
This is the layer that ``sync_fund`` / ``batch_sync_funds``
sit on top of.

Dependency direction: ``fetch`` depends on the four
``providers.*`` classes (or the legacy Eastmoney direct
client) and ``parsers`` for the Eastmoney shape. It
also touches ``store`` for the per-table write helper.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import http, normalizers, parsers, providers
from .providers import run_provider_chain
from .store import FundDataStore
from .paths import PROVIDER_AUTO

__all__ = [
    "search_funds",
    "fetch_fund_list",
    "fetch_nav_history",
    "fetch_snapshot",
    "fetch_stock_holdings",
    "fetch_profile",
    "fetch_bond_holdings",
    "fetch_industry_allocations",
    "fetch_fee_structures",
    "fetch_dividends",
    "fetch_splits",
    "fetch_fund_managers",
]

logger = logging.getLogger("fund_data")


def _build_providers(provider: str, capability: str | None = None):
    """Runtime-lookup wrapper for build_providers so tests that
    monkeypatch ``fund_data.build_providers = ...`` take effect.
    The static ``from .providers import build_providers`` would
    bind a separate reference at import time and bypass the
    monkeypatch on the package root."""
    import fund_data
    return fund_data.build_providers(provider, capability=capability)




def search_funds(
    keyword: str,
    *,
    db_path: str | Path | None = None,
    client: FundDataClient | None = None,
    persist: bool = True,
    raw_text: str | None = None,
    provider: str = PROVIDER_AUTO,
) -> list[dict[str, Any]]:
    if raw_text is not None:
        rows = parsers.parse_search_results(raw_text)
        source = "eastmoney.search"
        raw = raw_text
    elif client is not None:
        raw = client.search(keyword)
        rows = parsers.parse_search_results(raw)
        source = "eastmoney.search"
    else:
        result = run_provider_chain(
            _build_providers(provider, capability="search"), "search_funds", keyword
        )
        rows = result.rows
        source = f"{result.provider}.search"
        raw = normalizers._json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures})
    if persist:
        store = FundDataStore(db_path)
        store.upsert_funds(rows)
        store.record_raw_response(source, keyword, raw)
    return rows


def fetch_fund_list(
    *,
    db_path: str | Path | None = None,
    persist: bool = True,
    raw_text: str | None = None,
    provider: str = PROVIDER_AUTO,
) -> list[dict[str, Any]]:
    if raw_text is not None:
        rows = parsers.parse_search_results(raw_text)
        source = "eastmoney.fundcode_search"
        raw = raw_text
    else:
        result = run_provider_chain(_build_providers(provider, capability="fund_list"), "fund_list")
        rows = result.rows
        source = f"{result.provider}.fund_list"
        raw = normalizers._json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures})
    if persist:
        store = FundDataStore(db_path)
        store.upsert_funds(rows)
        store.record_raw_response(source, "all", raw)
    return rows


def fetch_nav_history(
    code: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = 1,
    per: int = 20,
    db_path: str | Path | None = None,
    client: FundDataClient | None = None,
    persist: bool = True,
    raw_text: str | None = None,
    provider: str = PROVIDER_AUTO,
) -> list[dict[str, Any]]:
    if raw_text is not None:
        raw = raw_text
        rows = parsers.parse_nav_history(raw)
        source = "eastmoney.nav_history"
    elif client is not None:
        raw = client.nav_history(code, start_date=start_date, end_date=end_date, page=page, per=per)
        rows = parsers.parse_nav_history(raw)
        source = "eastmoney.nav_history"
    else:
        result = run_provider_chain(
            _build_providers(provider, capability="nav_history"),
            "nav_history",
            code,
            start_date=start_date,
            end_date=end_date,
            page=page,
            per=per,
        )
        rows = result.rows
        source = f"{result.provider}.nav_history"
        raw = normalizers._json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures})
    if persist:
        request_key = (
            f"{normalizers.normalize_fund_code(code)}:{start_date or ''}:{end_date or ''}:{page}:{per}"
        )
        store = FundDataStore(db_path)
        store.upsert_nav_history(code, rows)
        store.record_raw_response(source, request_key, raw)
    return rows


def fetch_snapshot(
    code: str,
    *,
    db_path: str | Path | None = None,
    client: FundDataClient | None = None,
    persist: bool = True,
    raw_text: str | None = None,
    provider: str = PROVIDER_AUTO,
) -> dict[str, Any] | None:
    if raw_text is not None:
        raw = raw_text
        snapshot = parsers.parse_snapshot(raw, default_code=code)
        source = "eastmoney.snapshot"
    elif client is not None:
        raw = client.snapshot(code)
        snapshot = parsers.parse_snapshot(raw, default_code=code)
        source = "eastmoney.snapshot"
    else:
        result = run_provider_chain(
            _build_providers(provider, capability="snapshot"), "snapshot", code
        )
        snapshot = result.rows
        source = f"{result.provider}.snapshot"
        raw = normalizers._json_dumps(
            {"provider": result.provider, "snapshot": snapshot, "failures": result.failures}
        )
    if persist:
        # Back-end share classes (000002, 000012, ...) yield an
        # empty Eastmoney page; parse_snapshot returns None and the
        # EastmoneyProvider layer converts that to an empty dict.
        # Either signal means "no snapshot available" -- do not
        # write a half-row to the data base.
        if snapshot:
            store = FundDataStore(db_path)
            store.upsert_snapshot(snapshot)
            store.record_raw_response(source, normalizers.normalize_fund_code(code), raw)
    return snapshot


def fetch_stock_holdings(
    code: str,
    *,
    report_year: str | None = None,
    db_path: str | Path | None = None,
    persist: bool = True,
    provider: str = PROVIDER_AUTO,
) -> list[dict[str, Any]]:
    result = run_provider_chain(
        _build_providers(provider, capability="stock_holdings"),
        "stock_holdings",
        code,
        report_year=report_year,
    )
    rows = result.rows
    if persist:
        store = FundDataStore(db_path)
        store.upsert_stock_holdings(code, rows)
        store.record_raw_response(
            f"{result.provider}.stock_holdings",
            f"{normalizers.normalize_fund_code(code)}:{report_year or ''}",
            normalizers._json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures}),
        )
    return rows


def fetch_profile(
    code: str,
    *,
    db_path: str | Path | None = None,
    persist: bool = True,
    provider: str = PROVIDER_AUTO,
) -> dict[str, Any]:
    result = run_provider_chain(_build_providers(provider, capability="profile"), "profile", code)
    profile = result.rows
    if persist:
        store = FundDataStore(db_path)
        store.upsert_profile(profile)
        store.record_raw_response(
            f"{result.provider}.profile",
            normalizers.normalize_fund_code(code),
            normalizers._json_dumps(
                {"provider": result.provider, "profile": profile, "failures": result.failures}
            ),
        )
    return profile


def fetch_bond_holdings(
    code: str,
    *,
    report_year: str | None = None,
    db_path: str | Path | None = None,
    persist: bool = True,
    provider: str = PROVIDER_AUTO,
) -> list[dict[str, Any]]:
    result = run_provider_chain(
        _build_providers(provider, capability="bond_holdings"),
        "bond_holdings",
        code,
        report_year=report_year,
    )
    rows = result.rows
    if persist:
        store = FundDataStore(db_path)
        store.upsert_bond_holdings(code, rows)
        store.record_raw_response(
            f"{result.provider}.bond_holdings",
            f"{normalizers.normalize_fund_code(code)}:{report_year or ''}",
            normalizers._json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures}),
        )
    return rows


def fetch_industry_allocations(
    code: str,
    *,
    report_year: str | None = None,
    db_path: str | Path | None = None,
    persist: bool = True,
    provider: str = PROVIDER_AUTO,
) -> list[dict[str, Any]]:
    result = run_provider_chain(
        _build_providers(provider, capability="industry_allocations"),
        "industry_allocations",
        code,
        report_year=report_year,
    )
    rows = result.rows
    if persist:
        store = FundDataStore(db_path)
        store.upsert_industry_allocations(code, rows)
        store.record_raw_response(
            f"{result.provider}.industry_allocations",
            f"{normalizers.normalize_fund_code(code)}:{report_year or ''}",
            normalizers._json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures}),
        )
    return rows


def fetch_fee_structures(
    code: str,
    *,
    indicators: list[str] | None = None,
    db_path: str | Path | None = None,
    persist: bool = True,
    provider: str = PROVIDER_AUTO,
) -> list[dict[str, Any]]:
    result = run_provider_chain(
        _build_providers(provider, capability="fee_structures"),
        "fee_structures",
        code,
        indicators=indicators,
    )
    rows = result.rows
    if persist:
        store = FundDataStore(db_path)
        store.upsert_fee_structures(code, rows)
        store.record_raw_response(
            f"{result.provider}.fee_structures",
            normalizers.normalize_fund_code(code),
            normalizers._json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures}),
        )
    return rows


def fetch_dividends(
    code: str,
    *,
    db_path: str | Path | None = None,
    persist: bool = True,
    provider: str = PROVIDER_AUTO,
) -> list[dict[str, Any]]:
    result = run_provider_chain(
        _build_providers(provider, capability="dividends"), "dividends", code, allow_empty=True
    )
    rows = result.rows
    if persist:
        store = FundDataStore(db_path)
        store.upsert_dividends(code, rows)
        store.record_raw_response(
            f"{result.provider}.dividends",
            normalizers.normalize_fund_code(code),
            normalizers._json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures}),
        )
    return rows


def fetch_splits(
    code: str,
    *,
    db_path: str | Path | None = None,
    persist: bool = True,
    provider: str = PROVIDER_AUTO,
) -> list[dict[str, Any]]:
    result = run_provider_chain(
        _build_providers(provider, capability="splits"), "splits", code, allow_empty=True
    )
    rows = result.rows
    if persist:
        store = FundDataStore(db_path)
        store.upsert_splits(code, rows)
        store.record_raw_response(
            f"{result.provider}.splits",
            normalizers.normalize_fund_code(code),
            normalizers._json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures}),
        )
    return rows


def fetch_fund_managers(
    code: str | None = None,
    *,
    db_path: str | Path | None = None,
    persist: bool = True,
    provider: str = PROVIDER_AUTO,
) -> list[dict[str, Any]]:
    result = run_provider_chain(
        _build_providers(provider, capability="fund_managers"), "fund_managers", code
    )
    rows = result.rows
    if persist:
        store = FundDataStore(db_path)
        store.upsert_fund_managers(rows)
        store.record_raw_response(
            f"{result.provider}.fund_managers",
            normalizers.normalize_fund_code(code) if code else "all",
            normalizers._json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures}),
        )
    return rows

