#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

try:
    from . import fund_cloud, fund_data
    from . import doctor as doctor_module
except ImportError:  # pragma: no cover - exercised by direct script execution
    import fund_cloud
    import fund_data
    import doctor as doctor_module  # type: ignore[no-redef]

# Apply the macOS proxy / IPv4 / sqlite-timeout patches BEFORE
# any module opens a network socket or sqlite connection.
# Idempotent; no-op on Linux / Windows runners.
from _net_compat import apply as _apply_net_compat  # noqa: E402

_apply_net_compat()

# Load `INVESTODAY_API_KEY` / `TUSHARE_TOKEN` / etc. from the
# project-root `.env` if not already exported in the shell.
# Stdlib-only, idempotent, no-op when the file is missing.
# Shell exports still win (loader uses ``os.environ.setdefault``
# semantics). See ``fund_data._env`` for the search order.
from fund_data._env import load_env  # noqa: E402

load_env()


PROVIDER_CHOICES = ["auto", "eastmoney", "akshare", "investoday", "tushare"]

def _read_offline_raw(path: str | None) -> str | None:
    if not path:
        return None
    return Path(path).read_text(encoding="utf-8")


def _print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _add_json_arg(parser: argparse.ArgumentParser) -> None:
    """Add the ``--json`` flag that every per-fund read
    command shares.

    Contract: per-fund read commands (search / list / nav /
    snapshot / holdings / profile / bonds / industries / fees /
    dividends / splits / managers / coverage / coverage-report
    / export / cloud-status) ALWAYS emit a single JSON
    document on stdout -- the flag only chooses between
    indented (default, human-readable) and compact
    (single-line, agent-friendly / ``jq``-friendly). The
    contract is the same either way; ``--json`` is for
    pipelines that want to skip the whitespace and trust the
    parse.

    Errors always go to stderr regardless of the flag, and
    the exit code mirrors the success flag (``0`` for
    success, ``1`` for a data-plane error, ``2`` for an
    argparse / config error). The wrapper at the bottom of
    this file centralizes the exit code logic so the per-command
    handlers don't have to think about it.
    """
    parser.add_argument(
        "--json",
        action="store_true",
        help=(
            "Emit compact single-line JSON (no indent). Default is "
            "indented JSON for human readability. Either way the "
            "output is a single JSON document on stdout; errors go "
            "to stderr; exit code is 0 / 1 / 2 for success / data "
            "error / argparse error."
        ),
    )


def _emit(value, args) -> None:
    """Write ``value`` as JSON to stdout, respecting ``--json``.

    See :func:`_add_json_arg` for the full contract. The two
    modes differ only in whitespace; both are valid JSON and
    both round-trip through ``json.loads``. The compact mode
    is what agents pipe to ``jq`` / parse with the standard
    library; the indented mode is what humans eyeball.
    """
    indent = None if getattr(args, "json", False) else 2
    sys.stdout.write(json.dumps(value, ensure_ascii=False, indent=indent))
    sys.stdout.write("\n")


def _write_json_to_file(path: str, value) -> None:
    """Write a JSON payload to ``path``, creating parent directories
    on demand. Used by subcommands that want a structured
    report on disk for later inspection (e.g. an agent's
    nightly health check that archives the cloud cache status)."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


LOG_LEVEL_CHOICES = ["DEBUG", "INFO", "WARNING", "ERROR"]


def _setup_logging(quiet: bool, level_name: str) -> None:
    """Configure the root logger once at CLI startup.

    ``--quiet`` raises the effective level to WARNING so per-fund
    INFO progress lines (e.g. ``syncing 110022...``) don't drown
    out the structured JSON that agents pipe to ``jq``. The
    explicit ``--log-level`` always wins when set.
    """
    level = getattr(logging, level_name.upper(), logging.INFO)
    if quiet:
        level = max(level, logging.WARNING)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )


def _maybe_bootstrap_cloud(args: argparse.Namespace) -> None:
    """Prefer the project OSS bundle for agent-facing data commands."""
    if args.command == "cloud":
        return
    if getattr(args, "db", None):
        return
    result = fund_cloud.ensure_project_bundle()
    if result.get("fallback") == "api" and result.get("error"):
        logging.getLogger("fund_data").warning(
            "cloud bundle unavailable; falling back to provider APIs: %s",
            result["error"],
        )


def _add_common_db_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db", help="SQLite database path. Defaults to fund-data/data/fund_data.sqlite"
    )


def _add_offline_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--offline-raw", help="Read a saved raw response instead of calling the network"
    )


def _add_provider_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--provider",
        choices=PROVIDER_CHOICES,
        default="auto",
        help="Data provider. auto tries configured structured sources first, then free fallbacks.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Search, fetch, persist, and export Chinese fund data."
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help=(
            "Suppress INFO-level progress lines on stderr. "
            "Warnings and errors still surface. Useful when piping "
            "the JSON output to jq, awk, or another agent."
        ),
    )
    parser.add_argument(
        "--log-level",
        choices=LOG_LEVEL_CHOICES,
        default="INFO",
        help=(
            "Minimum severity for stderr log output. "
            "Overrides --quiet when set explicitly to DEBUG."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="Fetch the full fund code list")
    list_parser.add_argument("--limit", type=int, default=50)
    _add_common_db_arg(list_parser)
    _add_offline_arg(list_parser)
    _add_provider_arg(list_parser)
    _add_json_arg(list_parser)

    search = subparsers.add_parser("search", help="Search funds by keyword or code")
    search.add_argument("keyword")
    search.add_argument("--limit", type=int, default=20)
    _add_common_db_arg(search)
    _add_offline_arg(search)
    _add_provider_arg(search)
    _add_json_arg(search)

    nav = subparsers.add_parser("nav", help="Fetch historical NAV rows")
    nav.add_argument("code")
    nav.add_argument("--start-date")
    nav.add_argument("--end-date")
    nav.add_argument("--page", type=int, default=1)
    nav.add_argument("--per", type=int, default=20)
    nav.add_argument(
        "--refresh",
        action="store_true",
        help="Skip the local/OSS NAV cache and refresh from the provider chain.",
    )
    _add_common_db_arg(nav)
    _add_offline_arg(nav)
    _add_provider_arg(nav)
    _add_json_arg(nav)

    snapshot = subparsers.add_parser("snapshot", help="Fetch fund snapshot metadata")
    snapshot.add_argument("code")
    _add_common_db_arg(snapshot)
    _add_offline_arg(snapshot)
    _add_provider_arg(snapshot)
    _add_json_arg(snapshot)

    holdings = subparsers.add_parser("holdings", help="Fetch fund stock holdings")
    holdings.add_argument("code")
    holdings.add_argument("--report-year")
    _add_common_db_arg(holdings)
    _add_provider_arg(holdings)
    _add_json_arg(holdings)

    profile = subparsers.add_parser("profile", help="Fetch fund profile/basic archive data")
    profile.add_argument("code")
    _add_common_db_arg(profile)
    _add_provider_arg(profile)
    _add_json_arg(profile)

    bonds = subparsers.add_parser("bonds", help="Fetch fund bond holdings")
    bonds.add_argument("code")
    bonds.add_argument("--report-year")
    _add_common_db_arg(bonds)
    _add_provider_arg(bonds)
    _add_json_arg(bonds)

    industries = subparsers.add_parser("industries", help="Fetch fund industry allocations")
    industries.add_argument("code")
    industries.add_argument("--report-year")
    _add_common_db_arg(industries)
    _add_provider_arg(industries)
    _add_json_arg(industries)

    fees = subparsers.add_parser("fees", help="Fetch fund fee structures")
    fees.add_argument("code")
    fees.add_argument(
        "--indicator", action="append", help="Filter by fee indicator (repeatable)"
    )
    _add_common_db_arg(fees)
    _add_provider_arg(fees)
    _add_json_arg(fees)

    dividends = subparsers.add_parser("dividends", help="Fetch fund dividends")
    dividends.add_argument("code")
    _add_common_db_arg(dividends)
    _add_provider_arg(dividends)
    _add_json_arg(dividends)

    splits = subparsers.add_parser("splits", help="Fetch fund share splits")
    splits.add_argument("code")
    _add_common_db_arg(splits)
    _add_provider_arg(splits)
    _add_json_arg(splits)

    managers = subparsers.add_parser("managers", help="Fetch fund manager records")
    managers.add_argument("code", nargs="?")
    _add_common_db_arg(managers)
    _add_provider_arg(managers)
    _add_json_arg(managers)

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
    sync.add_argument(
        "--include-snapshots",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Pull the snapshot row (default: True).",
    )
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
    # ``sync_fund`` already pulls the snapshot row on every call;
    # this flag is the explicit opt-out so the OpenClaw completion
    # plan builder can match the real CLI contract. Default True
    # keeps the historical behaviour: every batch-sync refreshes
    # the snapshot row alongside nav_history.
    batch_sync.add_argument(
        "--include-snapshots",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Pull snapshot rows during batch sync (default: True).",
    )
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
    _add_json_arg(coverage)

    doctor = subparsers.add_parser(
        "doctor",
        help="Run the environment health check (db, akshare, providers, "
        "sync_failures, coverage). Always emits JSON on stdout -- "
        "agent-friendly; the exit code mirrors the overall ok flag.",
    )
    doctor.add_argument("--db", help="SQLite path. Defaults to fund-data/data/fund_data.sqlite")
    doctor.add_argument("--venv", help="AkShare virtual environment path")
    doctor.add_argument(
        "--skip-network",
        action="store_true",
        help="Skip the live Eastmoney reachability probe (useful in CI).",
    )
    doctor.add_argument(
        "--skip-sync-state",
        action="store_true",
        help=(
            "Skip the sync_state checks (sync_failures / coverage / "
            "backfill_stale). Useful when doctor runs against a "
            "query-only DB that excludes the sync_* tables."
        ),
    )
    doctor.add_argument(
        "--quiet",
        action="store_true",
        help="Compact JSON (no indent) and skip the human-readable FAIL banner.",
    )
    doctor.add_argument("--output", help="Write the JSON report to this file instead of stdout")

    health_check = subparsers.add_parser("health-check", help="Inspect one fund and recommend missing-data actions")
    health_check.add_argument("code")
    health_check.add_argument("--max-age-hours", type=float, default=36.0)
    # Default ``include_structural=True`` here so the CLI matches
    # the Python ``check_fund_health()`` default -- an operator
    # asking "what is wrong with 110022?" usually wants to see
    # the structural-empty / naturally-sparse context too.
    health_check.add_argument(
        "--include-structural",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    health_check.add_argument("--output")
    _add_common_db_arg(health_check)

    self_audit = subparsers.add_parser("self-audit", help="Build a prioritized read-only data remediation queue")
    self_audit.add_argument("--code", action="append", help="Fund code; can be repeated")
    self_audit.add_argument("--codes-file", action="append", help="File containing fund codes")
    self_audit.add_argument("--fund-type")
    self_audit.add_argument("--max-age-hours", type=float, default=36.0)
    self_audit.add_argument("--include-structural", action="store_true")
    self_audit.add_argument("--limit", type=int)
    self_audit.add_argument("--output")
    _add_common_db_arg(self_audit)

    completion_plan = subparsers.add_parser(
        "completion-plan",
        help="Convert a self-audit queue JSON into a bounded batch plan (read-only)",
    )
    completion_plan.add_argument("--queue", required=True, help="Path to the self-audit queue JSON")
    completion_plan.add_argument("--config", help="Path to the OpenClaw policy JSON")
    completion_plan.add_argument("--output", help="Write the plan JSON to this file")

    completion_run = subparsers.add_parser(
        "completion-run",
        help="Execute a completion plan. Refuses to mutate without --confirm-execute and a non-audit policy mode.",
    )
    completion_run.add_argument("--plan", required=True, help="Path to the completion plan JSON")
    completion_run.add_argument("--config", help="Path to the OpenClaw policy JSON")
    completion_run.add_argument(
        "--confirm-execute",
        action="store_true",
        help="Required for any non-dry-run execution.",
    )
    completion_run.add_argument(
        "--output", help="Write the execution report JSON to this file (default: alongside the plan)"
    )

    completion_verify = subparsers.add_parser(
        "completion-verify",
        help="Compare a before/after self-audit queue and the execution report",
    )
    completion_verify.add_argument("--before", required=True, help="Path to the before queue JSON")
    completion_verify.add_argument("--after", required=True, help="Path to the after queue JSON")
    completion_verify.add_argument(
        "--execution", required=True, help="Path to the execution.json report"
    )
    completion_verify.add_argument(
        "--run-doctor",
        action="store_true",
        help="Shell out to doctor.py --skip-network --quiet and fold the result into the report",
    )
    completion_verify.add_argument("--output", help="Write the verification report JSON to this file")

    coverage_report = subparsers.add_parser(
        "coverage-report",
        help="Show completeness score for funds with detailed missing-dataset breakdown",
    )
    coverage_report.add_argument(
        "--codes-file", action="append", help="Text file containing fund codes"
    )
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
        "--output",
        help="Write to this file instead of stdout (extension drives format if --format is omitted)",
    )
    _add_common_db_arg(coverage_report)
    _add_json_arg(coverage_report)

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
    _add_json_arg(export)

    cloud = subparsers.add_parser("cloud", help="Build, pull, and inspect cloud data bundles")
    cloud_subparsers = cloud.add_subparsers(dest="cloud_command", required=True)

    build_bundle = cloud_subparsers.add_parser(
        "build-bundle",
        help="Build a compressed query-only SQLite bundle for OSS/static hosting",
    )
    build_bundle.add_argument(
        "--source-db",
        default=str(fund_data.DEFAULT_DB_PATH),
        help="Full local SQLite database to package.",
    )
    build_bundle.add_argument(
        "--output-dir",
        required=True,
        help="Release directory for fund_data_query.sqlite.gz and manifest.json.",
    )
    build_bundle.add_argument(
        "--base-url",
        required=True,
        help="Public HTTPS URL prefix for this release directory.",
    )
    build_bundle.add_argument("--version", help="Bundle version, such as 2026-06-01.")
    build_bundle.add_argument(
        "--manifest-output",
        help="Optional path for the published current/manifest.json file.",
    )
    build_bundle.add_argument(
        "--output",
        help="Write the JSON result to this file instead of stdout. "
        "Parent directories are created on demand.",
    )

    archive_full = cloud_subparsers.add_parser(
        "archive-full",
        help="Create a compressed full SQLite archive for private OSS storage",
    )
    archive_full.add_argument(
        "--source-db",
        default=str(fund_data.DEFAULT_DB_PATH),
        help="Full local SQLite database to archive.",
    )
    archive_full.add_argument(
        "--output-dir",
        required=True,
        help="Archive directory for fund_data_full.sqlite.gz and manifest.json.",
    )
    archive_full.add_argument(
        "--base-url",
        help="Private OSS URI or HTTPS URL prefix for this archive directory.",
    )
    archive_full.add_argument("--version", help="Archive version, such as 2026-06-02.")
    archive_full.add_argument(
        "--manifest-output",
        help="Optional path for the archive manifest.json file.",
    )
    archive_full.add_argument(
        "--output",
        help="Write the JSON result to this file instead of stdout.",
    )

    pull = cloud_subparsers.add_parser("pull", help="Download and install a cloud query bundle")
    pull.add_argument(
        "--manifest-url",
        default=fund_cloud.default_manifest_url(),
        help=(
            "HTTPS/file URL for manifest.json. Defaults to "
            "FUND_DATA_MANIFEST_URL or the project OSS manifest."
        ),
    )
    pull.add_argument("--cache-dir", help="Local cache directory. Defaults to ~/.cache/fund-data.")
    pull.add_argument(
        "--output",
        help="Write the JSON result to this file instead of stdout.",
    )

    status = cloud_subparsers.add_parser("status", help="Show local cloud cache status")
    status.add_argument(
        "--cache-dir", help="Local cache directory. Defaults to ~/.cache/fund-data."
    )
    status.add_argument("--manifest-url", help="Optional remote manifest URL to compare against.")
    status.add_argument(
        "--output",
        help="Write the JSON result to this file instead of stdout.",
    )

    upload = cloud_subparsers.add_parser(
        "upload",
        help="Upload a built release directory to OSS via ossutil. "
        "Pushes the gzip db + sha256 sidecar to "
        "{prefix}/releases/{version}/ and, when --manifest is given, "
        "the manifest to {prefix}/current/manifest.json so cloud pull "
        "consumers see the new version without re-deploying.",
    )
    upload.add_argument(
        "--release-dir",
        required=True,
        help="Directory produced by `cloud build-bundle` (must contain "
        "fund_data_query.sqlite.gz and the matching .sha256 sidecar).",
    )
    upload.add_argument(
        "--bucket",
        default=fund_cloud.DEFAULT_BUCKET,
        help=f"OSS bucket name (default: {fund_cloud.DEFAULT_BUCKET}).",
    )
    upload.add_argument(
        "--region",
        default=fund_cloud.DEFAULT_REGION,
        help=f"OSS region (default: {fund_cloud.DEFAULT_REGION}).",
    )
    upload.add_argument(
        "--prefix",
        default=fund_cloud.DEFAULT_PREFIX,
        help=f"OSS object key prefix (default: {fund_cloud.DEFAULT_PREFIX}).",
    )
    upload.add_argument(
        "--manifest",
        help="Optional path to a manifest.json to publish at "
        "{prefix}/current/manifest.json. The manifest URL is "
        "included in the JSON response.",
    )
    upload.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip the ossutil calls and just return the planned upload list.",
    )
    upload.add_argument(
        "--output",
        help="Write the JSON result to this file instead of stdout.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.quiet, args.log_level)
    _maybe_bootstrap_cloud(args)

    try:
        if args.command == "list":
            rows = fund_data.fetch_fund_list(
                db_path=args.db,
                raw_text=_read_offline_raw(args.offline_raw),
                provider=args.provider,
            )
            _emit(rows[: args.limit], args)
            return 0

        if args.command == "search":
            rows = fund_data.search_funds(
                args.keyword,
                db_path=args.db,
                raw_text=_read_offline_raw(args.offline_raw),
                provider=args.provider,
            )
            _emit(rows[: args.limit], args)
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
                cache=not args.refresh,
            )
            _emit(rows, args)
            return 0

        if args.command == "snapshot":
            snapshot = fund_data.fetch_snapshot(
                args.code, db_path=args.db, provider=args.provider
            )
            _emit(snapshot, args)
            return 0

        if args.command == "holdings":
            rows = fund_data.fetch_stock_holdings(
                args.code,
                report_year=args.report_year,
                db_path=args.db,
                provider=args.provider,
            )
            _emit(rows, args)
            return 0

        if args.command == "profile":
            profile = fund_data.fetch_profile(
                args.code, db_path=args.db, provider=args.provider
            )
            _emit(profile, args)
            return 0

        if args.command == "bonds":
            rows = fund_data.fetch_bond_holdings(
                args.code,
                report_year=args.report_year,
                db_path=args.db,
                provider=args.provider,
            )
            _emit(rows, args)
            return 0

        if args.command == "industries":
            rows = fund_data.fetch_industry_allocations(
                args.code,
                report_year=args.report_year,
                db_path=args.db,
                provider=args.provider,
            )
            _emit(rows, args)
            return 0

        if args.command == "fees":
            rows = fund_data.fetch_fee_structures(
                args.code,
                indicators=args.indicator,
                db_path=args.db,
                provider=args.provider,
            )
            _emit(rows, args)
            return 0

        if args.command == "dividends":
            rows = fund_data.fetch_dividends(
                args.code, db_path=args.db, provider=args.provider
            )
            _emit(rows, args)
            return 0

        if args.command == "splits":
            rows = fund_data.fetch_splits(
                args.code, db_path=args.db, provider=args.provider
            )
            _emit(rows, args)
            return 0

        if args.command == "managers":
            rows = fund_data.fetch_fund_managers(
                args.code, db_path=args.db, provider=args.provider
            )
            _emit(rows, args)
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
                include_snapshots=getattr(args, "include_snapshots", True),
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
            _emit(result, args)
            return 0

        if args.command == "batch-sync":
            codes = []
            for codes_file in args.codes_file or []:
                codes.extend(
                    fund_data.parse_fund_codes(Path(codes_file).read_text(encoding="utf-8"))
                )
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
                include_snapshots=args.include_snapshots,
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
            _emit(result, args)
            return 0

        if args.command == "health-check":
            payload = fund_data.check_fund_health(
                args.code,
                db_path=args.db,
                max_age_hours=args.max_age_hours,
                include_structural=args.include_structural,
            )
            if args.output:
                _write_json_to_file(args.output, payload)
                print(args.output)
            else:
                _print_json(payload)
            return 0

        if args.command == "self-audit":
            codes = []
            for codes_file in args.codes_file or []:
                codes.extend(fund_data.parse_fund_codes(Path(codes_file).read_text(encoding="utf-8")))
            codes.extend(fund_data.normalize_fund_codes(args.code or []))
            payload = fund_data.build_self_audit_queue(
                db_path=args.db,
                codes=codes or None,
                fund_type=args.fund_type,
                max_age_hours=args.max_age_hours,
                include_structural=args.include_structural,
                limit=args.limit,
            )
            if args.output:
                _write_json_to_file(args.output, payload)
                print(args.output)
            else:
                _print_json(payload)
            return 0

        if args.command == "completion-plan":
            plan = fund_data.build_completion_plan(
                queue_path=args.queue,
                config_path=args.config,
                output_path=args.output,
            )
            if args.output:
                print(args.output)
            else:
                _print_json(plan)
            return 0

        if args.command == "completion-run":
            execution = fund_data.run_completion_plan(
                plan_path=args.plan,
                config_path=args.config,
                confirm_execute=args.confirm_execute,
            )
            if args.output:
                _write_json_to_file(args.output, execution)
                print(args.output)
            else:
                _print_json(execution)
            # Dry-run (no --confirm-execute) is the default and
            # always returns 0 so the operator can read the plan
            # preview. A confirmed run that still refuses to execute
            # (audit_only, budget overflow, lock held) exits 2 so
            # CI workflows can detect the refusal without parsing
            # the JSON payload.
            if args.confirm_execute and not execution.get("executed"):
                return 2
            return 0

        if args.command == "completion-verify":
            report = fund_data.verify_completion_run(
                before_queue_path=args.before,
                after_queue_path=args.after,
                execution_path=args.execution,
            )
            # Optionally shell out to doctor.py to fold the health
            # check into the report; spec calls for a doctor run
            # *before* verify, so this is a convenience for CI.
            if getattr(args, "run_doctor", False):
                import subprocess
                doctor_proc = subprocess.run(
                    [
                        ".venv-akshare/bin/python",
                        "fund-data/scripts/doctor.py",
                        "--skip-network",
                        "--quiet",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                try:
                    doctor_payload = json.loads(doctor_proc.stdout)
                except json.JSONDecodeError:
                    doctor_payload = {"raw_stdout": doctor_proc.stdout, "returncode": doctor_proc.returncode}
                report["doctor_ok"] = bool(doctor_payload.get("ok"))
                report["doctor"] = doctor_payload
                # A doctor failure flips the publish gate off.
                if not report["doctor_ok"]:
                    report["publish_recommended"] = False
            if args.output:
                _write_json_to_file(args.output, report)
                print(args.output)
            else:
                _print_json(report)
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
            _emit(rows, args)
            return 0

        if args.command == "doctor":
            argv = []
            if args.db:
                argv.extend(["--db", args.db])
            if args.venv:
                argv.extend(["--venv", args.venv])
            if args.skip_network:
                argv.append("--skip-network")
            if args.quiet:
                argv.append("--quiet")
            if args.output:
                argv.extend(["--output", args.output])
            return doctor_module.main(argv)

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
                "average_completeness": (
                    round(sum(r["completeness"] for r in rows) / len(rows), 4) if rows else 0.0
                ),
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
                _emit(summary, args)
            return 0

        if args.command == "cloud":
            if args.cloud_command == "build-bundle":
                result = fund_cloud.build_bundle(
                    source_db=args.source_db,
                    output_dir=args.output_dir,
                    base_url=args.base_url,
                    version=args.version,
                    manifest_output=args.manifest_output,
                )
                payload = fund_cloud.json_ready(result)
                if args.output:
                    _write_json_to_file(args.output, payload)
                else:
                    _emit(payload, args)
                return 0
            if args.cloud_command == "archive-full":
                result = fund_cloud.archive_full(
                    source_db=args.source_db,
                    output_dir=args.output_dir,
                    base_url=args.base_url,
                    version=args.version,
                    manifest_output=args.manifest_output,
                )
                payload = fund_cloud.json_ready(result)
                if args.output:
                    _write_json_to_file(args.output, payload)
                else:
                    _print_json(payload)
                return 0
            if args.cloud_command == "pull":
                payload = fund_cloud.pull_bundle(
                    args.manifest_url,
                    cache_dir=args.cache_dir,
                )
                if args.output:
                    _write_json_to_file(args.output, payload)
                else:
                    _print_json(payload)
                return 0
            if args.cloud_command == "status":
                payload = fund_cloud.status(
                    cache_dir=args.cache_dir,
                    manifest_url=args.manifest_url,
                )
                if args.output:
                    _write_json_to_file(args.output, payload)
                else:
                    _print_json(payload)
                return 0
            if args.cloud_command == "upload":
                result = fund_cloud.upload_to_oss(
                    release_dir=args.release_dir,
                    bucket=args.bucket,
                    region=args.region,
                    prefix=args.prefix,
                    manifest_path=args.manifest,
                    dry_run=args.dry_run,
                )
                payload = result.to_dict()
                if args.output:
                    _write_json_to_file(args.output, payload)
                else:
                    _print_json(payload)
                return 0

    except Exception as exc:
        print(f"fund-data error: {exc}", file=sys.stderr)
        return 1

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
