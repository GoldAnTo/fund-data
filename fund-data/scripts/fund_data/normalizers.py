"""Text / float / date / report-period normalizers.

Lifted from ``fund_data.py`` in the 0.3.0 split (RFC
``docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md``).
These helpers are pure functions over the raw values that
arrive from the four providers (Eastmoney, AkShare,
Investoday, Tushare). Every helper is intentionally
defensive: an unparseable input returns ``None`` / ``""`` /
the original value rather than raising, so a single bad
row does not abort a backfill run.

Dependency direction: ``normalizers`` depends on
``paths`` (for ``logger`` if it ever needs one) and the
standard library only. Parsers, store, fetch, sync
*consume* these helpers; none of them are imported by
this module.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Calendar day for the end of each Chinese reporting quarter.
# AkShare emits ``"YYYY年N季度股票投资明细"`` from
# ``fund_portfolio_hold_em`` and ``fund_portfolio_bond_hold_em``;
# the long-form Chinese label collapses to one of these four
# quarter-end ISO dates so a single JOIN / GROUP BY can cover
# both the equity and the bond disclosure tables.
_QUARTER_END_DAY = {1: 31, 2: 30, 3: 30, 4: 31}

# Pre-compiled so the helper is cheap to call from the per-row
# write path inside ``AkshareProvider.stock_holdings`` /
# ``bond_holdings``.  Anchored with ``^`` so a row that already
# happens to be ISO (``2024-12-31``) does not get re-mapped to a
# different quarter end.
_QUARTER_LABEL_RE = re.compile(r"^(\d{4})年([1-4])季度")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_YEAR_ONLY_RE = re.compile(r"^\d{4}$")

# Tokens that Eastmoney / AkShare / Tushare use to signal
# "no data" in an otherwise-numeric column. Matching is
# exact (case-insensitive via the lower-cased check below),
# so legitimate values like ``"--1.5"`` are not collapsed
# (the leading ``-`` would not be present alone, but a real
# negative would carry digits).
_MISSING_TOKENS = {"", "-", "--", "---", "暂无数据", "暂未披露", "nan", "nan"}


def normalize_fund_code(value: str) -> str:
    """Pull the first 6-digit run out of a value, raise on miss.

    The Eastmoney search response occasionally embeds the
    code inside HTML or a URL; ``fundcode_search.js`` mixes
    it with a pinyin column. We accept any 6-digit run
    rather than enforcing "value is exactly 6 digits" so
    the helper is forgiving when the source adds a
    non-numeric prefix (e.g. ``"_110022"``).
    """
    match = re.search(r"\d{6}", str(value))
    if not match:
        raise ValueError(f"fund code must contain 6 digits: {value!r}")
    return match.group(0)


def _to_float(value: Any, *, percent: bool = False) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text.lower() in _MISSING_TOKENS:
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
    """Best-effort unwrap of a JSON payload into ``list[dict]``.

    The four providers each ship a slightly different envelope
    (Eastmoney uses ``Datas``, AkShare uses ``data`` or
    ``result``, Tushare uses ``data.fields`` + ``data.items``).
    We try a list of common keys and a single level of
    recursion; anything more nested is left to the caller
    to interpret.
    """
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
    """Return the first value in ``row`` whose key matches and is not
    a missing-token.

    Providers use three to four naming conventions for the
    same field (``item`` / ``项目`` / ``名称`` / ``key`` /
    ``label``). Trying them in order keeps the call site
    provider-agnostic.
    """
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
    return str(value).strip().lower() in _MISSING_TOKENS


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
    """Parse ``"1.5%"`` (AkShare fee style) into ``0.015``.

    Returns ``None`` if the value has no percent sign, so a
    bare number is not silently re-interpreted. Use
    :func:`_to_float` with ``percent=True`` for that.
    """
    text = _clean_text(value)
    if not text or "%" not in text:
        return None
    number = _first_number(text)
    return number / 100 if number is not None else None


def _ratio_value(value: Any) -> float | None:
    """Parse ``"30:70"`` (3:7 ratio) into ``2.3333``.

    Falls back to :func:`_first_number` for plain numeric
    strings, so the same helper handles the three
    common shapes: ``"30:70"``, ``"0.875"``, ``"-1.5"``.
    """
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


def _normalize_report_period(value: Any) -> str:
    """Collapse a Chinese quarterly report label to its ISO quarter end.

    Handles the four shapes that show up in the local data base
    (and a fifth that the AkShare provider emits with a suffix
    like ``股票投资明细`` / ``债券投资明细``):

    ==============================  ==================
    input                          output
    ==============================  ==================
    ``"2024年4季度股票投资明细"``  ``"2024-12-31"``
    ``"2024年4季度债券投资明细"``  ``"2024-12-31"``
    ``"2024年4季度"``              ``"2024-12-31"``
    ``"2024-12-31"``               ``"2024-12-31"`` (idempotent)
    ``"2024"``                     ``"2024-12-31"``
    ==============================  ==================

    Empty / ``None`` returns ``""``.  An unrecognised format is
    returned as-is (defensive: do not break data we cannot
    interpret -- the migration script in
    :mod:`migrate_normalize_report_period` will surface the
    unknown values so the operator can decide).
    """
    text = _clean_text(value)
    if not text:
        return ""
    if _ISO_DATE_RE.match(text):
        return text
    m = _QUARTER_LABEL_RE.match(text)
    if m:
        year, quarter = int(m.group(1)), int(m.group(2))
        end_month = quarter * 3
        return f"{year}-{end_month:02d}-{_QUARTER_END_DAY[quarter]:02d}"
    if _YEAR_ONLY_RE.match(text):
        return f"{text}-12-31"
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
    """Flatten the two AkShare profile-table shapes into a
    single ``{label: value}`` mapping.

    The "single row" shape: a list of one dict whose keys are
    labels and values are values (no separate label column).
    The "labeled row" shape: a list of dicts, each with a
    label column (``item`` / ``项目`` / ``名称`` / ``key`` /
    ``label``) and a value column (``value`` / ``内容`` /
    ``数值`` / ``值``).

    Detection: the single-row shape is the special case
    where the row has no recognised label key. The
    labeled-row shape is the general case.
    """
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


__all__ = [
    "normalize_fund_code",
    "_to_float",
    "_json_dumps",
    "_records",
    "_extract_payload_records",
    "_first_value",
    "_is_missing",
    "_clean_text",
    "_first_number",
    "_rate_to_decimal",
    "_ratio_value",
    "_normalize_date_text",
    "_normalize_report_period",
    "_fee_indicator_alias",
    "_profile_dict",
]
