from __future__ import annotations

import csv
import html
import json
import logging
import os
import re
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger("fund_data")

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "fund_data.sqlite"
PROVIDER_AUTO = "auto"
PROVIDER_EASTMONEY = "eastmoney"
PROVIDER_AKSHARE = "akshare"
PROVIDER_INVESTODAY = "investoday"
PROVIDER_TUSHARE = "tushare"


def default_db_path() -> Path:
    """Resolve the on-disk path to use when no ``db_path=`` is passed.

    Precedence (intentionally narrow):
      1. An explicitly-set ``FUND_DATA_CACHE_DIR`` — the user has
         wired up the cloud bundle and wants the bundled query DB
         to win. This is checked first because
         ``FUND_DATA_DB`` is also commonly set in agent / CI
         environments (the mavis OpenCode workspace sets it to
         a per-pid tmp path) and the user's intent for the
         cloud bundle should not be silently overridden.
      2. ``FUND_DATA_DB`` env var — local override (typically
         test or one-off dev runs).
      3. ``fund_cloud.current_db_path()`` — the installed
         query DB, picked up automatically when the bundle
         has a current.json.
      4. ``DEFAULT_DB_PATH`` — the on-disk fallback
         (``fund-data/data/fund_data.sqlite``).
    """
    cache_dir = os.environ.get("FUND_DATA_CACHE_DIR")
    if cache_dir:
        try:
            from . import fund_cloud
        except ImportError:  # pragma: no cover - direct script execution
            import fund_cloud  # type: ignore
        cloud_db = fund_cloud.current_db_path()
        if cloud_db:
            return cloud_db
    configured = os.environ.get("FUND_DATA_DB")
    if configured:
        return Path(configured)
    try:
        from . import fund_cloud
    except ImportError:  # pragma: no cover - direct script execution
        import fund_cloud  # type: ignore
    cloud_db = fund_cloud.current_db_path()
    return cloud_db or DEFAULT_DB_PATH


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


# --- schema migrations -----------------------------------------------------
#
# Each entry is ``(version, callable)``. ``ensure_schema`` reads
# ``PRAGMA user_version`` and applies every migration whose version
# is greater than the current user_version, in ascending order. After
# each successful migration it records the version in
# ``schema_migrations`` and bumps the pragma.
#
# Adding a new migration:
#   1. Append ``(N, _migration_NNN_short_description)`` to MIGRATIONS.
#   2. Add a regression test in test_fund_data.py that runs
#      ``ensure_schema`` against a v(N-1) DB and asserts the new
#      shape.
#   3. Bump FUND_DATA_SCHEMA_VERSION in any consumer code that caches
#      the version (currently nothing does).
#
# Never renumber or remove an existing migration — old DBs depend on
# each version being applied exactly once, in order.


def _migration_001_add_industry_allocations_market_value(conn: sqlite3.Connection) -> None:
    """v1: add ``industry_allocations.market_value`` for AkShare's
    industry weighting breakdown."""
    columns = {row["name"] for row in conn.execute("pragma table_info(industry_allocations)")}
    if "market_value" not in columns:
        conn.execute("alter table industry_allocations add column market_value real")


def _migration_002_add_fee_structures_fee_text(conn: sqlite3.Connection) -> None:
    """v2: add ``fee_structures.fee_text`` so the AkShare page scraper
    can persist its human-readable fee strings alongside the decimal
    value."""
    columns = {row["name"] for row in conn.execute("pragma table_info(fee_structures)")}
    if "fee_text" not in columns:
        conn.execute("alter table fee_structures add column fee_text text")


def _migration_003_add_fee_structures_discount_fee(conn: sqlite3.Connection) -> None:
    """v3: add ``fee_structures.discount_fee`` to carry the discounted
    fee (e.g. promo rate) alongside the list price."""
    columns = {row["name"] for row in conn.execute("pragma table_info(fee_structures)")}
    if "discount_fee" not in columns:
        conn.execute("alter table fee_structures add column discount_fee real")


def _migration_004_add_fee_structures_discount_fee_text(conn: sqlite3.Connection) -> None:
    """v4: add ``fee_structures.discount_fee_text`` to carry the
    human-readable discounted fee string."""
    columns = {row["name"] for row in conn.execute("pragma table_info(fee_structures)")}
    if "discount_fee_text" not in columns:
        conn.execute("alter table fee_structures add column discount_fee_text text")


MIGRATIONS: list[tuple[int, Any]] = [
    (1, _migration_001_add_industry_allocations_market_value),
    (2, _migration_002_add_fee_structures_fee_text),
    (3, _migration_003_add_fee_structures_discount_fee),
    (4, _migration_004_add_fee_structures_discount_fee_text),
]

FUND_DATA_SCHEMA_VERSION = max(version for version, _fn in MIGRATIONS)


def normalize_fund_code(value: str) -> str:
    match = re.search(r"\d{6}", str(value))
    if not match:
        raise ValueError(f"fund code must contain 6 digits: {value!r}")
    return match.group(0)


def _to_float(value: Any, *, percent: bool = False) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in {"", "-", "--", "---", "暂无数据", "暂未披露", "nan", "NaN"}:
        return None
    text = text.replace("%", "")
    try:
        number = float(text)
    except ValueError:
        return None
    return number / 100 if percent else number


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        return [dict(row) for row in value]
    if hasattr(value, "to_dict"):
        return [dict(row) for row in value.to_dict("records")]
    raise TypeError(f"unsupported tabular value: {type(value)!r}")


def _extract_payload_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "result", "rows", "items", "list", "Datas"):
        value = payload.get(key)
        if isinstance(value, list):
            return [dict(item) for item in value]
        if isinstance(value, dict):
            nested = _extract_payload_records(value)
            if nested:
                return nested
    return []


def _first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if not _is_missing(value):
            return value
    return None


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        if value != value:
            return True
    except Exception:
        pass
    return str(value).strip() in {"", "-", "--", "---", "暂无数据", "暂未披露", "nan", "NaN"}


def _clean_text(value: Any) -> str:
    if _is_missing(value):
        return ""
    return re.sub(r"\s+", " ", str(value).strip())


def _first_number(value: Any) -> float | None:
    if value is None:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else None


def _rate_to_decimal(value: Any) -> float | None:
    text = _clean_text(value)
    if not text or "%" not in text:
        return None
    number = _first_number(text)
    return number / 100 if number is not None else None


def _ratio_value(value: Any) -> float | None:
    text = _clean_text(value)
    if not text:
        return None
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*:\s*(-?\d+(?:\.\d+)?)", text)
    if match:
        denominator = float(match.group(1))
        numerator = float(match.group(2))
        return numerator / denominator if denominator else None
    return _first_number(text)


def _normalize_date_text(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    iso_match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if iso_match:
        year, month, day = iso_match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    chinese_match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if chinese_match:
        year, month, day = chinese_match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    return text


def _fee_indicator_alias(value: Any) -> str:
    text = _clean_text(value)
    aliases = {
        "认购费率（前端）": "认购费率",
        "认购费率（后端）": "认购费率",
        "申购费率（前端）": "申购费率",
        "申购费率（后端）": "申购费率",
        "赎回费率（前端）": "赎回费率",
        "赎回费率（后端）": "赎回费率",
    }
    return aliases.get(text, text)


def _profile_dict(records: list[dict[str, Any]]) -> dict[str, str]:
    profile: dict[str, str] = {}
    if len(records) == 1:
        row = records[0]
        if not any(key in row for key in ("item", "项目", "名称", "key", "label")):
            return {str(key).strip(): _clean_text(value) for key, value in row.items()}
    for row in records:
        key = _first_value(row, "item", "项目", "名称", "key", "label")
        value = _first_value(row, "value", "内容", "数值", "值")
        if key is not None and value is not None:
            profile[str(key).strip()] = _clean_text(value)
    return profile


def _decode_js_fragment(value: str) -> str:
    if "\\u" in value or "\\x" in value:
        try:
            return value.encode("utf-8").decode("unicode_escape")
        except UnicodeDecodeError:
            pass
    return (
        value.replace(r"\/", "/")
        .replace(r"\"", '"')
        .replace(r"\'", "'")
        .replace(r"\n", "\n")
        .replace(r"\r", "\r")
        .replace(r"\t", "\t")
    )


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            value = html.unescape("".join(self._cell)).strip()
            self._row.append(re.sub(r"\s+", " ", value))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def parse_search_results(raw_text: str) -> list[dict[str, Any]]:
    text = raw_text.lstrip("\ufeff").strip()
    if text.startswith("var r"):
        return parse_fund_code_list(text)

    payload = json.loads(text)
    if int(payload.get("ErrCode", 0)) != 0:
        raise ValueError(f"Eastmoney search error: {payload.get('ErrMsg', '')}")

    rows: list[dict[str, Any]] = []
    for item in payload.get("Datas") or []:
        base = item.get("FundBaseInfo") or {}
        code_value = base.get("FCODE") or item.get("CODE") or item.get("_id")
        if not code_value:
            continue
        rows.append(
            {
                "fund_code": normalize_fund_code(code_value),
                "fund_name": base.get("SHORTNAME") or item.get("NAME") or "",
                "fund_type": base.get("FTYPE") or "",
                "company": base.get("JJGS") or "",
                "manager": base.get("JJJL") or "",
                "nav": _to_float(base.get("DWJZ")),
                "nav_date": base.get("FSRQ") or "",
                "other_names": base.get("OTHERNAME") or "",
                "source": "eastmoney.search",
            }
        )
    return rows


def parse_fund_code_list(raw_text: str) -> list[dict[str, Any]]:
    text = raw_text.lstrip("\ufeff").strip()
    match = re.search(r"var\s+r\s*=\s*(\[.*\])\s*;?\s*$", text, re.S)
    if not match:
        raise ValueError("could not find Eastmoney fund code list array")
    payload = json.loads(match.group(1))
    rows = []
    for item in payload:
        if len(item) < 4:
            continue
        rows.append(
            {
                "fund_code": normalize_fund_code(item[0]),
                "fund_name": item[2],
                "fund_type": item[3],
                "company": "",
                "manager": "",
                "nav": None,
                "nav_date": "",
                "other_names": item[4] if len(item) > 4 else "",
                "source": "eastmoney.fundcode_search",
            }
        )
    return rows


def parse_fund_codes(raw_text: str) -> list[str]:
    seen: set[str] = set()
    codes: list[str] = []
    for line in raw_text.splitlines():
        line = line.split("#", 1)[0]
        for match in re.findall(r"\d{6}", line):
            code = normalize_fund_code(match)
            if code not in seen:
                seen.add(code)
                codes.append(code)
    return codes


def normalize_fund_codes(values: list[str] | tuple[str, ...]) -> list[str]:
    return parse_fund_codes("\n".join(str(value) for value in values))


def parse_nav_history(raw_text: str) -> list[dict[str, Any]]:
    match = re.search(r'content:"(?P<table>.*?)",\s*records:', raw_text, re.S)
    if not match:
        raise ValueError("could not find NAV table in Eastmoney response")
    table_html = _decode_js_fragment(match.group("table"))
    parser = _TableParser()
    parser.feed(table_html)

    rows: list[dict[str, Any]] = []
    for cells in parser.rows:
        if not cells or cells[0] == "净值日期" or len(cells) < 6:
            continue
        rows.append(
            {
                "nav_date": cells[0],
                "unit_nav": _to_float(cells[1]),
                "accumulated_nav": _to_float(cells[2]),
                "daily_growth_rate": _to_float(cells[3], percent=True),
                "subscribe_status": cells[4],
                "redeem_status": cells[5],
                "dividend": cells[6] if len(cells) > 6 else "",
                "source": "eastmoney.nav_history",
            }
        )
    return rows


def _extract_js_string(raw_text: str, name: str) -> str:
    match = re.search(rf"var\s+{re.escape(name)}\s*=\s*\"(.*?)\"\s*;", raw_text, re.S)
    return html.unescape(_decode_js_fragment(match.group(1).strip())) if match else ""


def _extract_js_array(raw_text: str, name: str) -> list[str]:
    match = re.search(rf"var\s+{re.escape(name)}\s*=\s*(\[.*?\])\s*;", raw_text, re.S)
    if not match:
        return []
    try:
        return list(json.loads(match.group(1)))
    except json.JSONDecodeError:
        return []


def parse_snapshot(raw_text: str) -> dict[str, Any]:
    returns = {
        "one_year": _to_float(_extract_js_string(raw_text, "syl_1n"), percent=True),
        "six_month": _to_float(_extract_js_string(raw_text, "syl_6y"), percent=True),
        "three_month": _to_float(_extract_js_string(raw_text, "syl_3y"), percent=True),
        "one_month": _to_float(_extract_js_string(raw_text, "syl_1y"), percent=True),
    }
    return {
        "fund_code": normalize_fund_code(_extract_js_string(raw_text, "fS_code")),
        "fund_name": _extract_js_string(raw_text, "fS_name"),
        "source_rate": _to_float(_extract_js_string(raw_text, "fund_sourceRate")),
        "current_rate": _to_float(_extract_js_string(raw_text, "fund_Rate")),
        "min_purchase": _to_float(_extract_js_string(raw_text, "fund_minsg")),
        "stock_codes": _extract_js_array(raw_text, "stockCodesNew"),
        "returns": returns,
        "source": "eastmoney.snapshot",
    }


class ProviderError(RuntimeError):
    pass


@dataclass
class ProviderResult:
    provider: str
    rows: Any
    failures: list[dict[str, str]]


def run_provider_chain(
    providers: list[Any], method_name: str, *args: Any, allow_empty: bool = False, **kwargs: Any
) -> ProviderResult:
    failures: list[dict[str, str]] = []
    for provider in providers:
        try:
            method = getattr(provider, method_name)
            rows = method(*args, **kwargs)
            if rows is None or (rows == [] and not allow_empty):
                raise ProviderError("provider returned no rows")
            return ProviderResult(provider=provider.name, rows=rows, failures=failures)
        except Exception as exc:
            failures.append({"provider": provider.name, "error": str(exc)})
    failure_text = "; ".join(f"{item['provider']}: {item['error']}" for item in failures)
    raise ProviderError(f"all providers failed for {method_name}: {failure_text}")


@dataclass
class FundDataClient:
    min_interval_seconds: float = 1.0
    timeout_seconds: int = 20
    rate_limiter: _RateLimiter | None = None

    SEARCH_URL = "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx"
    FUND_CODE_URL = "https://fund.eastmoney.com/js/fundcode_search.js"
    NAV_URL = "https://fundf10.eastmoney.com/F10DataApi.aspx"
    SNAPSHOT_URL_TEMPLATE = "https://fund.eastmoney.com/pingzhongdata/{code}.js"

    def __post_init__(self) -> None:
        self._last_call = 0.0
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://fund.eastmoney.com/",
        }

    def get_text(self, url: str, params: dict[str, Any] | None = None) -> str:
        full_url = url if not params else f"{url}?{urlencode(params)}"
        request = Request(full_url, headers=self.headers)
        if self.rate_limiter is not None:
            with self.rate_limiter:
                response = urlopen(request, timeout=self.timeout_seconds)
                charset = response.headers.get_content_charset() or "utf-8"
                data = response.read()
        else:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self.min_interval_seconds:
                time.sleep(self.min_interval_seconds - elapsed)
            response = urlopen(request, timeout=self.timeout_seconds)
            charset = response.headers.get_content_charset() or "utf-8"
            data = response.read()
            self._last_call = time.monotonic()
        return data.decode(charset, errors="replace")

    def search(self, keyword: str) -> str:
        return self.get_text(self.SEARCH_URL, {"m": "1", "key": keyword})

    def fund_code_list(self) -> str:
        return self.get_text(self.FUND_CODE_URL)

    def nav_history(
        self,
        code: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        page: int = 1,
        per: int = 20,
    ) -> str:
        params = {
            "type": "lsjz",
            "code": normalize_fund_code(code),
            "page": page,
            "per": per,
        }
        if start_date:
            params["sdate"] = start_date
        if end_date:
            params["edate"] = end_date
        return self.get_text(self.NAV_URL, params)

    def snapshot(self, code: str) -> str:
        return self.get_text(self.SNAPSHOT_URL_TEMPLATE.format(code=normalize_fund_code(code)))


class _RateLimiter:
    def __init__(self, min_interval_seconds: float) -> None:
        self.min_interval_seconds = min_interval_seconds
        self._lock = threading.Lock()
        self._last_call = 0.0

    def __enter__(self) -> _RateLimiter:
        self._lock.acquire()
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._last_call = time.monotonic()
        self._lock.release()


class EastmoneyProvider:
    name = PROVIDER_EASTMONEY

    def __init__(self, client: FundDataClient | None = None) -> None:
        self.client = client or FundDataClient()

    def search_funds(self, keyword: str) -> list[dict[str, Any]]:
        return parse_search_results(self.client.search(keyword))

    def fund_list(self) -> list[dict[str, Any]]:
        return parse_fund_code_list(self.client.fund_code_list())

    def nav_history(
        self,
        code: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        page: int = 1,
        per: int = 20,
    ) -> list[dict[str, Any]]:
        return parse_nav_history(
            self.client.nav_history(
                code, start_date=start_date, end_date=end_date, page=page, per=per
            )
        )

    def snapshot(self, code: str) -> dict[str, Any]:
        return parse_snapshot(self.client.snapshot(code))

    def stock_holdings(self, code: str, *, report_year: str | None = None) -> list[dict[str, Any]]:
        raise ProviderError(
            "Eastmoney direct stock holdings are not implemented; use akshare or investoday"
        )


class AkshareProvider:
    name = PROVIDER_AKSHARE

    def __init__(self, ak_module: Any | None = None) -> None:
        if ak_module is None and os.environ.get("FUND_DATA_DISABLE_AKSHARE") == "1":
            raise ProviderError("akshare is disabled by FUND_DATA_DISABLE_AKSHARE=1")
        if ak_module is not None:
            self.ak = ak_module
            return
        try:
            import akshare as ak  # type: ignore
        except Exception as exc:
            raise ProviderError(
                "akshare is not installed; run `python3 -m pip install akshare`"
            ) from exc
        self.ak = ak

    def search_funds(self, keyword: str) -> list[dict[str, Any]]:
        keyword_text = str(keyword).lower()
        rows = self.fund_list()
        return [
            row
            for row in rows
            if keyword_text in row["fund_code"].lower()
            or keyword_text in row["fund_name"].lower()
            or keyword_text in row.get("other_names", "").lower()
        ]

    def fund_list(self) -> list[dict[str, Any]]:
        rows = []
        for item in _records(self.ak.fund_name_em()):
            code = item.get("基金代码") or item.get("基金代码 ") or item.get("代码")
            name = item.get("基金简称") or item.get("基金名称") or item.get("名称")
            if not code or not name:
                continue
            rows.append(
                {
                    "fund_code": normalize_fund_code(code),
                    "fund_name": str(name),
                    "fund_type": str(item.get("基金类型") or ""),
                    "company": "",
                    "manager": "",
                    "nav": None,
                    "nav_date": "",
                    "other_names": ",".join(
                        str(item.get(key) or "")
                        for key in ("拼音缩写", "拼音全称")
                        if item.get(key)
                    ),
                    "source": "akshare.fund_name_em",
                }
            )
        return rows

    def nav_history(
        self,
        code: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        page: int = 1,
        per: int = 20,
    ) -> list[dict[str, Any]]:
        del page, per
        raw_rows = _records(
            self.ak.fund_open_fund_info_em(
                symbol=normalize_fund_code(code), indicator="单位净值走势"
            )
        )
        rows: list[dict[str, Any]] = []
        for item in raw_rows:
            nav_date = str(item.get("净值日期") or item.get("日期") or "")
            if start_date and nav_date < start_date:
                continue
            if end_date and nav_date > end_date:
                continue
            rows.append(
                {
                    "nav_date": nav_date,
                    "unit_nav": _to_float(item.get("单位净值")),
                    "accumulated_nav": _to_float(item.get("累计净值")),
                    "daily_growth_rate": _to_float(item.get("日增长率"), percent=True),
                    "subscribe_status": "",
                    "redeem_status": "",
                    "dividend": "",
                    "source": "akshare.fund_open_fund_info_em",
                }
            )
        rows.sort(key=lambda row: row["nav_date"], reverse=True)
        return rows

    def stock_holdings(self, code: str, *, report_year: str | None = None) -> list[dict[str, Any]]:
        year = report_year or str(datetime.now().year - 1)
        rows = []
        for item in _records(
            self.ak.fund_portfolio_hold_em(symbol=normalize_fund_code(code), date=year)
        ):
            stock_code = item.get("股票代码") or item.get("代码")
            stock_name = item.get("股票名称") or item.get("名称")
            if not stock_code or not stock_name:
                continue
            rows.append(
                {
                    "report_period": str(item.get("季度") or year),
                    "stock_code": str(stock_code).zfill(6),
                    "stock_name": str(stock_name),
                    "net_value_ratio": _to_float(item.get("占净值比例"), percent=True),
                    "shares": _to_float(item.get("持股数")),
                    "market_value": _to_float(item.get("持仓市值")),
                    "source": "akshare.fund_portfolio_hold_em",
                }
            )
        return rows

    def profile(self, code: str) -> dict[str, Any]:
        profile = _profile_dict(
            _records(self.ak.fund_overview_em(symbol=normalize_fund_code(code)))
        )
        established = profile.get("成立日期/规模", "")
        established_date = _normalize_date_text(established.split("/")[0]) if established else ""
        asset_size_text = profile.get("净资产规模") or profile.get("资产规模", "")
        return {
            "fund_code": normalize_fund_code(code),
            "fund_name": profile.get("基金简称", ""),
            "full_name": profile.get("基金全称", ""),
            "fund_type": profile.get("基金类型", ""),
            "issue_date": _normalize_date_text(profile.get("发行日期", "")),
            "establishment_date": established_date,
            "asset_size": _first_number(asset_size_text),
            "asset_size_date": _normalize_date_text(asset_size_text),
            "fund_company": profile.get("基金管理人", ""),
            "custodian": profile.get("基金托管人", ""),
            "manager": profile.get("基金经理人", ""),
            "benchmark": profile.get("业绩比较基准", ""),
            "tracking_target": profile.get("跟踪标的", ""),
            "source": "akshare.fund_overview_em",
        }

    def bond_holdings(self, code: str, *, report_year: str | None = None) -> list[dict[str, Any]]:
        year = report_year or str(datetime.now().year - 1)
        try:
            raw_df = self.ak.fund_portfolio_bond_hold_em(
                symbol=normalize_fund_code(code), date=year
            )
        except Exception:
            return []
        rows = []
        for item in _records(raw_df):
            bond_code = item.get("债券代码") or item.get("代码")
            bond_name = item.get("债券名称") or item.get("名称")
            if not bond_code or not bond_name:
                continue
            net_value_ratio = _to_float(
                _first_value(
                    item,
                    "占净值比例",
                    "占基金资产净值比例",
                    "占净值比",
                    "持仓占净值比",
                    "ratio",
                ),
                percent=True,
            )
            market_value = _to_float(
                _first_value(
                    item,
                    "持仓市值",
                    "市值",
                    "债券市值",
                    "market_value",
                )
            )
            rows.append(
                {
                    "report_period": str(item.get("季度") or year),
                    "bond_code": str(bond_code),
                    "bond_name": str(bond_name),
                    "net_value_ratio": net_value_ratio,
                    "market_value": market_value,
                    "source": "akshare.fund_portfolio_bond_hold_em",
                }
            )
        return rows

    def industry_allocations(
        self, code: str, *, report_year: str | None = None
    ) -> list[dict[str, Any]]:
        year = report_year or str(datetime.now().year - 1)
        rows = []
        for item in _records(
            self.ak.fund_portfolio_industry_allocation_em(
                symbol=normalize_fund_code(code), date=year
            )
        ):
            industry = _first_value(
                item,
                "行业类别",
                "行业名称",
                "行业",
                "行业分布",
                "类别",
            )
            if not industry:
                continue
            net_value_ratio = _to_float(
                _first_value(
                    item,
                    "占净值比例",
                    "占基金资产净值比例",
                    "占净值比",
                    "持仓占净值比",
                    "市值占净值比例",
                ),
                percent=True,
            )
            market_value = _to_float(
                _first_value(
                    item,
                    "市值",
                    "持仓市值",
                    "市场价值",
                    "market_value",
                )
            )
            rows.append(
                {
                    "report_period": str(
                        _first_value(item, "季度", "截止时间", "报告期", "report_period") or year
                    ),
                    "industry_name": str(industry),
                    "net_value_ratio": net_value_ratio,
                    "market_value": market_value,
                    "source": "akshare.fund_portfolio_industry_allocation_em",
                }
            )
        if not rows:
            bond_type_keywords = {"债券", "货币", "纯债", "短债", "中短债", "企债", "信用债"}
            try:
                profile_rows = _records(self.ak.fund_overview_em(symbol=normalize_fund_code(code)))
                profile = _profile_dict(profile_rows)
                fund_type = str(profile.get("基金类型", "")).lower()
                if any(keyword in fund_type for keyword in bond_type_keywords):
                    rows.append(
                        {
                            "report_period": year,
                            "industry_name": "债券/货币基金-无行业配置",
                            "net_value_ratio": None,
                            "market_value": None,
                            "source": "akshare.fund_portfolio_industry_allocation_em:bond_fund_fallback",
                        }
                    )
            except Exception:
                pass
        return rows

    def fee_structures(
        self, code: str, *, indicators: list[str] | None = None
    ) -> list[dict[str, Any]]:
        indicator_list = [
            _fee_indicator_alias(item)
            for item in (
                indicators
                or [
                    "交易状态",
                    "申购与赎回金额",
                    "交易确认日",
                    "运作费用",
                    "认购费率",
                    "申购费率",
                    "赎回费率",
                ]
            )
        ]
        rows = []
        page_rows = self._fee_structures_from_eastmoney_page(code, indicator_list)
        for indicator in indicator_list:
            try:
                raw_rows = _records(
                    self.ak.fund_fee_em(symbol=normalize_fund_code(code), indicator=indicator)
                )
            except Exception:
                raw_rows = []
            rows.extend(self._normalize_fee_records(indicator, raw_rows, "akshare.fund_fee_em"))
        if page_rows:
            existing_keys = {
                (row["fee_type"], row["condition_name"], row.get("fee_text", "")) for row in rows
            }
            for row in page_rows:
                key = (row["fee_type"], row["condition_name"], row.get("fee_text", ""))
                if key not in existing_keys:
                    rows.append(row)
                    existing_keys.add(key)
        if not rows:
            etf_fallback = self._fee_etf_fallback(code)
            if etf_fallback:
                rows.extend(etf_fallback)
        return rows

    def _fee_etf_fallback(self, code: str) -> list[dict[str, Any]]:
        normalized = normalize_fund_code(code)
        rows: list[dict[str, Any]] = []
        try:
            etf_info = _records(self.ak.fund_etf_fund_info_em(symbol=normalized))
            if etf_info:
                first_row = etf_info[0] if isinstance(etf_info, list) else etf_info
                management_fee = _first_value(
                    first_row,
                    "管理费率",
                    "管理费",
                    "mgmt_fee",
                    "management_fee",
                )
                custody_fee = _first_value(
                    first_row,
                    "托管费率",
                    "托管费",
                    "custody_fee",
                )
                if management_fee or custody_fee:
                    if management_fee:
                        rows.append(
                            {
                                "fee_type": "运作费用",
                                "condition_name": "管理费率",
                                "fee": _rate_to_decimal(management_fee),
                                "fee_text": _clean_text(management_fee),
                                "discount_fee": None,
                                "discount_fee_text": "",
                                "source": "akshare.fund_etf_fund_info_em:management_fee",
                            }
                        )
                    if custody_fee:
                        rows.append(
                            {
                                "fee_type": "运作费用",
                                "condition_name": "托管费率",
                                "fee": _rate_to_decimal(custody_fee),
                                "fee_text": _clean_text(custody_fee),
                                "discount_fee": None,
                                "discount_fee_text": "",
                                "source": "akshare.fund_etf_fund_info_em:custody_fee",
                            }
                        )
        except Exception:
            pass
        if not rows:
            try:
                open_fund_info = _records(
                    self.ak.fund_open_fund_info_em(symbol=normalized, indicator="基本信息")
                )
                for item in open_fund_info:
                    management_fee = _first_value(
                        item,
                        "管理费率",
                        "管理费",
                        "mgmt_fee",
                    )
                    custody_fee = _first_value(
                        item,
                        "托管费率",
                        "托管费",
                        "custody_fee",
                    )
                    if management_fee:
                        rows.append(
                            {
                                "fee_type": "运作费用",
                                "condition_name": "管理费率",
                                "fee": _rate_to_decimal(management_fee),
                                "fee_text": _clean_text(management_fee),
                                "discount_fee": None,
                                "discount_fee_text": "",
                                "source": "akshare.fund_open_fund_info_em:management_fee",
                            }
                        )
                    if custody_fee:
                        rows.append(
                            {
                                "fee_type": "运作费用",
                                "condition_name": "托管费率",
                                "fee": _rate_to_decimal(custody_fee),
                                "fee_text": _clean_text(custody_fee),
                                "discount_fee": None,
                                "discount_fee_text": "",
                                "source": "akshare.fund_open_fund_info_em:custody_fee",
                            }
                        )
                    if rows:
                        break
            except Exception:
                pass
        if not rows:
            rows.append(
                {
                    "fee_type": "运作费用",
                    "condition_name": "场内ETF-无费率信息",
                    "fee": None,
                    "fee_text": "",
                    "discount_fee": None,
                    "discount_fee_text": "",
                    "source": "akshare.fee_fallback:etf_no_data",
                }
            )
        return rows

    def _normalize_fee_records(
        self, indicator: str, records: list[dict[str, Any]], source: str
    ) -> list[dict[str, Any]]:
        rows = []
        for item in records:
            fee_text = _clean_text(
                _first_value(
                    item,
                    "费用",
                    "费率",
                    "赎回费率",
                    "原费率",
                    "天天基金优惠费率",
                    "天天基金优惠费率-银行卡购买",
                )
            )
            condition = _clean_text(
                _first_value(item, "条件或名称", "适用金额", "适用期限", "项目", "名称")
            )
            if not condition:
                values = [_clean_text(value) for value in item.values()]
                if len(values) >= 2:
                    condition = values[0]
                    fee_text = fee_text or values[1]
            if not condition and not fee_text:
                continue
            rows.append(
                {
                    "fee_type": _clean_text(_first_value(item, "费用类型") or indicator),
                    "condition_name": condition,
                    "fee": _rate_to_decimal(fee_text),
                    "fee_text": fee_text,
                    "discount_fee": _rate_to_decimal(
                        _first_value(
                            item,
                            "优惠费率",
                            "天天基金优惠费率",
                            "天天基金优惠费率-银行卡购买",
                            "天天基金优惠费率-活期宝购买",
                        )
                    ),
                    "discount_fee_text": _clean_text(
                        _first_value(
                            item,
                            "优惠费率",
                            "天天基金优惠费率",
                            "天天基金优惠费率-银行卡购买",
                            "天天基金优惠费率-活期宝购买",
                        )
                    ),
                    "source": source,
                }
            )
        return rows

    def _fee_structures_from_eastmoney_page(
        self, code: str, indicators: list[str]
    ) -> list[dict[str, Any]]:
        try:
            from io import StringIO

            import pandas as pd  # type: ignore
            import requests  # type: ignore
            from bs4 import BeautifulSoup  # type: ignore
        except Exception:
            return []

        url = f"https://fundf10.eastmoney.com/jjfl_{normalize_fund_code(code)}.html"
        try:
            response = requests.get(url, timeout=20)
            response.raise_for_status()
        except Exception:
            return []

        wanted = {_fee_indicator_alias(indicator) for indicator in indicators}
        soup = BeautifulSoup(response.text, features="html.parser")
        rows: list[dict[str, Any]] = []
        for title_elem in soup.find_all(name="h4", class_="t"):
            title = _fee_indicator_alias(title_elem.get_text(strip=True))
            if wanted and title not in wanted:
                continue
            table_elems = (
                title_elem.find_all_next("table")[:2]
                if title == "申购与赎回金额"
                else [title_elem.find_next("table")]
            )
            for table_elem in table_elems:
                if table_elem is None:
                    continue
                try:
                    records = pd.read_html(StringIO(str(table_elem)))[0].to_dict("records")
                except Exception:
                    continue
                if records and all(isinstance(key, int) for key in records[0]):
                    rows.extend(self._fee_key_value_rows(title, records))
                else:
                    rows.extend(self._fee_rate_rows(title, records))
        return rows

    def _fee_key_value_rows(
        self, title: str, records: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        rows = []
        for record in records:
            values = [_clean_text(value) for value in record.values()]
            for index in range(0, len(values) - 1, 2):
                condition = values[index]
                fee_text = values[index + 1]
                if not condition or not fee_text:
                    continue
                rows.append(
                    {
                        "fee_type": title,
                        "condition_name": condition,
                        "fee": _rate_to_decimal(fee_text),
                        "fee_text": fee_text,
                        "discount_fee": None,
                        "discount_fee_text": "",
                        "source": "eastmoney.fund_fee_page",
                    }
                )
        return rows

    def _fee_rate_rows(self, title: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = []
        for record in records:
            condition = _clean_text(_first_value(record, "适用金额", "适用期限", "条件或名称"))
            if not condition and record:
                condition = _clean_text(next(iter(record.values())))
            fee_text = _clean_text(_first_value(record, "费率", "赎回费率", "原费率", "费用"))
            discount_text = _clean_text(
                _first_value(
                    record,
                    "优惠费率",
                    "天天基金优惠费率",
                    "天天基金优惠费率-银行卡购买",
                    "天天基金优惠费率-活期宝购买",
                )
            )
            combined = _clean_text(_first_value(record, "原费率|天天基金优惠费率"))
            if combined and not fee_text:
                parts = [part.strip() for part in combined.split("|", 1)]
                fee_text = parts[0]
                discount_text = parts[1] if len(parts) > 1 else ""
            if not condition or not (fee_text or discount_text):
                continue
            rows.append(
                {
                    "fee_type": title,
                    "condition_name": condition,
                    "fee": _rate_to_decimal(fee_text),
                    "fee_text": fee_text,
                    "discount_fee": _rate_to_decimal(discount_text),
                    "discount_fee_text": discount_text,
                    "source": "eastmoney.fund_fee_page",
                }
            )
        return rows

    def dividends(self, code: str) -> list[dict[str, Any]]:
        code = normalize_fund_code(code)
        try:
            dividend_records = _records(
                self.ak.fund_open_fund_info_em(symbol=code, indicator="分红送配详情")
            )
        except Exception:
            dividend_records = [{}]
        rows = self._dividend_rows_from_records(
            code,
            dividend_records,
            "akshare.fund_open_fund_info_em:分红送配详情",
        )
        if rows or not dividend_records:
            return rows
        return self._dividend_rows_from_records(
            code,
            _records(self.ak.fund_fh_em()),
            "akshare.fund_fh_em",
            require_code_match=True,
        )

    def _dividend_rows_from_records(
        self,
        code: str,
        records: list[dict[str, Any]],
        source: str,
        *,
        require_code_match: bool = False,
    ) -> list[dict[str, Any]]:
        rows = []
        for item in records:
            item_code = _first_value(item, "基金代码", "代码", "fund_code")
            if require_code_match and (not item_code or normalize_fund_code(item_code) != code):
                continue
            dividend_date = _first_value(item, "权益登记日", "登记日", "dividend_date")
            if not dividend_date:
                continue
            rows.append(
                {
                    "dividend_date": _normalize_date_text(dividend_date),
                    "ex_dividend_date": _normalize_date_text(
                        _first_value(item, "除息日期", "除息日", "ex_dividend_date")
                    ),
                    "dividend_per_share": _first_number(
                        _first_value(item, "分红", "每份分红", "dividend")
                    ),
                    "payment_date": _normalize_date_text(
                        _first_value(item, "分红发放日", "发放日", "payment_date")
                    ),
                    "source": source,
                }
            )
        return rows

    def splits(self, code: str) -> list[dict[str, Any]]:
        code = normalize_fund_code(code)
        try:
            split_records = _records(
                self.ak.fund_open_fund_info_em(symbol=code, indicator="拆分详情")
            )
        except Exception:
            split_records = [{}]
        rows = self._split_rows_from_records(
            code,
            split_records,
            "akshare.fund_open_fund_info_em:拆分详情",
        )
        if rows or not split_records:
            return rows
        return self._split_rows_from_records(
            code,
            _records(self.ak.fund_cf_em()),
            "akshare.fund_cf_em",
            require_code_match=True,
        )

    def _split_rows_from_records(
        self,
        code: str,
        records: list[dict[str, Any]],
        source: str,
        *,
        require_code_match: bool = False,
    ) -> list[dict[str, Any]]:
        rows = []
        for item in records:
            item_code = _first_value(item, "基金代码", "代码", "fund_code")
            if require_code_match and (not item_code or normalize_fund_code(item_code) != code):
                continue
            split_date = _first_value(item, "拆分折算日", "拆分日", "split_date")
            if not split_date:
                continue
            rows.append(
                {
                    "split_date": _normalize_date_text(split_date),
                    "split_type": _clean_text(_first_value(item, "拆分类型", "类型", "split_type")),
                    "split_ratio": _ratio_value(
                        _first_value(item, "拆分折算比例", "拆分比例", "拆分折算", "split_ratio")
                    ),
                    "source": source,
                }
            )
        return rows

    def fund_managers(self, code: str | None = None) -> list[dict[str, Any]]:
        normalized_code = normalize_fund_code(code) if code else None
        rows = []
        for item in _records(self.ak.fund_manager_em()):
            current_codes = str(_first_value(item, "现任基金代码", "current_fund_codes") or "")
            if normalized_code and normalized_code not in current_codes:
                continue
            rows.append(
                {
                    "manager_name": str(_first_value(item, "姓名", "manager_name") or ""),
                    "company": str(_first_value(item, "所属公司", "company") or ""),
                    "current_fund_codes": current_codes,
                    "current_funds": str(_first_value(item, "现任基金", "current_funds") or ""),
                    "tenure_days": int(
                        _to_float(_first_value(item, "累计从业时间", "tenure_days")) or 0
                    ),
                    "current_aum": _to_float(
                        _first_value(item, "现任基金资产总规模", "current_aum")
                    ),
                    "best_return": _to_float(
                        _first_value(item, "现任基金最佳回报", "best_return"), percent=True
                    ),
                    "source": "akshare.fund_manager_em",
                }
            )
        return rows


class InvestodayProvider:
    name = PROVIDER_INVESTODAY

    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        # Accept either env var. ``INVESTODAY_API_KEY`` is the canonical
        # name we expose in PROVIDERS.md / SKILL.md; ``INVESTDATA_API_KEY``
        # is the legacy / Investoday-console-exported name and is kept
        # as a fallback for older setups.
        self.api_key = (
            api_key or os.environ.get("INVESTODAY_API_KEY") or os.environ.get("INVESTDATA_API_KEY")
        )
        if not self.api_key:
            raise ProviderError("INVESTODAY_API_KEY (or INVESTDATA_API_KEY) is not set")
        self.base_url = (
            base_url
            or os.environ.get("FINANCIAL_DATA_BASE_URL")
            or "https://data-api.investoday.net/data"
        ).rstrip("/")
        # Cache the /fund/all catalog for the lifetime of this provider
        # instance so repeated calls (search, profile per code, list) do
        # not re-hit the network. The catalog is ~27k records / ~10 MB;
        # the 1-hour TTL is a safety net for long-lived backfills.
        self._catalog_cache: list[dict[str, Any]] | None = None
        self._catalog_cache_ts: float = 0.0
        self._catalog_cache_ttl: float = 3600.0

    def _get_json(self, path: str, params: dict[str, Any]) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        request = Request(url, headers={"apiKey": self.api_key, "User-Agent": "fund-data-skill"})
        with urlopen(request, timeout=30) as response:
            data = response.read().decode("utf-8", errors="replace")
        return json.loads(data)

    @staticmethod
    def _normalize_fund_record(item: dict[str, Any]) -> dict[str, Any] | None:
        code = _first_value(item, "fundCode", "fund_code", "code", "FCODE", "基金代码")
        name = _first_value(
            item, "fundName", "fund_name", "name", "SHORTNAME", "基金名称", "基金简称"
        )
        if not code or not name:
            return None
        return {
            "fund_code": normalize_fund_code(code),
            "fund_name": str(name),
            "fund_type": str(_first_value(item, "fundType", "fund_type", "type", "基金类型") or ""),
            "company": str(
                _first_value(item, "company", "fundCompany", "managerCompany", "基金公司") or ""
            ),
            "manager": str(_first_value(item, "manager", "fundManager", "基金经理") or ""),
            "nav": _to_float(_first_value(item, "nav", "unitNav", "DWJZ", "单位净值")),
            "nav_date": str(_first_value(item, "navDate", "date", "FSRQ", "净值日期") or ""),
            "other_names": str(_first_value(item, "otherNames", "alias", "aliases") or ""),
            "source": "investoday.fund_all",
            # Pass-through profile fields so ``profile()`` can build
            # a full row without a second API call.
            "_raw": item,
        }

    def _fetch_catalog(self) -> list[dict[str, Any]]:
        """Auto-paginate ``/fund/all`` until the full universe is in hand.

        The Investoday API caps ``pageSize`` at 500. The total universe
        is ~27k funds, so we walk ~55 pages and stop early when the
        page is short.
        """
        rows: list[dict[str, Any]] = []
        page = 1
        page_size = 500
        while True:
            payload = self._get_json("/fund/all", {"pageNum": page, "pageSize": page_size})
            records = _extract_payload_records(payload)
            if not records:
                break
            for item in records:
                row = self._normalize_fund_record(item)
                if row is not None:
                    rows.append(row)
            total = int(payload.get("totalCount") or 0)
            if total and page * page_size >= total:
                break
            if len(records) < page_size:
                break
            page += 1
            if page > 200:  # safety stop; 200 * 500 = 100k, well over 27k
                break
        return rows

    def _get_catalog(self) -> list[dict[str, Any]]:
        now = time.time()
        if self._catalog_cache is None or (now - self._catalog_cache_ts) > self._catalog_cache_ttl:
            self._catalog_cache = self._fetch_catalog()
            self._catalog_cache_ts = now
        return self._catalog_cache

    def fund_list(self) -> list[dict[str, Any]]:
        """Return every fund in the Investoday catalog (auto-paginated).

        Strips the internal ``_raw`` payload before returning so callers
        do not accidentally re-serialize the upstream record.
        """
        return [{k: v for k, v in row.items() if k != "_raw"} for row in self._get_catalog()]

    def profile(self, code: str) -> dict[str, Any]:
        """Look up the profile for ``code`` from the cached catalog.

        Raises :class:`ProviderError` if the code is not in the
        Investoday universe, or if the catalog cannot be fetched.
        """
        target = normalize_fund_code(code)
        for row in self._get_catalog():
            if row["fund_code"] == target:
                raw = row.get("_raw") or {}
                # ``establishDate`` comes as ``"2010-08-20 00:00:00"``;
                # trim to ISO date for the ``establishment_date`` slot.
                raw_establish = raw.get("establishDate") or ""
                est_date = str(raw_establish)[:10] if raw_establish else ""
                return {
                    "fund_code": target,
                    "fund_name": str(raw.get("fundName") or row["fund_name"]),
                    "full_name": str(
                        raw.get("fundNameFull") or raw.get("fundName") or row["fund_name"]
                    ),
                    "fund_type": str(raw.get("fundType") or row.get("fund_type") or ""),
                    "issue_date": "",
                    "establishment_date": est_date,
                    "asset_size": None,
                    "asset_size_date": "",
                    "fund_company": str(
                        raw.get("managementCompanyName") or row.get("company") or ""
                    ),
                    "custodian": str(raw.get("custodianName") or ""),
                    "manager": "",
                    "benchmark": str(raw.get("benchmarkCode") or ""),
                    "tracking_target": "",
                    "is_qdii": bool(int(raw.get("isQdii") or 0)),
                    "is_fof": bool(int(raw.get("isFof") or 0)),
                    "investment_objective": str(raw.get("investmentObjective") or ""),
                    "investment_strategy": str(raw.get("investmentStrategy") or ""),
                    "risk_return_profile": str(raw.get("riskReturnProfile") or ""),
                    "source": "investoday.fund_all",
                }
        raise ProviderError(f"investoday: {code} not found in /fund/all catalog")

    def search_funds(self, keyword: str) -> list[dict[str, Any]]:
        keyword_text = str(keyword).lower()
        return [
            row
            for row in self.fund_list()
            if keyword_text in row["fund_code"].lower()
            or keyword_text in row["fund_name"].lower()
            or keyword_text in row.get("other_names", "").lower()
        ]

    def nav_history(
        self,
        code: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        page: int = 1,
        per: int = 20,
    ) -> list[dict[str, Any]]:
        records = _extract_payload_records(
            self._get_json(
                "/fund/nav/history",
                {
                    "fundCode": normalize_fund_code(code),
                    "code": normalize_fund_code(code),
                    "startDate": start_date or "",
                    "endDate": end_date or "",
                    "pageNum": page,
                    "pageSize": per,
                },
            )
        )
        rows = []
        for item in records:
            nav_date = str(_first_value(item, "navDate", "date", "tradeDate", "净值日期") or "")
            rows.append(
                {
                    "nav_date": nav_date,
                    "unit_nav": _to_float(_first_value(item, "unitNav", "nav", "单位净值")),
                    "accumulated_nav": _to_float(
                        _first_value(item, "accumulatedNav", "accNav", "累计净值")
                    ),
                    "daily_growth_rate": _to_float(
                        _first_value(item, "dailyGrowthRate", "dailyReturn", "日增长率"),
                        percent="%"
                        in str(
                            _first_value(item, "dailyGrowthRate", "dailyReturn", "日增长率") or ""
                        ),
                    ),
                    "subscribe_status": str(
                        _first_value(item, "subscribeStatus", "申购状态") or ""
                    ),
                    "redeem_status": str(_first_value(item, "redeemStatus", "赎回状态") or ""),
                    "dividend": str(_first_value(item, "dividend", "分红送配") or ""),
                    "source": "investoday.fund_nav_history",
                }
            )
        return rows

    def stock_holdings(self, code: str, *, report_year: str | None = None) -> list[dict[str, Any]]:
        records = _extract_payload_records(
            self._get_json(
                "/fund/portfolio-stock-holdings",
                {
                    "fundCode": normalize_fund_code(code),
                    "code": normalize_fund_code(code),
                    "reportYear": report_year or "",
                },
            )
        )
        rows = []
        for item in records:
            stock_code = _first_value(item, "stockCode", "stock_code", "股票代码")
            stock_name = _first_value(item, "stockName", "stock_name", "股票名称")
            if not stock_code or not stock_name:
                continue
            rows.append(
                {
                    "report_period": str(
                        _first_value(item, "reportPeriod", "quarter", "reportDate", "季度")
                        or report_year
                        or ""
                    ),
                    "stock_code": str(stock_code).zfill(6),
                    "stock_name": str(stock_name),
                    "net_value_ratio": _to_float(
                        _first_value(item, "netValueRatio", "holdingRatio", "占净值比例"),
                        percent="%"
                        in str(
                            _first_value(item, "netValueRatio", "holdingRatio", "占净值比例") or ""
                        ),
                    ),
                    "shares": _to_float(_first_value(item, "shares", "holdingShares", "持股数")),
                    "market_value": _to_float(
                        _first_value(item, "marketValue", "holdingMarketValue", "持仓市值")
                    ),
                    "source": "investoday.fund_portfolio_stock_holdings",
                }
            )
        return rows


class TushareProvider:
    """Tushare Pro adapter for Chinese public funds.

    Tushare's fund interface is the most standardized free+paid option
    for Chinese funds and the cleanest fallback when AkShare is being
    rate-limited. The free tier caps at ~200 calls/minute, so this
    provider is best used to cover the Eastmoney gap (profile,
    holdings, fees, dividends, splits, managers) rather than NAV
    history, which Eastmoney already serves well.

    Requires:
        pip install tushare
        export TUSHARE_TOKEN=...   # apply at https://tushare.pro
    """

    name = PROVIDER_TUSHARE

    def __init__(self, token: str | None = None, pro_module: Any | None = None) -> None:
        self.token = token or os.environ.get("TUSHARE_TOKEN")
        if pro_module is not None:
            self.pro = pro_module
            return
        if not self.token:
            raise ProviderError(
                "TUSHARE_TOKEN is not set; apply at https://tushare.pro and export it"
            )
        try:
            import tushare as ts

            ts.set_token(self.token)
            self.pro = ts.pro_api()
        except Exception as exc:
            raise ProviderError(f"tushare not available: {exc}") from exc

    @staticmethod
    def _to_ts_code(fund_code: str) -> str:
        """Convert our 6-digit fund_code to Tushare's ``<code>.OF`` format."""
        return f"{normalize_fund_code(fund_code)}.OF"

    def search_funds(self, keyword: str) -> list[dict[str, Any]]:
        df = self.pro.fund_basic(name=str(keyword)[:30])
        return self._normalize_fund_basic(df)

    def fund_list(self) -> list[dict[str, Any]]:
        df = self.pro.fund_basic()
        return self._normalize_fund_basic(df)

    def _normalize_fund_basic(self, df: Any) -> list[dict[str, Any]]:
        if df is None or (hasattr(df, "empty") and df.empty):
            return []
        rows = []
        items = list(df.iterrows()) if hasattr(df, "iterrows") else list(df)
        for item in items:
            if isinstance(item, tuple):
                item = item[1]
            code = str(item.get("ts_code", "")).split(".")[0]
            if not code:
                continue
            rows.append(
                {
                    "fund_code": normalize_fund_code(code),
                    "fund_name": str(item.get("name", "")),
                    "fund_type": str(item.get("fund_type", "")),
                    "company": str(item.get("management", "")),
                    "manager": str(item.get("custodian", "")) or "",
                    "nav": None,
                    "nav_date": "",
                    "other_names": "",
                    "source": "tushare.fund_basic",
                }
            )
        return rows

    def nav_history(
        self,
        code: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        page: int = 1,
        per: int = 200,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "ts_code": self._to_ts_code(code),
            "page_size": per,
            "page": page,
        }
        if start_date:
            params["start_date"] = start_date.replace("-", "")
        if end_date:
            params["end_date"] = end_date.replace("-", "")
        df = self.pro.fund_nav(**params)
        if df is None or (hasattr(df, "empty") and df.empty):
            return []
        rows = []
        items = list(df.iterrows()) if hasattr(df, "iterrows") else list(df)
        for item in items:
            if isinstance(item, tuple):
                item = item[1]
            rows.append(
                {
                    "nav_date": str(item.get("nav_date", "")).replace("T", " ").split(" ")[0],
                    "unit_nav": _to_float(item.get("unit_nav")),
                    "accumulated_nav": _to_float(item.get("accum_nav")),
                    "daily_growth_rate": _to_float(item.get("adj_nav")),
                    "subscribe_status": "",
                    "redeem_status": "",
                    "dividend": "",
                    "source": "tushare.fund_nav",
                }
            )
        return rows

    def profile(self, code: str) -> dict[str, Any]:
        df = self.pro.fund_basic(ts_code=self._to_ts_code(code))
        if df is None or (hasattr(df, "empty") and df.empty):
            raise ProviderError(f"tushare returned no profile for {code}")
        item = df.iloc[0].to_dict() if hasattr(df, "iloc") else (df[0] if df else {})
        return {
            "fund_code": normalize_fund_code(code),
            "fund_name": str(item.get("name", "")),
            "full_name": str(item.get("name", "")),
            "fund_type": str(item.get("fund_type", "")),
            "issue_date": "",
            "establishment_date": "",
            "asset_size": None,
            "asset_size_date": "",
            "fund_company": str(item.get("management", "")),
            "custodian": str(item.get("custodian", "")),
            "manager": "",
            "benchmark": "",
            "tracking_target": "",
            "source": "tushare.fund_basic",
        }

    def stock_holdings(self, code: str, *, report_year: str | None = None) -> list[dict[str, Any]]:
        period = _tushare_period(report_year)
        df = self.pro.fund_portfolio(ts_code=self._to_ts_code(code), period=period)
        if df is None or (hasattr(df, "empty") and df.empty):
            return []
        rows = []
        items = list(df.iterrows()) if hasattr(df, "iterrows") else list(df)
        for item in items:
            if isinstance(item, tuple):  # pandas (index, row) pair
                item = item[1]
            stock_code = str(item.get("stock_code", ""))
            if not stock_code or stock_code == "nan":
                continue
            ratio = _to_float(item.get("ratio"), percent=False)
            if ratio is not None and ratio > 1.0:
                ratio = ratio / 100.0
            rows.append(
                {
                    "report_period": str(item.get("end_date", period)),
                    "stock_code": str(stock_code).zfill(6),
                    "stock_name": str(item.get("stock_name", "")),
                    "net_value_ratio": ratio,
                    "shares": _to_float(item.get("amount")),
                    "market_value": _to_float(item.get("mkv")),
                    "source": "tushare.fund_portfolio",
                }
            )
        return rows

    def fund_managers(self, code: str | None = None) -> list[dict[str, Any]]:
        if code:
            df = self.pro.fund_manager(ts_code=self._to_ts_code(code))
        else:
            df = self.pro.fund_manager()
        if df is None or (hasattr(df, "empty") and df.empty):
            return []
        rows = []
        items = list(df.iterrows()) if hasattr(df, "iterrows") else list(df)
        for item in items:
            if isinstance(item, tuple):
                item = item[1]
            manager_name = str(item.get("name", ""))
            current_codes = str(item.get("ts_code", "")).split(".")[0]
            if not manager_name:
                continue
            rows.append(
                {
                    "manager_name": manager_name,
                    "company": str(item.get("gender", "")),
                    "current_fund_codes": current_codes,
                    "current_funds": str(item.get("fund_name", "")),
                    "tenure_days": 0,
                    "current_aum": None,
                    "best_return": _to_float(item.get("return_rate")),
                    "source": "tushare.fund_manager",
                }
            )
        return rows


def _tushare_period(report_year: str | None) -> str:
    """Translate a calendar year (e.g. ``"2024"``) to Tushare's quarterly
    period format (latest available quarter of that year)."""
    if not report_year:
        return "20241231"
    year = str(report_year)
    return f"{year}1231"


def build_providers(provider: str, *, capability: str | None = None) -> list[Any]:
    """Build the provider chain for a capability."""
    providers, init_warnings = build_providers_full(provider, capability=capability)
    for message in init_warnings:
        logger.warning(message)
    return providers


def build_providers_full(
    provider: str, *, capability: str | None = None
) -> tuple[list[Any], list[str]]:
    """Build the provider chain and return the init warnings as well.

    Returns a tuple of (providers, init_warnings). ``init_warnings`` is a
    list of human-readable strings, one per skipped provider, in the order
    they were tried. It is empty in non-auto mode or when every provider
    initialized successfully.
    """
    names: list[str]
    if provider == PROVIDER_AUTO:
        names = []
        if os.environ.get("INVESTDATA_API_KEY"):
            names.append(PROVIDER_INVESTODAY)
        if os.environ.get("TUSHARE_TOKEN"):
            names.append(PROVIDER_TUSHARE)
        if capability in {
            "stock_holdings",
            "profile",
            "bond_holdings",
            "industry_allocations",
            "fee_structures",
            "dividends",
            "splits",
            "fund_managers",
        }:
            # AkShare covers all of these; fall back to Eastmoney if missing.
            names.extend([PROVIDER_AKSHARE, PROVIDER_EASTMONEY])
        else:
            # NAV / snapshot / search / fund_list are well-served by Eastmoney.
            names.extend([PROVIDER_EASTMONEY, PROVIDER_AKSHARE])
    else:
        names = [provider]

    providers: list[Any] = []
    init_warnings: list[str] = []
    for name in names:
        try:
            if name == PROVIDER_EASTMONEY:
                providers.append(EastmoneyProvider())
            elif name == PROVIDER_AKSHARE:
                providers.append(AkshareProvider())
            elif name == PROVIDER_INVESTODAY:
                providers.append(InvestodayProvider())
            elif name == PROVIDER_TUSHARE:
                providers.append(TushareProvider())
            else:
                raise ProviderError(f"unknown provider: {name}")
        except ProviderError as exc:
            if provider != PROVIDER_AUTO:
                raise
            message = f"{name} unavailable for {capability or 'request'}: {exc}"
            init_warnings.append(message)
    if not providers:
        raise ProviderError(f"no providers available for {capability or 'request'}")
    return providers, init_warnings


class FundDataStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        default_path = default_db_path()
        self.db_path = Path(db_path) if db_path is not None else default_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.ensure_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript("""
                create table if not exists funds (
                    fund_code text primary key,
                    fund_name text not null,
                    fund_type text,
                    company text,
                    manager text,
                    nav real,
                    nav_date text,
                    other_names text,
                    source text,
                    updated_at text not null
                );
                create table if not exists nav_history (
                    fund_code text not null,
                    nav_date text not null,
                    unit_nav real,
                    accumulated_nav real,
                    daily_growth_rate real,
                    subscribe_status text,
                    redeem_status text,
                    dividend text,
                    source text,
                    fetched_at text not null,
                    primary key (fund_code, nav_date)
                );
                create table if not exists snapshots (
                    fund_code text primary key,
                    fund_name text,
                    source_rate real,
                    current_rate real,
                    min_purchase real,
                    returns_json text,
                    stock_codes_json text,
                    source text,
                    fetched_at text not null
                );
                create table if not exists raw_responses (
                    source text not null,
                    request_key text not null,
                    fetched_at text not null,
                    raw_text text not null,
                    primary key (source, request_key)
                );
                create table if not exists sync_runs (
                    id integer primary key autoincrement,
                    operation text not null,
                    fund_code text,
                    status text not null,
                    rows_changed integer not null,
                    started_at text not null,
                    finished_at text not null,
                    message text
                );
                create table if not exists sync_failures (
                    id integer primary key autoincrement,
                    batch_id text not null,
                    operation text not null,
                    fund_code text,
                    provider text,
                    message text not null,
                    failed_at text not null
                );
                create table if not exists stock_holdings (
                    fund_code text not null,
                    report_period text not null,
                    stock_code text not null,
                    stock_name text,
                    net_value_ratio real,
                    shares real,
                    market_value real,
                    source text,
                    fetched_at text not null,
                    primary key (fund_code, report_period, stock_code)
                );
                create table if not exists fund_profiles (
                    fund_code text primary key,
                    fund_name text,
                    full_name text,
                    fund_type text,
                    issue_date text,
                    establishment_date text,
                    asset_size real,
                    asset_size_date text,
                    fund_company text,
                    custodian text,
                    manager text,
                    benchmark text,
                    tracking_target text,
                    source text,
                    fetched_at text not null
                );
                create table if not exists bond_holdings (
                    fund_code text not null,
                    report_period text not null,
                    bond_code text not null,
                    bond_name text,
                    net_value_ratio real,
                    market_value real,
                    source text,
                    fetched_at text not null,
                    primary key (fund_code, report_period, bond_code)
                );
                create table if not exists industry_allocations (
                    fund_code text not null,
                    report_period text not null,
                    industry_name text not null,
                    net_value_ratio real,
                    market_value real,
                    source text,
                    fetched_at text not null,
                    primary key (fund_code, report_period, industry_name)
                );
                create table if not exists fee_structures (
                    fund_code text not null,
                    fee_type text not null,
                    condition_name text not null,
                    fee real,
                    fee_text text,
                    discount_fee real,
                    discount_fee_text text,
                    source text,
                    fetched_at text not null,
                    primary key (fund_code, fee_type, condition_name)
                );
                create table if not exists dividends (
                    fund_code text not null,
                    dividend_date text not null,
                    ex_dividend_date text,
                    dividend_per_share real,
                    payment_date text,
                    source text,
                    fetched_at text not null,
                    primary key (fund_code, dividend_date)
                );
                create table if not exists splits (
                    fund_code text not null,
                    split_date text not null,
                    split_type text,
                    split_ratio real,
                    source text,
                    fetched_at text not null,
                    primary key (fund_code, split_date)
                );
                create table if not exists fund_managers (
                    manager_name text not null,
                    company text,
                    current_fund_codes text,
                    current_funds text,
                    tenure_days integer,
                    current_aum real,
                    best_return real,
                    source text,
                    fetched_at text not null,
                    primary key (manager_name, company, current_fund_codes)
                );
                -- Schema migration registry. Bumped by apply_migrations()
                -- below. The version column here is the *audit log*;
                -- PRAGMA user_version is the *fast read* of the same
                -- value (read on every open).
                create table if not exists schema_migrations (
                    version integer primary key,
                    applied_at text not null
                );
                """)
            self._apply_migrations(conn)

    def _apply_migrations(self, conn: sqlite3.Connection) -> None:
        """Run every migration in :data:`MIGRATIONS` whose version is
        greater than the database's current ``PRAGMA user_version``.

        A failed migration aborts the whole ``ensure_schema`` call —
        the migration that errored has its transaction rolled back,
        so re-running ``ensure_schema`` retries the failed migration
        (the prior version remains as ``user_version``).
        """
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        for version, fn in MIGRATIONS:
            if version <= current:
                # Already applied (either this is a fresh DB and the
                # version is < the first migration, or an old DB
                # that's been upgraded before).
                continue
            try:
                fn(conn)
            except Exception:
                logger.exception("schema migration %d failed", version)
                raise
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (version, utc_now()),
            )
            conn.execute(f"PRAGMA user_version = {int(version)}")

    def _ensure_column(
        self, conn: sqlite3.Connection, table: str, column: str, column_type: str
    ) -> None:
        columns = {row["name"] for row in conn.execute(f"pragma table_info({table})")}
        if column not in columns:
            conn.execute(f"alter table {table} add column {column} {column_type}")

    def upsert_funds(self, rows: list[dict[str, Any]]) -> int:
        now = utc_now()
        with self.connect() as conn:
            conn.executemany(
                """
                insert into funds (
                    fund_code, fund_name, fund_type, company, manager, nav, nav_date,
                    other_names, source, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(fund_code) do update set
                    fund_name=excluded.fund_name,
                    fund_type=excluded.fund_type,
                    company=excluded.company,
                    manager=excluded.manager,
                    nav=excluded.nav,
                    nav_date=excluded.nav_date,
                    other_names=excluded.other_names,
                    source=excluded.source,
                    updated_at=excluded.updated_at
                """,
                [
                    (
                        row["fund_code"],
                        row.get("fund_name", ""),
                        row.get("fund_type", ""),
                        row.get("company", ""),
                        row.get("manager", ""),
                        row.get("nav"),
                        row.get("nav_date", ""),
                        row.get("other_names", ""),
                        row.get("source", ""),
                        now,
                    )
                    for row in rows
                ],
            )
        return len(rows)

    def upsert_nav_history(self, fund_code: str, rows: list[dict[str, Any]]) -> int:
        code = normalize_fund_code(fund_code)
        now = utc_now()
        with self.connect() as conn:
            conn.executemany(
                """
                insert into nav_history (
                    fund_code, nav_date, unit_nav, accumulated_nav, daily_growth_rate,
                    subscribe_status, redeem_status, dividend, source, fetched_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(fund_code, nav_date) do update set
                    unit_nav=excluded.unit_nav,
                    accumulated_nav=excluded.accumulated_nav,
                    daily_growth_rate=excluded.daily_growth_rate,
                    subscribe_status=excluded.subscribe_status,
                    redeem_status=excluded.redeem_status,
                    dividend=excluded.dividend,
                    source=excluded.source,
                    fetched_at=excluded.fetched_at
                """,
                [
                    (
                        code,
                        row["nav_date"],
                        row.get("unit_nav"),
                        row.get("accumulated_nav"),
                        row.get("daily_growth_rate"),
                        row.get("subscribe_status", ""),
                        row.get("redeem_status", ""),
                        row.get("dividend", ""),
                        row.get("source", "eastmoney.nav_history"),
                        now,
                    )
                    for row in rows
                ],
            )
        return len(rows)

    def upsert_snapshot(self, snapshot: dict[str, Any]) -> int:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                insert into snapshots (
                    fund_code, fund_name, source_rate, current_rate, min_purchase,
                    returns_json, stock_codes_json, source, fetched_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(fund_code) do update set
                    fund_name=excluded.fund_name,
                    source_rate=excluded.source_rate,
                    current_rate=excluded.current_rate,
                    min_purchase=excluded.min_purchase,
                    returns_json=excluded.returns_json,
                    stock_codes_json=excluded.stock_codes_json,
                    source=excluded.source,
                    fetched_at=excluded.fetched_at
                """,
                (
                    snapshot["fund_code"],
                    snapshot.get("fund_name", ""),
                    snapshot.get("source_rate"),
                    snapshot.get("current_rate"),
                    snapshot.get("min_purchase"),
                    _json_dumps(snapshot.get("returns", {})),
                    _json_dumps(snapshot.get("stock_codes", [])),
                    snapshot.get("source", "eastmoney.snapshot"),
                    now,
                ),
            )
        return 1

    def upsert_stock_holdings(self, fund_code: str, rows: list[dict[str, Any]]) -> int:
        code = normalize_fund_code(fund_code)
        now = utc_now()
        with self.connect() as conn:
            conn.executemany(
                """
                insert into stock_holdings (
                    fund_code, report_period, stock_code, stock_name, net_value_ratio,
                    shares, market_value, source, fetched_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(fund_code, report_period, stock_code) do update set
                    stock_name=excluded.stock_name,
                    net_value_ratio=excluded.net_value_ratio,
                    shares=excluded.shares,
                    market_value=excluded.market_value,
                    source=excluded.source,
                    fetched_at=excluded.fetched_at
                """,
                [
                    (
                        code,
                        row.get("report_period", ""),
                        str(row.get("stock_code", "")).zfill(6),
                        row.get("stock_name", ""),
                        row.get("net_value_ratio"),
                        row.get("shares"),
                        row.get("market_value"),
                        row.get("source", ""),
                        now,
                    )
                    for row in rows
                ],
            )
        return len(rows)

    def upsert_profile(self, profile: dict[str, Any]) -> int:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                insert into fund_profiles (
                    fund_code, fund_name, full_name, fund_type, issue_date,
                    establishment_date, asset_size, asset_size_date, fund_company,
                    custodian, manager, benchmark, tracking_target, source, fetched_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(fund_code) do update set
                    fund_name=excluded.fund_name,
                    full_name=excluded.full_name,
                    fund_type=excluded.fund_type,
                    issue_date=excluded.issue_date,
                    establishment_date=excluded.establishment_date,
                    asset_size=excluded.asset_size,
                    asset_size_date=excluded.asset_size_date,
                    fund_company=excluded.fund_company,
                    custodian=excluded.custodian,
                    manager=excluded.manager,
                    benchmark=excluded.benchmark,
                    tracking_target=excluded.tracking_target,
                    source=excluded.source,
                    fetched_at=excluded.fetched_at
                """,
                (
                    normalize_fund_code(profile["fund_code"]),
                    profile.get("fund_name", ""),
                    profile.get("full_name", ""),
                    profile.get("fund_type", ""),
                    profile.get("issue_date", ""),
                    profile.get("establishment_date", ""),
                    profile.get("asset_size"),
                    profile.get("asset_size_date", ""),
                    profile.get("fund_company", ""),
                    profile.get("custodian", ""),
                    profile.get("manager", ""),
                    profile.get("benchmark", ""),
                    profile.get("tracking_target", ""),
                    profile.get("source", ""),
                    now,
                ),
            )
        return 1

    def upsert_bond_holdings(self, fund_code: str, rows: list[dict[str, Any]]) -> int:
        code = normalize_fund_code(fund_code)
        now = utc_now()
        with self.connect() as conn:
            conn.executemany(
                """
                insert into bond_holdings (
                    fund_code, report_period, bond_code, bond_name, net_value_ratio,
                    market_value, source, fetched_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(fund_code, report_period, bond_code) do update set
                    bond_name=excluded.bond_name,
                    net_value_ratio=excluded.net_value_ratio,
                    market_value=excluded.market_value,
                    source=excluded.source,
                    fetched_at=excluded.fetched_at
                """,
                [
                    (
                        code,
                        row.get("report_period", ""),
                        str(row.get("bond_code", "")),
                        row.get("bond_name", ""),
                        row.get("net_value_ratio"),
                        row.get("market_value"),
                        row.get("source", ""),
                        now,
                    )
                    for row in rows
                ],
            )
        return len(rows)

    def upsert_industry_allocations(self, fund_code: str, rows: list[dict[str, Any]]) -> int:
        code = normalize_fund_code(fund_code)
        now = utc_now()
        with self.connect() as conn:
            conn.executemany(
                """
                insert into industry_allocations (
                    fund_code, report_period, industry_name, net_value_ratio,
                    market_value, source, fetched_at
                ) values (?, ?, ?, ?, ?, ?, ?)
                on conflict(fund_code, report_period, industry_name) do update set
                    net_value_ratio=excluded.net_value_ratio,
                    market_value=excluded.market_value,
                    source=excluded.source,
                    fetched_at=excluded.fetched_at
                """,
                [
                    (
                        code,
                        row.get("report_period", ""),
                        row.get("industry_name", ""),
                        row.get("net_value_ratio"),
                        row.get("market_value"),
                        row.get("source", ""),
                        now,
                    )
                    for row in rows
                ],
            )
        return len(rows)

    def upsert_fee_structures(self, fund_code: str, rows: list[dict[str, Any]]) -> int:
        code = normalize_fund_code(fund_code)
        now = utc_now()
        with self.connect() as conn:
            conn.executemany(
                """
                insert into fee_structures (
                    fund_code, fee_type, condition_name, fee, fee_text,
                    discount_fee, discount_fee_text, source, fetched_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(fund_code, fee_type, condition_name) do update set
                    fee=excluded.fee,
                    fee_text=excluded.fee_text,
                    discount_fee=excluded.discount_fee,
                    discount_fee_text=excluded.discount_fee_text,
                    source=excluded.source,
                    fetched_at=excluded.fetched_at
                """,
                [
                    (
                        code,
                        row.get("fee_type", ""),
                        row.get("condition_name", ""),
                        row.get("fee"),
                        row.get("fee_text", ""),
                        row.get("discount_fee"),
                        row.get("discount_fee_text", ""),
                        row.get("source", ""),
                        now,
                    )
                    for row in rows
                ],
            )
        return len(rows)

    def upsert_dividends(self, fund_code: str, rows: list[dict[str, Any]]) -> int:
        code = normalize_fund_code(fund_code)
        now = utc_now()
        with self.connect() as conn:
            conn.executemany(
                """
                insert into dividends (
                    fund_code, dividend_date, ex_dividend_date, dividend_per_share,
                    payment_date, source, fetched_at
                ) values (?, ?, ?, ?, ?, ?, ?)
                on conflict(fund_code, dividend_date) do update set
                    ex_dividend_date=excluded.ex_dividend_date,
                    dividend_per_share=excluded.dividend_per_share,
                    payment_date=excluded.payment_date,
                    source=excluded.source,
                    fetched_at=excluded.fetched_at
                """,
                [
                    (
                        code,
                        row.get("dividend_date", ""),
                        row.get("ex_dividend_date", ""),
                        row.get("dividend_per_share"),
                        row.get("payment_date", ""),
                        row.get("source", ""),
                        now,
                    )
                    for row in rows
                ],
            )
        return len(rows)

    def upsert_splits(self, fund_code: str, rows: list[dict[str, Any]]) -> int:
        code = normalize_fund_code(fund_code)
        now = utc_now()
        with self.connect() as conn:
            conn.executemany(
                """
                insert into splits (
                    fund_code, split_date, split_type, split_ratio, source, fetched_at
                ) values (?, ?, ?, ?, ?, ?)
                on conflict(fund_code, split_date) do update set
                    split_type=excluded.split_type,
                    split_ratio=excluded.split_ratio,
                    source=excluded.source,
                    fetched_at=excluded.fetched_at
                """,
                [
                    (
                        code,
                        row.get("split_date", ""),
                        row.get("split_type", ""),
                        row.get("split_ratio"),
                        row.get("source", ""),
                        now,
                    )
                    for row in rows
                ],
            )
        return len(rows)

    def upsert_fund_managers(self, rows: list[dict[str, Any]]) -> int:
        now = utc_now()
        with self.connect() as conn:
            conn.executemany(
                """
                insert into fund_managers (
                    manager_name, company, current_fund_codes, current_funds,
                    tenure_days, current_aum, best_return, source, fetched_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(manager_name, company, current_fund_codes) do update set
                    current_funds=excluded.current_funds,
                    tenure_days=excluded.tenure_days,
                    current_aum=excluded.current_aum,
                    best_return=excluded.best_return,
                    source=excluded.source,
                    fetched_at=excluded.fetched_at
                """,
                [
                    (
                        row.get("manager_name", ""),
                        row.get("company", ""),
                        row.get("current_fund_codes", ""),
                        row.get("current_funds", ""),
                        row.get("tenure_days"),
                        row.get("current_aum"),
                        row.get("best_return"),
                        row.get("source", ""),
                        now,
                    )
                    for row in rows
                ],
            )
        return len(rows)

    def record_raw_response(self, source: str, request_key: str, raw_text: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into raw_responses (source, request_key, fetched_at, raw_text)
                values (?, ?, ?, ?)
                on conflict(source, request_key) do update set
                    fetched_at=excluded.fetched_at,
                    raw_text=excluded.raw_text
                """,
                (source, request_key, utc_now(), raw_text),
            )

    def record_sync_run(
        self,
        *,
        operation: str,
        fund_code: str | None,
        status: str,
        rows_changed: int,
        started_at: str,
        message: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into sync_runs (
                    operation, fund_code, status, rows_changed, started_at, finished_at, message
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (operation, fund_code, status, rows_changed, started_at, utc_now(), message),
            )

    def record_sync_failure(
        self,
        *,
        batch_id: str,
        operation: str,
        fund_code: str | None,
        provider: str,
        message: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into sync_failures (
                    batch_id, operation, fund_code, provider, message, failed_at
                ) values (?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    operation,
                    normalize_fund_code(fund_code) if fund_code else None,
                    provider,
                    message,
                    utc_now(),
                ),
            )

    def export_table(self, table: str, fund_code: str | None = None) -> list[dict[str, Any]]:
        allowed = {
            "funds",
            "nav_history",
            "snapshots",
            "raw_responses",
            "sync_runs",
            "sync_failures",
            "stock_holdings",
            "fund_profiles",
            "bond_holdings",
            "industry_allocations",
            "fee_structures",
            "dividends",
            "splits",
            "fund_managers",
        }
        if table not in allowed:
            raise ValueError(f"unsupported table: {table}")
        sql = f"select * from {table}"
        params: tuple[str, ...] = ()
        if fund_code and table in {
            "funds",
            "nav_history",
            "snapshots",
            "sync_runs",
            "sync_failures",
            "stock_holdings",
            "fund_profiles",
            "bond_holdings",
            "industry_allocations",
            "fee_structures",
            "dividends",
            "splits",
        }:
            sql += " where fund_code = ?"
            params = (normalize_fund_code(fund_code),)
        sql += " order by 1"
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def coverage_rows(self, fund_code: str | None = None) -> list[dict[str, Any]]:
        where = ""
        params: tuple[str, ...] = ()
        if fund_code:
            where = "where f.fund_code = ?"
            params = (normalize_fund_code(fund_code),)
        sql = f"""
            select
                f.fund_code,
                f.fund_name,
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
        """
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]


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
        rows = parse_search_results(raw_text)
        source = "eastmoney.search"
        raw = raw_text
    elif client is not None:
        raw = client.search(keyword)
        rows = parse_search_results(raw)
        source = "eastmoney.search"
    else:
        result = run_provider_chain(
            build_providers(provider, capability="search"), "search_funds", keyword
        )
        rows = result.rows
        source = f"{result.provider}.search"
        raw = _json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures})
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
        rows = parse_search_results(raw_text)
        source = "eastmoney.fundcode_search"
        raw = raw_text
    else:
        result = run_provider_chain(build_providers(provider, capability="fund_list"), "fund_list")
        rows = result.rows
        source = f"{result.provider}.fund_list"
        raw = _json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures})
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
        rows = parse_nav_history(raw)
        source = "eastmoney.nav_history"
    elif client is not None:
        raw = client.nav_history(code, start_date=start_date, end_date=end_date, page=page, per=per)
        rows = parse_nav_history(raw)
        source = "eastmoney.nav_history"
    else:
        result = run_provider_chain(
            build_providers(provider, capability="nav_history"),
            "nav_history",
            code,
            start_date=start_date,
            end_date=end_date,
            page=page,
            per=per,
        )
        rows = result.rows
        source = f"{result.provider}.nav_history"
        raw = _json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures})
    if persist:
        request_key = (
            f"{normalize_fund_code(code)}:{start_date or ''}:{end_date or ''}:{page}:{per}"
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
) -> dict[str, Any]:
    if raw_text is not None:
        raw = raw_text
        snapshot = parse_snapshot(raw)
        source = "eastmoney.snapshot"
    elif client is not None:
        raw = client.snapshot(code)
        snapshot = parse_snapshot(raw)
        source = "eastmoney.snapshot"
    else:
        result = run_provider_chain(
            build_providers(provider, capability="snapshot"), "snapshot", code
        )
        snapshot = result.rows
        source = f"{result.provider}.snapshot"
        raw = _json_dumps(
            {"provider": result.provider, "snapshot": snapshot, "failures": result.failures}
        )
    if persist:
        store = FundDataStore(db_path)
        store.upsert_snapshot(snapshot)
        store.record_raw_response(source, normalize_fund_code(code), raw)
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
        build_providers(provider, capability="stock_holdings"),
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
            f"{normalize_fund_code(code)}:{report_year or ''}",
            _json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures}),
        )
    return rows


def fetch_profile(
    code: str,
    *,
    db_path: str | Path | None = None,
    persist: bool = True,
    provider: str = PROVIDER_AUTO,
) -> dict[str, Any]:
    result = run_provider_chain(build_providers(provider, capability="profile"), "profile", code)
    profile = result.rows
    if persist:
        store = FundDataStore(db_path)
        store.upsert_profile(profile)
        store.record_raw_response(
            f"{result.provider}.profile",
            normalize_fund_code(code),
            _json_dumps(
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
        build_providers(provider, capability="bond_holdings"),
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
            f"{normalize_fund_code(code)}:{report_year or ''}",
            _json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures}),
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
        build_providers(provider, capability="industry_allocations"),
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
            f"{normalize_fund_code(code)}:{report_year or ''}",
            _json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures}),
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
        build_providers(provider, capability="fee_structures"),
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
            normalize_fund_code(code),
            _json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures}),
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
        build_providers(provider, capability="dividends"), "dividends", code, allow_empty=True
    )
    rows = result.rows
    if persist:
        store = FundDataStore(db_path)
        store.upsert_dividends(code, rows)
        store.record_raw_response(
            f"{result.provider}.dividends",
            normalize_fund_code(code),
            _json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures}),
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
        build_providers(provider, capability="splits"), "splits", code, allow_empty=True
    )
    rows = result.rows
    if persist:
        store = FundDataStore(db_path)
        store.upsert_splits(code, rows)
        store.record_raw_response(
            f"{result.provider}.splits",
            normalize_fund_code(code),
            _json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures}),
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
        build_providers(provider, capability="fund_managers"), "fund_managers", code
    )
    rows = result.rows
    if persist:
        store = FundDataStore(db_path)
        store.upsert_fund_managers(rows)
        store.record_raw_response(
            f"{result.provider}.fund_managers",
            normalize_fund_code(code) if code else "all",
            _json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures}),
        )
    return rows


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
    code_list = normalize_fund_codes(codes) if codes else None
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
        "fund_code": normalize_fund_code(code),
        "fund_name": profile.get("fund_name")
        or snapshot.get("fund_name")
        or normalize_fund_code(code),
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
    code = normalize_fund_code(code)
    if include_all:
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

    try:
        snapshot = fetch_snapshot(
            code, db_path=db_path, client=client, persist=True, provider=provider
        )
        snapshot_count = 1
        rows_changed += snapshot_count

        profile_count = 0
        profile: dict[str, Any] | None = None
        if include_profile:
            try:
                profile = fetch_profile(code, db_path=db_path, persist=True, provider=provider)
                profile_count = 1
                rows_changed += profile_count
            except Exception as exc:
                record_dataset_error("profile", exc)

        fund_count = store.upsert_funds(
            [_fund_row_from_sync(code, snapshot=snapshot, profile=profile)]
        )
        rows_changed += fund_count

        nav_rows = fetch_nav_history(
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
                holding_rows = fetch_stock_holdings(
                    code, report_year=report_year, db_path=db_path, persist=True, provider=provider
                )
                holdings_count = len(holding_rows)
                rows_changed += holdings_count
            except Exception as exc:
                record_dataset_error("stock_holdings", exc)

        bond_count = 0
        if include_bonds:
            try:
                bond_rows = fetch_bond_holdings(
                    code, report_year=report_year, db_path=db_path, persist=True, provider=provider
                )
                bond_count = len(bond_rows)
                rows_changed += bond_count
            except Exception as exc:
                record_dataset_error("bond_holdings", exc)

        industry_count = 0
        if include_industries:
            try:
                industry_rows = fetch_industry_allocations(
                    code, report_year=report_year, db_path=db_path, persist=True, provider=provider
                )
                industry_count = len(industry_rows)
                rows_changed += industry_count
            except Exception as exc:
                record_dataset_error("industry_allocations", exc)

        fee_count = 0
        if include_fees:
            try:
                fee_rows = fetch_fee_structures(
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
                dividend_rows = fetch_dividends(
                    code, db_path=db_path, persist=True, provider=provider
                )
                dividend_count = len(dividend_rows)
                rows_changed += dividend_count
            except Exception as exc:
                record_dataset_error("dividends", exc)
            try:
                split_rows = fetch_splits(code, db_path=db_path, persist=True, provider=provider)
                split_count = len(split_rows)
                rows_changed += split_count
            except Exception as exc:
                record_dataset_error("splits", exc)

        manager_count = 0
        if include_managers:
            try:
                manager_rows = fetch_fund_managers(
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
    code_list = normalize_fund_codes(codes)
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

    sync_kwargs: dict[str, Any] = {
        "start_date": start_date,
        "end_date": end_date,
        "page": page,
        "per": per,
        "db_path": db_path,
        "provider": provider,
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
            result = sync_fund(code, **sync_kwargs)
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


def write_rows(rows: list[dict[str, Any]], output_path: str | Path | None, fmt: str) -> str:
    if fmt == "json":
        text = json.dumps(rows, ensure_ascii=False, indent=2)
        if output_path:
            Path(output_path).write_text(text + "\n", encoding="utf-8")
        return text
    if fmt != "csv":
        raise ValueError(f"unsupported format: {fmt}")
    if not rows:
        text = ""
    else:
        fieldnames = list(rows[0].keys())
        if output_path:
            with Path(output_path).open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            return str(output_path)
        from io import StringIO

        buffer = StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        text = buffer.getvalue()
    return text


def export_table(
    table: str,
    *,
    db_path: str | Path | None = None,
    fund_code: str | None = None,
) -> list[dict[str, Any]]:
    return FundDataStore(db_path).export_table(table, fund_code=fund_code)
