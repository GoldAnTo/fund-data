#!/usr/bin/env python3
"""MCP stdio server for the fund-data skill.

The server is dependency-free and speaks newline-delimited JSON-RPC 2.0
over stdin/stdout, which is the standard MCP stdio transport. It exposes
the existing ``fund_data`` Python helpers as MCP tools so OpenClaw,
Claude, Codex, and other MCP-capable agents can call the local fund data
base without shelling out to ``fund_cli.py``.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

try:
    from . import fund_cloud, fund_data
except ImportError:  # pragma: no cover - exercised by direct script execution
    import fund_cloud

    import fund_data

SERVER_NAME = "fund-data"
SERVER_VERSION = "0.1.0"
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = {
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
}

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603


def _string_schema(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def _integer_schema(description: str, *, minimum: int | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "integer", "description": description}
    if minimum is not None:
        schema["minimum"] = minimum
    return schema


def _number_schema(description: str, *, minimum: float | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "number", "description": description}
    if minimum is not None:
        schema["minimum"] = minimum
    return schema


def _boolean_schema(description: str) -> dict[str, Any]:
    return {"type": "boolean", "description": description}


def _array_schema(description: str, item_schema: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "description": description, "items": item_schema}


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        },
    }


COMMON_ARGS = {
    "db": _string_schema("SQLite database path. Defaults to fund-data/data/fund_data.sqlite."),
    "provider": {
        "type": "string",
        "enum": ["auto", "eastmoney", "akshare", "investoday", "tushare"],
        "description": "Provider chain to use. Defaults to auto.",
    },
}


TOOLS: list[dict[str, Any]] = [
    _tool(
        "fund_search",
        "Search Chinese public funds by keyword, pinyin, name, or 6-digit code.",
        {
            **COMMON_ARGS,
            "keyword": _string_schema("Fund keyword, name, pinyin, theme, or 6-digit code."),
            "limit": _integer_schema("Maximum rows to return.", minimum=1),
        },
        required=["keyword"],
    ),
    _tool(
        "fund_list",
        "Fetch and persist the full fund code list.",
        {**COMMON_ARGS, "limit": _integer_schema("Maximum rows to return.", minimum=1)},
    ),
    _tool(
        "fund_nav_history",
        "Fetch and persist historical NAV rows for one fund.",
        {
            **COMMON_ARGS,
            "code": _string_schema("6-digit fund code."),
            "start_date": _string_schema("Start date as YYYY-MM-DD."),
            "end_date": _string_schema("End date as YYYY-MM-DD."),
            "page": _integer_schema("Source page number.", minimum=1),
            "per": _integer_schema("Rows per source page.", minimum=1),
        },
        required=["code"],
    ),
    _tool(
        "fund_snapshot",
        "Fetch and persist Eastmoney snapshot metadata for one fund.",
        {**COMMON_ARGS, "code": _string_schema("6-digit fund code.")},
        required=["code"],
    ),
    _tool(
        "fund_profile",
        "Fetch and persist profile/basic archive data for one fund.",
        {**COMMON_ARGS, "code": _string_schema("6-digit fund code.")},
        required=["code"],
    ),
    _tool(
        "fund_stock_holdings",
        "Fetch and persist disclosed stock holdings for one fund.",
        {
            **COMMON_ARGS,
            "code": _string_schema("6-digit fund code."),
            "report_year": _string_schema("Report year such as 2024."),
        },
        required=["code"],
    ),
    _tool(
        "fund_bond_holdings",
        "Fetch and persist disclosed bond holdings for one fund.",
        {
            **COMMON_ARGS,
            "code": _string_schema("6-digit fund code."),
            "report_year": _string_schema("Report year such as 2024."),
        },
        required=["code"],
    ),
    _tool(
        "fund_industry_allocations",
        "Fetch and persist industry allocation rows for one fund.",
        {
            **COMMON_ARGS,
            "code": _string_schema("6-digit fund code."),
            "report_year": _string_schema("Report year such as 2024."),
        },
        required=["code"],
    ),
    _tool(
        "fund_fee_structures",
        "Fetch and persist fee structure rows for one fund.",
        {
            **COMMON_ARGS,
            "code": _string_schema("6-digit fund code."),
            "indicators": _array_schema(
                "Optional fee indicators, such as 申购费率.",
                _string_schema("Fee indicator"),
            ),
        },
        required=["code"],
    ),
    _tool(
        "fund_dividends",
        "Fetch and persist dividend/distribution history for one fund.",
        {**COMMON_ARGS, "code": _string_schema("6-digit fund code.")},
        required=["code"],
    ),
    _tool(
        "fund_splits",
        "Fetch and persist split/conversion history for one fund.",
        {**COMMON_ARGS, "code": _string_schema("6-digit fund code.")},
        required=["code"],
    ),
    _tool(
        "fund_managers",
        "Fetch and persist fund-manager records, optionally filtered by fund code.",
        {**COMMON_ARGS, "code": _string_schema("Optional 6-digit fund code.")},
    ),
    _tool(
        "fund_sync",
        "Run the per-fund sync workflow and persist snapshot, NAV, and optional datasets.",
        {
            **COMMON_ARGS,
            "code": _string_schema("6-digit fund code."),
            "start_date": _string_schema("Start date as YYYY-MM-DD."),
            "end_date": _string_schema("End date as YYYY-MM-DD."),
            "page": _integer_schema("Source page number.", minimum=1),
            "per": _integer_schema("Rows per source page.", minimum=1),
            "include_holdings": _boolean_schema("Include stock holdings."),
            "include_profile": _boolean_schema("Include profile data."),
            "include_bonds": _boolean_schema("Include bond holdings."),
            "include_industries": _boolean_schema("Include industry allocations."),
            "include_fees": _boolean_schema("Include fee structures."),
            "include_distributions": _boolean_schema("Include dividends and splits."),
            "include_managers": _boolean_schema("Include fund-manager records."),
            "include_all": _boolean_schema("Include every optional dataset."),
            "report_year": _string_schema("Report year such as 2024."),
            "fee_indicators": _array_schema("Fee indicators.", _string_schema("Fee indicator")),
        },
        required=["code"],
    ),
    _tool(
        "fund_batch_sync",
        "Run sync for repeated fund codes and record failures in the local queue.",
        {
            **COMMON_ARGS,
            "codes": _array_schema("Fund codes to sync.", _string_schema("6-digit fund code")),
            "start_date": _string_schema("Start date as YYYY-MM-DD."),
            "end_date": _string_schema("End date as YYYY-MM-DD."),
            "page": _integer_schema("Source page number.", minimum=1),
            "per": _integer_schema("Rows per source page.", minimum=1),
            "include_all": _boolean_schema("Include every optional dataset."),
            "report_year": _string_schema("Report year such as 2024."),
            "fee_indicators": _array_schema("Fee indicators.", _string_schema("Fee indicator")),
            "batch_id": _string_schema("Optional stable batch id."),
            "stop_on_error": _boolean_schema("Stop after the first hard error."),
            "concurrency": _integer_schema("Parallel fund fetches.", minimum=1),
            "min_interval_seconds": _number_schema(
                "Minimum interval between HTTP calls.", minimum=0
            ),
        },
        required=["codes"],
    ),
    _tool(
        "fund_coverage",
        "Return local table coverage rows, optionally for one fund.",
        {"db": COMMON_ARGS["db"], "fund_code": _string_schema("Optional 6-digit fund code.")},
    ),
    _tool(
        "fund_coverage_report",
        "Return per-fund completeness rows with missing-dataset details.",
        {
            "db": COMMON_ARGS["db"],
            "codes": _array_schema("Optional fund codes.", _string_schema("6-digit fund code")),
            "fund_type": _string_schema("Filter by fund type substring."),
            "only_incomplete": _boolean_schema("Only return rows whose completeness is below 1."),
            "min_completeness": _number_schema("Minimum completeness score in [0, 1].", minimum=0),
            "limit": _integer_schema("Maximum rows to return.", minimum=1),
        },
    ),
    _tool(
        "fund_export",
        "Export persisted rows from a supported local SQLite table.",
        {
            "db": COMMON_ARGS["db"],
            "table": {
                "type": "string",
                "enum": [
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
                "description": "Table to export.",
            },
            "fund_code": _string_schema("Optional 6-digit fund code filter."),
            "limit": _integer_schema("Maximum rows to return.", minimum=1),
        },
        required=["table"],
    ),
    _tool(
        "fund_cloud_status",
        "Inspect the local fund-data cloud cache and optionally compare it with a remote manifest.",
        {
            "cache_dir": _string_schema("Optional local cloud cache directory."),
            "manifest_url": _string_schema("Optional remote manifest URL to compare against."),
        },
    ),
]


def _json_response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _json_error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _tool_result(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    structured = {"rows": payload, "count": len(payload)} if isinstance(payload, list) else payload
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, indent=2),
            }
        ],
        "structuredContent": structured,
        "isError": is_error,
    }


def _args(params: dict[str, Any]) -> dict[str, Any]:
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise ValueError("params.arguments must be an object")
    return arguments


def _required_str(arguments: dict[str, Any], name: str) -> str:
    value = arguments.get(name)
    if value is None or value == "":
        raise ValueError(f"missing required argument: {name}")
    return str(value)


def _optional_str(arguments: dict[str, Any], name: str) -> str | None:
    value = arguments.get(name)
    return None if value is None or value == "" else str(value)


def _optional_int(arguments: dict[str, Any], name: str, default: int | None = None) -> int | None:
    value = arguments.get(name, default)
    return None if value is None else int(value)


def _optional_float(
    arguments: dict[str, Any], name: str, default: float | None = None
) -> float | None:
    value = arguments.get(name, default)
    return None if value is None else float(value)


def _optional_bool(arguments: dict[str, Any], name: str, default: bool = False) -> bool:
    value = arguments.get(name, default)
    return bool(value)


def _optional_str_list(arguments: dict[str, Any], name: str) -> list[str] | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return [str(item) for item in value]


def _db(arguments: dict[str, Any]) -> str | Path | None:
    return _optional_str(arguments, "db")


def _provider(arguments: dict[str, Any]) -> str:
    return _optional_str(arguments, "provider") or fund_data.PROVIDER_AUTO


def _maybe_bootstrap_cloud(arguments: dict[str, Any]) -> None:
    if _optional_str(arguments, "db"):
        return
    fund_cloud.ensure_project_bundle()


def _limit(rows: list[dict[str, Any]], arguments: dict[str, Any]) -> list[dict[str, Any]]:
    limit = _optional_int(arguments, "limit")
    return rows[:limit] if limit else rows


def _call_fund_search(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    rows = fund_data.search_funds(
        _required_str(arguments, "keyword"),
        db_path=_db(arguments),
        provider=_provider(arguments),
    )
    return _limit(rows, arguments)


def _call_fund_list(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    rows = fund_data.fetch_fund_list(db_path=_db(arguments), provider=_provider(arguments))
    return _limit(rows, arguments)


def _call_fund_nav_history(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    return fund_data.fetch_nav_history(
        _required_str(arguments, "code"),
        start_date=_optional_str(arguments, "start_date"),
        end_date=_optional_str(arguments, "end_date"),
        page=_optional_int(arguments, "page", 1) or 1,
        per=_optional_int(arguments, "per", 20) or 20,
        db_path=_db(arguments),
        provider=_provider(arguments),
    )


def _call_fund_snapshot(arguments: dict[str, Any]) -> dict[str, Any]:
    return fund_data.fetch_snapshot(
        _required_str(arguments, "code"), db_path=_db(arguments), provider=_provider(arguments)
    )


def _call_fund_profile(arguments: dict[str, Any]) -> dict[str, Any]:
    return fund_data.fetch_profile(
        _required_str(arguments, "code"), db_path=_db(arguments), provider=_provider(arguments)
    )


def _call_fund_stock_holdings(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    return fund_data.fetch_stock_holdings(
        _required_str(arguments, "code"),
        report_year=_optional_str(arguments, "report_year"),
        db_path=_db(arguments),
        provider=_provider(arguments),
    )


def _call_fund_bond_holdings(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    return fund_data.fetch_bond_holdings(
        _required_str(arguments, "code"),
        report_year=_optional_str(arguments, "report_year"),
        db_path=_db(arguments),
        provider=_provider(arguments),
    )


def _call_fund_industry_allocations(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    return fund_data.fetch_industry_allocations(
        _required_str(arguments, "code"),
        report_year=_optional_str(arguments, "report_year"),
        db_path=_db(arguments),
        provider=_provider(arguments),
    )


def _call_fund_fee_structures(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    return fund_data.fetch_fee_structures(
        _required_str(arguments, "code"),
        indicators=_optional_str_list(arguments, "indicators"),
        db_path=_db(arguments),
        provider=_provider(arguments),
    )


def _call_fund_dividends(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    return fund_data.fetch_dividends(
        _required_str(arguments, "code"), db_path=_db(arguments), provider=_provider(arguments)
    )


def _call_fund_splits(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    return fund_data.fetch_splits(
        _required_str(arguments, "code"), db_path=_db(arguments), provider=_provider(arguments)
    )


def _call_fund_managers(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    return fund_data.fetch_fund_managers(
        _optional_str(arguments, "code"), db_path=_db(arguments), provider=_provider(arguments)
    )


def _call_fund_sync(arguments: dict[str, Any]) -> dict[str, Any]:
    return fund_data.sync_fund(
        _required_str(arguments, "code"),
        start_date=_optional_str(arguments, "start_date"),
        end_date=_optional_str(arguments, "end_date"),
        page=_optional_int(arguments, "page", 1) or 1,
        per=_optional_int(arguments, "per", 50) or 50,
        db_path=_db(arguments),
        provider=_provider(arguments),
        include_holdings=_optional_bool(arguments, "include_holdings"),
        include_profile=_optional_bool(arguments, "include_profile"),
        include_bonds=_optional_bool(arguments, "include_bonds"),
        include_industries=_optional_bool(arguments, "include_industries"),
        include_fees=_optional_bool(arguments, "include_fees"),
        include_distributions=_optional_bool(arguments, "include_distributions"),
        include_managers=_optional_bool(arguments, "include_managers"),
        include_all=_optional_bool(arguments, "include_all"),
        report_year=_optional_str(arguments, "report_year"),
        fee_indicators=_optional_str_list(arguments, "fee_indicators"),
    )


def _call_fund_batch_sync(arguments: dict[str, Any]) -> dict[str, Any]:
    codes = _optional_str_list(arguments, "codes")
    if not codes:
        raise ValueError("missing required argument: codes")
    return fund_data.batch_sync_funds(
        codes,
        start_date=_optional_str(arguments, "start_date"),
        end_date=_optional_str(arguments, "end_date"),
        page=_optional_int(arguments, "page", 1) or 1,
        per=_optional_int(arguments, "per", 50) or 50,
        db_path=_db(arguments),
        provider=_provider(arguments),
        include_all=_optional_bool(arguments, "include_all"),
        report_year=_optional_str(arguments, "report_year"),
        fee_indicators=_optional_str_list(arguments, "fee_indicators"),
        batch_id=_optional_str(arguments, "batch_id"),
        stop_on_error=_optional_bool(arguments, "stop_on_error"),
        concurrency=_optional_int(arguments, "concurrency", 1) or 1,
        min_interval_seconds=_optional_float(arguments, "min_interval_seconds"),
    )


def _call_fund_coverage(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    return fund_data.coverage_rows(
        db_path=_db(arguments), fund_code=_optional_str(arguments, "fund_code")
    )


def _call_fund_coverage_report(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    return fund_data.coverage_report(
        db_path=_db(arguments),
        codes=_optional_str_list(arguments, "codes"),
        fund_type=_optional_str(arguments, "fund_type"),
        only_incomplete=_optional_bool(arguments, "only_incomplete"),
        min_completeness=_optional_float(arguments, "min_completeness", 0.0) or 0.0,
        limit=_optional_int(arguments, "limit"),
    )


def _call_fund_export(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    rows = fund_data.export_table(
        _required_str(arguments, "table"),
        db_path=_db(arguments),
        fund_code=_optional_str(arguments, "fund_code"),
    )
    return _limit(rows, arguments)


def _call_fund_cloud_status(arguments: dict[str, Any]) -> dict[str, Any]:
    return fund_cloud.status(
        cache_dir=_optional_str(arguments, "cache_dir"),
        manifest_url=_optional_str(arguments, "manifest_url"),
    )


TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "fund_search": _call_fund_search,
    "fund_list": _call_fund_list,
    "fund_nav_history": _call_fund_nav_history,
    "fund_snapshot": _call_fund_snapshot,
    "fund_profile": _call_fund_profile,
    "fund_stock_holdings": _call_fund_stock_holdings,
    "fund_bond_holdings": _call_fund_bond_holdings,
    "fund_industry_allocations": _call_fund_industry_allocations,
    "fund_fee_structures": _call_fund_fee_structures,
    "fund_dividends": _call_fund_dividends,
    "fund_splits": _call_fund_splits,
    "fund_managers": _call_fund_managers,
    "fund_sync": _call_fund_sync,
    "fund_batch_sync": _call_fund_batch_sync,
    "fund_coverage": _call_fund_coverage,
    "fund_coverage_report": _call_fund_coverage_report,
    "fund_export": _call_fund_export,
    "fund_cloud_status": _call_fund_cloud_status,
}


def handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _json_error(request_id, JSONRPC_INVALID_PARAMS, "params must be an object")

    # Notifications have no response.
    if request_id is None:
        return None

    if method == "initialize":
        requested = str(params.get("protocolVersion") or DEFAULT_PROTOCOL_VERSION)
        protocol_version = (
            requested if requested in SUPPORTED_PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION
        )
        return _json_response(
            request_id,
            {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "Use fund-data tools for Chinese public fund search, persistence, "
                    "coverage checks, and repeatable local data ingestion. Always report "
                    "source and fetched_at fields when quoting market data."
                ),
            },
        )

    if method == "ping":
        return _json_response(request_id, {})

    if method == "tools/list":
        return _json_response(request_id, {"tools": TOOLS})

    if method == "tools/call":
        tool_name = params.get("name")
        if not isinstance(tool_name, str):
            return _json_error(request_id, JSONRPC_INVALID_PARAMS, "params.name must be a string")
        handler = TOOL_HANDLERS.get(tool_name)
        if handler is None:
            return _json_error(request_id, JSONRPC_METHOD_NOT_FOUND, f"unknown tool: {tool_name}")
        try:
            arguments = _args(params)
            if tool_name != "fund_cloud_status":
                _maybe_bootstrap_cloud(arguments)
            payload = handler(arguments)
        except (TypeError, ValueError) as exc:
            return _json_error(request_id, JSONRPC_INVALID_PARAMS, str(exc))
        except Exception as exc:  # noqa: BLE001 - tool errors should reach clients as tool results
            return _json_response(request_id, _tool_result({"error": str(exc)}, is_error=True))
        return _json_response(request_id, _tool_result(payload))

    return _json_error(request_id, JSONRPC_METHOD_NOT_FOUND, f"method not found: {method}")


def _write_message(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            _write_message(_json_error(None, JSONRPC_PARSE_ERROR, "parse error", str(exc)))
            continue
        if not isinstance(message, dict):
            _write_message(_json_error(None, JSONRPC_INVALID_REQUEST, "message must be an object"))
            continue
        response = handle_message(message)
        if response is not None:
            _write_message(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
