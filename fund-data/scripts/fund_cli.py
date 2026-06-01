#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import fund_data


def _read_offline_raw(path: str | None) -> str | None:
    if not path:
        return None
    return Path(path).read_text(encoding="utf-8")


def _print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _add_common_db_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", help="SQLite database path. Defaults to fund-data/data/fund_data.sqlite")


def _add_offline_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--offline-raw", help="Read a saved raw response instead of calling the network")


def _add_provider_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--provider",
        choices=["auto", "eastmoney", "akshare", "investoday"],
        default="auto",
        help="Data provider. auto tries configured structured sources first, then free fallbacks.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Search, fetch, persist, and export Chinese fund data."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="Fetch the full fund code list")
    list_parser.add_argument("--limit", type=int, default=50)
    _add_common_db_arg(list_parser)
    _add_offline_arg(list_parser)
    _add_provider_arg(list_parser)

    search = subparsers.add_parser("search", help="Search funds by keyword or code")
    search.add_argument("keyword")
    search.add_argument("--limit", type=int, default=20)
    _add_common_db_arg(search)
    _add_offline_arg(search)
    _add_provider_arg(search)

    nav = subparsers.add_parser("nav", help="Fetch historical NAV rows")
    nav.add_argument("code")
    nav.add_argument("--start-date")
    nav.add_argument("--end-date")
    nav.add_argument("--page", type=int, default=1)
    nav.add_argument("--per", type=int, default=20)
    _add_common_db_arg(nav)
    _add_offline_arg(nav)
    _add_provider_arg(nav)

    snapshot = subparsers.add_parser("snapshot", help="Fetch fund snapshot metadata")
    snapshot.add_argument("code")
    _add_common_db_arg(snapshot)
    _add_offline_arg(snapshot)
    _add_provider_arg(snapshot)

    holdings = subparsers.add_parser("holdings", help="Fetch fund stock holdings")
    holdings.add_argument("code")
    holdings.add_argument("--report-year")
    _add_common_db_arg(holdings)
    _add_provider_arg(holdings)

    profile = subparsers.add_parser("profile", help="Fetch fund profile/basic archive data")
    profile.add_argument("code")
    _add_common_db_arg(profile)
    _add_provider_arg(profile)

    bonds = subparsers.add_parser("bonds", help="Fetch fund bond holdings")
    bonds.add_argument("code")
    bonds.add_argument("--report-year")
    _add_common_db_arg(bonds)
    _add_provider_arg(bonds)

    industries = subparsers.add_parser("industries", help="Fetch fund industry allocations")
    industries.add_argument("code")
    industries.add_argument("--report-year")
    _add_common_db_arg(industries)
    _add_provider_arg(industries)

    fees = subparsers.add_parser("fees", help="Fetch fund fee structures")
    fees.add_argument("code")
    fees.add_argument("--indicator", action="append")
    _add_common_db_arg(fees)
    _add_provider_arg(fees)

    dividends = subparsers.add_parser("dividends", help="Fetch fund dividends")
    dividends.add_argument("code")
    _add_common_db_arg(dividends)
    _add_provider_arg(dividends)

    splits = subparsers.add_parser("splits", help="Fetch fund share splits")
    splits.add_argument("code")
    _add_common_db_arg(splits)
    _add_provider_arg(splits)

    managers = subparsers.add_parser("managers", help="Fetch fund manager records")
    managers.add_argument("--code")
    _add_common_db_arg(managers)
    _add_provider_arg(managers)

    sync = subparsers.add_parser(
        "sync", help="Fetch snapshot, NAV, and optional fund base datasets in one run"
    )
    sync.add_argument("code")
    sync.add_argument("--start-date")
    sync.add_argument("--end-date")
    sync.add_argument("--page", type=int, default=1)
    sync.add_argument("--per", type=int, default=50)
    sync.add_argument("--include-holdings", action="store_true")
    sync.add_argument("--include-profile", action="store_true")
    sync.add_argument("--include-bonds", action="store_true")
    sync.add_argument("--include-industries", action="store_true")
    sync.add_argument("--include-fees", action="store_true")
    sync.add_argument("--include-distributions", action="store_true")
    sync.add_argument("--include-managers", action="store_true")
    sync.add_argument("--include-all", action="store_true")
    sync.add_argument("--report-year")
    sync.add_argument("--fee-indicator", action="append")
    _add_common_db_arg(sync)
    _add_provider_arg(sync)

    batch_sync = subparsers.add_parser(
        "batch-sync", help="Run sync for a fund-code file or repeated fund codes"
    )
    batch_sync.add_argument("--codes-file", action="append", help="Text file containing fund codes")
    batch_sync.add_argument("--code", action="append", help="Fund code; can be repeated")
    batch_sync.add_argument("--start-date")
    batch_sync.add_argument("--end-date")
    batch_sync.add_argument("--page", type=int, default=1)
    batch_sync.add_argument("--per", type=int, default=50)
    batch_sync.add_argument("--include-holdings", action="store_true")
    batch_sync.add_argument("--include-profile", action="store_true")
    batch_sync.add_argument("--include-bonds", action="store_true")
    batch_sync.add_argument("--include-industries", action="store_true")
    batch_sync.add_argument("--include-fees", action="store_true")
    batch_sync.add_argument("--include-distributions", action="store_true")
    batch_sync.add_argument("--include-managers", action="store_true")
    batch_sync.add_argument("--include-all", action="store_true")
    batch_sync.add_argument("--report-year")
    batch_sync.add_argument("--fee-indicator", action="append")
    batch_sync.add_argument("--batch-id")
    batch_sync.add_argument("--stop-on-error", action="store_true")
    batch_sync.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Number of fund codes to fetch in parallel. Keep <=4 to respect Eastmoney rate limits.",
    )
    batch_sync.add_argument(
        "--min-interval-seconds",
        type=float,
        default=None,
        help="Global minimum interval between HTTP calls. Defaults to 1.0 (sequential) or 0.25 (concurrent).",
    )
    _add_common_db_arg(batch_sync)
    _add_provider_arg(batch_sync)

    coverage = subparsers.add_parser("coverage", help="Show local data coverage by fund")
    coverage.add_argument("--fund-code")
    _add_common_db_arg(coverage)

    coverage_report = subparsers.add_parser(
        "coverage-report",
        help="Show completeness score for funds with detailed missing-dataset breakdown",
    )
    coverage_report.add_argument("--codes-file", action="append", help="Text file containing fund codes")
    coverage_report.add_argument("--code", action="append", help="Fund code; can be repeated")
    coverage_report.add_argument("--fund-type", help="Filter by fund type (substring match)")
    coverage_report.add_argument(
        "--only-incomplete",
        action="store_true",
        help="Only return funds whose completeness < 1.0",
    )
    coverage_report.add_argument(
        "--min-completeness",
        type=float,
        default=0.0,
        help="Only return funds whose completeness >= this value (0-1)",
    )
    coverage_report.add_argument("--limit", type=int, help="Limit number of returned rows")
    coverage_report.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help="Output format (default: json)",
    )
    coverage_report.add_argument(
        "--output", help="Write to this file instead of stdout (extension drives format if --format is omitted)"
    )
    _add_common_db_arg(coverage_report)

    export = subparsers.add_parser("export", help="Export a persisted table")
    export.add_argument(
        "table",
        choices=[
            "funds",
            "nav_history",
            "snapshots",
            "stock_holdings",
            "fund_profiles",
            "bond_holdings",
            "industry_allocations",
            "fee_structures",
            "dividends",
            "splits",
            "fund_managers",
            "raw_responses",
            "sync_runs",
            "sync_failures",
        ],
    )
    export.add_argument("--fund-code")
    export.add_argument("--format", choices=["json", "csv"], default="json")
    export.add_argument("--output")
    _add_common_db_arg(export)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "list":
            rows = fund_data.fetch_fund_list(
                db_path=args.db,
                raw_text=_read_offline_raw(args.offline_raw),
                provider=args.provider,
            )
            _print_json(rows[: args.limit])
            return 0

        if args.command == "search":
            rows = fund_data.search_funds(
                args.keyword,
                db_path=args.db,
                raw_text=_read_offline_raw(args.offline_raw),
                provider=args.provider,
            )
            _print_json(rows[: args.limit])
            return 0

        if args.command == "nav":
            rows = fund_data.fetch_nav_history(
                args.code,
                start_date=args.start_date,
                end_date=args.end_date,
                page=args.page,
                per=args.per,
                db_path=args.db,
                raw_text=_read_offline_raw(args.offline_raw),
                provider=args.provider,
            )
            _print_json(rows)
            return 0

        if args.command == "snapshot":
            snapshot = fund_data.fetch_snapshot(
                args.code,
                db_path=args.db,
                raw_text=_read_offline_raw(args.offline_raw),
                provider=args.provider,
            )
            _print_json(snapshot)
            return 0

        if args.command == "holdings":
            rows = fund_data.fetch_stock_holdings(
                args.code,
                report_year=args.report_year,
                db_path=args.db,
                provider=args.provider,
            )
            _print_json(rows)
            return 0

        if args.command == "profile":
            profile = fund_data.fetch_profile(
                args.code,
                db_path=args.db,
                provider=args.provider,
            )
            _print_json(profile)
            return 0

        if args.command == "bonds":
            rows = fund_data.fetch_bond_holdings(
                args.code,
                report_year=args.report_year,
                db_path=args.db,
                provider=args.provider,
            )
            _print_json(rows)
            return 0

        if args.command == "industries":
            rows = fund_data.fetch_industry_allocations(
                args.code,
                report_year=args.report_year,
                db_path=args.db,
                provider=args.provider,
            )
            _print_json(rows)
            return 0

        if args.command == "fees":
            rows = fund_data.fetch_fee_structures(
                args.code,
                indicators=args.indicator,
                db_path=args.db,
                provider=args.provider,
            )
            _print_json(rows)
            return 0

        if args.command == "dividends":
            rows = fund_data.fetch_dividends(
                args.code,
                db_path=args.db,
                provider=args.provider,
            )
            _print_json(rows)
            return 0

        if args.command == "splits":
            rows = fund_data.fetch_splits(
                args.code,
                db_path=args.db,
                provider=args.provider,
            )
            _print_json(rows)
            return 0

        if args.command == "managers":
            rows = fund_data.fetch_fund_managers(
                args.code,
                db_path=args.db,
                provider=args.provider,
            )
            _print_json(rows)
            return 0

        if args.command == "sync":
            result = fund_data.sync_fund(
                args.code,
                start_date=args.start_date,
                end_date=args.end_date,
                page=args.page,
                per=args.per,
                db_path=args.db,
                provider=args.provider,
                include_holdings=args.include_holdings,
                include_profile=args.include_profile,
                include_bonds=args.include_bonds,
                include_industries=args.include_industries,
                include_fees=args.include_fees,
                include_distributions=args.include_distributions,
                include_managers=args.include_managers,
                include_all=args.include_all,
                report_year=args.report_year,
                fee_indicators=args.fee_indicator,
            )
            _print_json(result)
            return 0

        if args.command == "batch-sync":
            codes = []
            for codes_file in args.codes_file or []:
                codes.extend(fund_data.parse_fund_codes(Path(codes_file).read_text(encoding="utf-8")))
            codes.extend(fund_data.normalize_fund_codes(args.code or []))
            codes = fund_data.normalize_fund_codes(codes)
            result = fund_data.batch_sync_funds(
                codes,
                start_date=args.start_date,
                end_date=args.end_date,
                page=args.page,
                per=args.per,
                db_path=args.db,
                provider=args.provider,
                include_holdings=args.include_holdings,
                include_profile=args.include_profile,
                include_bonds=args.include_bonds,
                include_industries=args.include_industries,
                include_fees=args.include_fees,
                include_distributions=args.include_distributions,
                include_managers=args.include_managers,
                include_all=args.include_all,
                report_year=args.report_year,
                fee_indicators=args.fee_indicator,
                batch_id=args.batch_id,
                stop_on_error=args.stop_on_error,
                concurrency=args.concurrency,
                min_interval_seconds=args.min_interval_seconds,
            )
            _print_json(result)
            return 0

        if args.command == "export":
            rows = fund_data.export_table(args.table, db_path=args.db, fund_code=args.fund_code)
            text = fund_data.write_rows(rows, args.output, args.format)
            if not args.output:
                print(text)
            else:
                print(args.output)
            return 0

        if args.command == "coverage":
            rows = fund_data.coverage_rows(db_path=args.db, fund_code=args.fund_code)
            _print_json(rows)
            return 0

        if args.command == "coverage-report":
            codes: list[str] = []
            for codes_file in args.codes_file or []:
                codes.extend(
                    fund_data.parse_fund_codes(Path(codes_file).read_text(encoding="utf-8"))
                )
            codes.extend(fund_data.normalize_fund_codes(args.code or []))
            codes = fund_data.normalize_fund_codes(codes) or None
            rows = fund_data.coverage_report(
                db_path=args.db,
                codes=codes,
                fund_type=args.fund_type,
                only_incomplete=args.only_incomplete,
                min_completeness=args.min_completeness,
                limit=args.limit,
            )
            summary = {
                "total_funds": len(rows),
                "fully_covered": sum(1 for r in rows if r["completeness"] == 1.0),
                "average_completeness": round(
                    sum(r["completeness"] for r in rows) / len(rows), 4
                ) if rows else 0.0,
                "rows": rows,
            }
            output_path = args.output
            if output_path:
                if args.format == "csv":
                    text = fund_data.write_rows(rows, output_path, "csv")
                else:
                    output_path_path = Path(output_path)
                    output_path_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path_path.write_text(
                        json.dumps(summary, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    text = str(output_path_path)
                print(text)
            else:
                _print_json(summary)
            return 0

    except Exception as exc:
        print(f"fund-data error: {exc}", file=sys.stderr)
        return 1

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
