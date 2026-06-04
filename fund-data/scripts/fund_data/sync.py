"""sync_fund, batch_sync_funds, coverage_report: the orchestration
layer that turns a fund code into a populated SQLite row.

Lifted from ``fund_data.py`` in the 0.3.0 split (RFC
``docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md``).
A ``sync`` call drives the four fetch_* functions per fund
plus the optional profile / bonds / industries / fees /
distributions / managers sub-calls, persists everything via
``FundDataStore.upsert_*``, and writes a row to
``sync_runs`` (or ``sync_failures`` on hard error).

Dependency direction: ``sync`` depends on ``fetch`` (the
per-capability wrappers), ``store`` (the upsert_* methods),
``http`` (the legacy FundDataClient) and ``providers``
(for the build_providers facade). It does not import
from parsers / normalizers / schema directly -- those
are consumed transitively via the lower layers.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import fetch, http, parsers, providers
from . import normalizers
from .paths import PROVIDER_AUTO, PROVIDER_EASTMONEY, utc_now
from .store import FundDataStore
from .http import FundDataClient, _RateLimiter

__all__ = [
    "coverage_rows",
    "coverage_report",
    "sync_fund",
    "batch_sync_funds",
]

logger = logging.getLogger("fund_data")


def coverage_rows(
    *,
    db_path: str | Path | None = None,
    fund_code: str | None = None,
) -> list[dict[str, Any]]:
    return FundDataStore(db_path).coverage_rows(fund_code=fund_code)


def coverage_report(
    *,
    db_path: str | Path | None = None,
    codes: list[str] | tuple[str, ...] | None = None,
    fund_type: str | None = None,
    only_incomplete: bool = False,
    min_completeness: float = 0.0,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return per-fund coverage with completeness score.

    Each row carries a `completeness` value in [0, 1] (8 dataset columns weighted equally)
    and a `missing` list of dataset names that are empty for that fund.
    """
    code_list = parsers.normalize_fund_codes(codes) if codes else None
    store = FundDataStore(db_path)

    where_clauses: list[str] = []
    params: list[str] = []
    if code_list:
        placeholders = ",".join("?" * len(code_list))
        where_clauses.append(f"f.fund_code in ({placeholders})")
        params.extend(code_list)
    if fund_type:
        where_clauses.append("f.fund_type like ?")
        params.append(f"%{fund_type}%")
    where = (" where " + " and ".join(where_clauses)) if where_clauses else ""

    limit_clause = f" limit {int(limit)}" if limit else ""

    sql = f"""
        select
            f.fund_code,
            f.fund_name,
            f.fund_type,
            case when p.fund_code is null then 0 else 1 end as has_profile,
            (select count(*) from nav_history n where n.fund_code = f.fund_code) as nav_rows,
            (select count(*) from stock_holdings s where s.fund_code = f.fund_code) as stock_holding_rows,
            (select count(*) from bond_holdings b where b.fund_code = f.fund_code) as bond_holding_rows,
            (select count(*) from industry_allocations i where i.fund_code = f.fund_code) as industry_rows,
            (select count(*) from fee_structures fs where fs.fund_code = f.fund_code) as fee_rows,
            (select count(*) from dividends d where d.fund_code = f.fund_code) as dividend_rows,
            (select count(*) from splits sp where sp.fund_code = f.fund_code) as split_rows
        from funds f
        left join fund_profiles p on p.fund_code = f.fund_code
        {where}
        order by f.fund_code
        {limit_clause}
    """

    DATASETS = [
        ("has_profile", "profile"),
        ("nav_rows", "nav"),
        ("stock_holding_rows", "stock_holdings"),
        ("bond_holding_rows", "bond_holdings"),
        ("industry_rows", "industry"),
        ("fee_rows", "fees"),
        ("dividend_rows", "dividends"),
        ("split_rows", "splits"),
    ]

    with store.connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    for row in rows:
        present = 0
        missing: list[str] = []
        for column, name in DATASETS:
            value = row.get(column) or 0
            if value:
                present += 1
            else:
                missing.append(name)
        row["completeness"] = round(present / len(DATASETS), 4)
        row["missing"] = missing

    if only_incomplete:
        rows = [r for r in rows if r["completeness"] < 1.0]
    if min_completeness > 0:
        rows = [r for r in rows if r["completeness"] >= min_completeness]

    return rows


def _fund_row_from_sync(
    code: str,
    *,
    snapshot: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = snapshot or {}
    profile = profile or {}
    return {
        "fund_code": normalizers.normalize_fund_code(code),
        "fund_name": profile.get("fund_name")
        or snapshot.get("fund_name")
        or normalizers.normalize_fund_code(code),
        "fund_type": profile.get("fund_type", ""),
        "company": profile.get("fund_company", ""),
        "manager": profile.get("manager", ""),
        "nav": None,
        "nav_date": "",
        "other_names": "",
        "source": profile.get("source") or snapshot.get("source", "sync"),
    }


def sync_fund(
    code: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = 1,
    per: int = 50,
    db_path: str | Path | None = None,
    client: FundDataClient | None = None,
    provider: str = PROVIDER_AUTO,
    include_snapshots: bool = True,
    include_holdings: bool = False,
    include_profile: bool = False,
    include_bonds: bool = False,
    include_industries: bool = False,
    include_fees: bool = False,
    include_distributions: bool = False,
    include_managers: bool = False,
    include_all: bool = False,
    report_year: str | None = None,
    fee_indicators: list[str] | None = None,
) -> dict[str, Any]:
    started_at = utc_now()
    store = FundDataStore(db_path)
    code = normalizers.normalize_fund_code(code)
    if include_all:
        include_snapshots = True
        include_holdings = True
        include_profile = True
        include_bonds = True
        include_industries = True
        include_fees = True
        include_distributions = True
        include_managers = True
    rows_changed = 0
    dataset_errors: list[dict[str, str]] = []

    def record_dataset_error(dataset: str, exc: Exception) -> None:
        dataset_errors.append({"dataset": dataset, "message": str(exc)})

    snapshot_count = 0
    snapshot: dict[str, Any] | None = None
    try:
        if include_snapshots:
            snapshot = fetch.fetch_snapshot(
                code, db_path=db_path, client=client, persist=True, provider=provider
            )
            # Back-end share classes have no standalone Eastmoney
            # page; treat "no snapshot available" (None or empty dict)
            # as a soft skip rather than aborting the whole sync.
            snapshot_count = 1 if snapshot else 0
            rows_changed += snapshot_count

        profile_count = 0
        profile: dict[str, Any] | None = None
        if include_profile:
            try:
                profile = fetch.fetch_profile(code, db_path=db_path, persist=True, provider=provider)
                profile_count = 1
                rows_changed += profile_count
            except Exception as exc:
                record_dataset_error("profile", exc)

        fund_count = store.upsert_funds(
            [_fund_row_from_sync(code, snapshot=snapshot, profile=profile)]
        )
        rows_changed += fund_count

        nav_rows = fetch.fetch_nav_history(
            code,
            start_date=start_date,
            end_date=end_date,
            page=page,
            per=per,
            db_path=db_path,
            client=client,
            persist=True,
            provider=provider,
        )
        nav_count = len(nav_rows)
        rows_changed += nav_count

        holdings_count = 0
        if include_holdings:
            try:
                holding_rows = fetch.fetch_stock_holdings(
                    code, report_year=report_year, db_path=db_path, persist=True, provider=provider
                )
                holdings_count = len(holding_rows)
                rows_changed += holdings_count
            except Exception as exc:
                record_dataset_error("stock_holdings", exc)

        bond_count = 0
        if include_bonds:
            try:
                bond_rows = fetch.fetch_bond_holdings(
                    code, report_year=report_year, db_path=db_path, persist=True, provider=provider
                )
                bond_count = len(bond_rows)
                rows_changed += bond_count
            except Exception as exc:
                record_dataset_error("bond_holdings", exc)

        industry_count = 0
        if include_industries:
            try:
                industry_rows = fetch.fetch_industry_allocations(
                    code, report_year=report_year, db_path=db_path, persist=True, provider=provider
                )
                industry_count = len(industry_rows)
                rows_changed += industry_count
            except Exception as exc:
                record_dataset_error("industry_allocations", exc)

        fee_count = 0
        if include_fees:
            try:
                fee_rows = fetch.fetch_fee_structures(
                    code,
                    indicators=fee_indicators,
                    db_path=db_path,
                    persist=True,
                    provider=provider,
                )
                fee_count = len(fee_rows)
                rows_changed += fee_count
            except Exception as exc:
                record_dataset_error("fee_structures", exc)

        dividend_count = 0
        split_count = 0
        if include_distributions:
            try:
                dividend_rows = fetch.fetch_dividends(
                    code, db_path=db_path, persist=True, provider=provider
                )
                dividend_count = len(dividend_rows)
                rows_changed += dividend_count
            except Exception as exc:
                record_dataset_error("dividends", exc)
            try:
                split_rows = fetch.fetch_splits(code, db_path=db_path, persist=True, provider=provider)
                split_count = len(split_rows)
                rows_changed += split_count
            except Exception as exc:
                record_dataset_error("splits", exc)

        manager_count = 0
        if include_managers:
            try:
                manager_rows = fetch.fetch_fund_managers(
                    code, db_path=db_path, persist=True, provider=provider
                )
                manager_count = len(manager_rows)
                rows_changed += manager_count
            except Exception as exc:
                record_dataset_error("fund_managers", exc)

        coverage = store.coverage_rows(fund_code=code)
        store.record_sync_run(
            operation="sync",
            fund_code=code,
            status="ok",
            rows_changed=rows_changed,
            started_at=started_at,
        )
        return {
            "fund_code": code,
            "status": "ok",
            "rows_changed": rows_changed,
            "fund_rows": fund_count,
            "snapshot_rows": snapshot_count,
            "nav_rows": nav_count,
            "holdings_rows": holdings_count,
            "stock_holding_rows": holdings_count,
            "profile_rows": profile_count,
            "bond_holding_rows": bond_count,
            "industry_rows": industry_count,
            "fee_rows": fee_count,
            "dividend_rows": dividend_count,
            "split_rows": split_count,
            "manager_rows": manager_count,
            "dataset_errors": dataset_errors,
            "coverage": coverage[0] if coverage else {},
        }
    except Exception as exc:
        store.record_sync_run(
            operation="sync",
            fund_code=code,
            status="error",
            rows_changed=rows_changed,
            started_at=started_at,
            message=str(exc),
        )
        raise


def batch_sync_funds(
    codes: list[str] | tuple[str, ...],
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = 1,
    per: int = 50,
    db_path: str | Path | None = None,
    provider: str = PROVIDER_AUTO,
    include_snapshots: bool = True,
    include_holdings: bool = False,
    include_profile: bool = False,
    include_bonds: bool = False,
    include_industries: bool = False,
    include_fees: bool = False,
    include_distributions: bool = False,
    include_managers: bool = False,
    include_all: bool = False,
    report_year: str | None = None,
    fee_indicators: list[str] | None = None,
    batch_id: str | None = None,
    stop_on_error: bool = False,
    concurrency: int = 1,
    min_interval_seconds: float | None = None,
) -> dict[str, Any]:
    code_list = parsers.normalize_fund_codes(codes)
    if not code_list:
        raise ValueError("no fund codes provided for batch sync")

    batch_id = batch_id or f"batch-{utc_now()}"
    store = FundDataStore(db_path)
    results: list[dict[str, Any]] = []
    ok_count = 0
    failed_count = 0

    concurrency = max(1, int(concurrency))
    if min_interval_seconds is None:
        min_interval_seconds = 0.25 if concurrency > 1 else 1.0

    if include_all:
        include_snapshots = True
    sync_kwargs: dict[str, Any] = {
        "start_date": start_date,
        "end_date": end_date,
        "page": page,
        "per": per,
        "db_path": db_path,
        "provider": provider,
        "include_snapshots": include_snapshots,
        "include_holdings": include_holdings,
        "include_profile": include_profile,
        "include_bonds": include_bonds,
        "include_industries": include_industries,
        "include_fees": include_fees,
        "include_distributions": include_distributions,
        "include_managers": include_managers,
        "include_all": include_all,
        "report_year": report_year,
        "fee_indicators": fee_indicators,
    }
    if provider == PROVIDER_EASTMONEY:
        sync_kwargs["client"] = FundDataClient(
            min_interval_seconds=min_interval_seconds,
            rate_limiter=_RateLimiter(min_interval_seconds) if concurrency > 1 else None,
        )

    def _run(code: str) -> dict[str, Any]:
        try:
            # Runtime lookup so test mocks of
            # ``fund_data.sync_fund = ...`` take effect.
            import fund_data as _fd
            result = _fd.sync_fund(code, **sync_kwargs)
            result.setdefault("fund_code", code)
            result.setdefault("status", "ok")
            return result
        except Exception as exc:
            return {"fund_code": code, "status": "error", "message": str(exc)}

    if concurrency <= 1:
        for code in code_list:
            outcome = _run(code)
            if outcome.get("status") == "ok":
                results.append(outcome)
                ok_count += 1
            else:
                store.record_sync_failure(
                    batch_id=batch_id,
                    operation="batch-sync",
                    fund_code=outcome["fund_code"],
                    provider=provider,
                    message=outcome.get("message", ""),
                )
                results.append(outcome)
                failed_count += 1
                if stop_on_error:
                    raise ProviderError(outcome.get("message", ""))
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_code = {executor.submit(_run, code): code for code in code_list}
            for future in as_completed(future_to_code):
                outcome = future.result()
                if outcome.get("status") == "ok":
                    results.append(outcome)
                    ok_count += 1
                else:
                    store.record_sync_failure(
                        batch_id=batch_id,
                        operation="batch-sync",
                        fund_code=outcome["fund_code"],
                        provider=provider,
                        message=outcome.get("message", ""),
                    )
                    results.append(outcome)
                    failed_count += 1
                    if stop_on_error:
                        for pending in future_to_code:
                            pending.cancel()
                        raise ProviderError(outcome.get("message", ""))

    coverage: list[dict[str, Any]] = []
    for code in code_list:
        coverage.extend(store.coverage_rows(fund_code=code))

    return {
        "batch_id": batch_id,
        "total": len(code_list),
        "ok": ok_count,
        "failed": failed_count,
        "concurrency": concurrency,
        "min_interval_seconds": min_interval_seconds,
        "results": results,
        "coverage": coverage,
    }


