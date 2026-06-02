"""HTML / JSON / JS parsers for the four upstream providers.

Lifted from ``fund_data.py`` in the 0.3.0 split (RFC
``docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md``).
Pure functions over the raw response bodies that the four
HTTP clients return; the providers (EastmoneyProvider,
AkshareProvider, InvestodayProvider, TushareProvider) call
into here and then hand the resulting dicts to
``FundDataStore``.

Dependency direction: ``parsers`` depends on
``normalizers`` (for ``normalize_fund_code`` and
``_to_float``). Nothing in normalizers / paths imports
from this module.

``normalize_fund_codes`` is co-located here even though it
is a thin wrapper over :func:`parse_fund_codes`: callers
that need to "clean up a list of user-supplied codes" expect
both names to live next to each other, and putting
``normalize_fund_codes`` in normalizers.py would invert the
dependency direction (normalizers -> parsers).
"""

from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from typing import Any

from . import normalizers

from . import normalizers

__all__ = [
    "parse_search_results",
    "parse_fund_code_list",
    "parse_fund_codes",
    "normalize_fund_codes",
    "parse_nav_history",
    "parse_snapshot",
]


def _decode_js_fragment(value: str) -> str:
    """Decode a JavaScript string fragment emitted by Eastmoney.

    The Eastmoney page body uses ``\\uXXXX`` / ``\\xNN`` escapes
    inline (not inside the JSON payload) for the table content
    blobs; this helper turns them back into UTF-8 strings.
    Falls back to a small set of literal escape replacements
    when the body is not actually unicode-escaped (the
    Eastmoney NAV endpoint emits both shapes).
    """
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
    """Pull rows out of the Eastmoney NAV table HTML.

    The Eastmoney NAV endpoint emits a JavaScript string with
    HTML table rows like ``<tr><td>...</td><td>...</td>...</tr>``.
    A full HTMLParser is heavier than the job strictly needs,
    but using the stdlib version means the parse path is
    exercised against every Python release's HTMLParser
    quirks rather than a hand-rolled regex that would
    silently miss a malformed row in production.
    """

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
    """Parse an Eastmoney fund-search response.

    Two shapes land in this entry point: the older
    ``fundcode_search.js`` style (a ``var r = [...]`` array) and
    the newer search API style (a JSON envelope with
    ``ErrCode`` / ``Datas``). We detect the shape by the
    leading ``var r`` and dispatch to
    :func:`parse_fund_code_list` in that case.
    """
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
                "fund_code": normalizers.normalize_fund_code(code_value),
                "fund_name": base.get("SHORTNAME") or item.get("NAME") or "",
                "fund_type": base.get("FTYPE") or "",
                "company": base.get("JJGS") or "",
                "manager": base.get("JJJL") or "",
                "nav": normalizers._to_float(base.get("DWJZ")),
                "nav_date": base.get("FSRQ") or "",
                "other_names": base.get("OTHERNAME") or "",
                "source": "eastmoney.search",
            }
        )
    return rows


def parse_fund_code_list(raw_text: str) -> list[dict[str, Any]]:
    """Parse the ``fundcode_search.js`` universe-list response.

    Each row is a 5-tuple ``[code, pinyin, name, fund_type,
    other_names]``; the function returns the canonical
    ``{fund_code, fund_name, fund_type, ...}`` dict.
    """
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
                "fund_code": normalizers.normalize_fund_code(item[0]),
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
    """Pull every 6-digit code out of a text blob, in order,
    deduplicated.

    Used by the CLI ``--codes-file`` path and by
    :func:`normalize_fund_codes` (below). ``#`` is the
    comment marker: anything after it on a line is dropped
    so a watchlist file like::

        110022  # 易方达蓝筹
        000001  # 华夏成长

    parses to ``["110022", "000001"]`` with the comments
    ignored.
    """
    seen: set[str] = set()
    codes: list[str] = []
    for line in raw_text.splitlines():
        line = line.split("#", 1)[0]
        for match in re.findall(r"\d{6}", line):
            code = normalizers.normalize_fund_code(match)
            if code not in seen:
                seen.add(code)
                codes.append(code)
    return codes


def normalize_fund_codes(values: list[str] | tuple[str, ...]) -> list[str]:
    """Normalize + deduplicate a list of user-supplied codes.

    Convenience wrapper over :func:`parse_fund_codes` for the
    case where the caller already has the codes in memory
    (e.g. a list of args) rather than in a text blob.
    """
    return parse_fund_codes("\n".join(str(value) for value in values))


def parse_nav_history(raw_text: str) -> list[dict[str, Any]]:
    """Parse the Eastmoney NAV history response.

    The page body is a JavaScript string with a ``content:"..."``
    block holding the HTML table; we extract the table,
    HTML-parse it via :class:`_TableParser`, and turn each row
    into a canonical dict. The header row (``"净值日期"``) is
    skipped; rows with fewer than 6 cells are skipped
    defensively.
    """
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
                "unit_nav": normalizers._to_float(cells[1]),
                "accumulated_nav": normalizers._to_float(cells[2]),
                "daily_growth_rate": normalizers._to_float(cells[3], percent=True),
                "subscribe_status": cells[4],
                "redeem_status": cells[5],
                "dividend": cells[6] if len(cells) > 6 else "",
                "source": "eastmoney.nav_history",
            }
        )
    return rows


def _extract_js_string(raw_text: str, name: str) -> str:
    """Find a top-level ``var <name> = "...";`` declaration in a
    page body and return the unescaped value.

    Used by :func:`parse_snapshot` to pluck the per-fund
    scalars (``fS_code``, ``fS_name``, ``fund_sourceRate``,
    ``fund_Rate``, ``fund_minsg``, the four ``syl_XX``
    returns) out of the Eastmoney ``pingzhongdata`` page.
    """
    match = re.search(rf"var\s+{re.escape(name)}\s*=\s*\"(.*?)\"\s*;", raw_text, re.S)
    return html.unescape(_decode_js_fragment(match.group(1).strip())) if match else ""


def _extract_js_array(raw_text: str, name: str) -> list[str]:
    """Find a top-level ``var <name> = [...];`` declaration in a
    page body and return the parsed list.

    A JSONDecodeError inside the array literal is
    downgraded to ``[]`` so a partial array (e.g. a
    truncated snapshot page during a connectivity blip) does
    not abort the whole sync.
    """
    match = re.search(rf"var\s+{re.escape(name)}\s*=\s*(\[.*?\])\s*;", raw_text, re.S)
    if not match:
        return []
    try:
        return list(json.loads(match.group(1)))
    except json.JSONDecodeError:
        return []


def parse_snapshot(
    raw_text: str, *, default_code: str = ""
) -> dict[str, Any] | None:
    """Parse the Eastmoney ``pingzhongdata/{code}.js`` payload.

    Returns ``None`` when the page body is empty or the embedded
    ``fS_code`` token is missing, so callers can distinguish a
    legitimate "no standalone snapshot for this fund" (e.g. the
    back-end share classes ``000002``, ``000012``, ... that share
    their profile with the front-end class) from a parse error.
    ``default_code`` is used as the ``fund_code`` fallback when the
    page body is present but the embedded ``fS_code`` is blank,
    matching what the caller already passed in via
    :meth:`FundDataClient.snapshot`.

    The parser was previously happy to feed an empty ``fS_code``
    through :func:`normalize_fund_code`, which raises
    ``ValueError("fund code must contain 6 digits: ''")`` and lands
    the fund in ``sync_failures`` with a message that hides the
    real cause (back-end share). 241 funds (``000002``/``000012``/
    ``000108``/...) carry that spurious failure today.
    """
    if not raw_text or not raw_text.strip():
        return None
    returns = {
        "one_year": normalizers._to_float(_extract_js_string(raw_text, "syl_1n"), percent=True),
        "six_month": normalizers._to_float(_extract_js_string(raw_text, "syl_6y"), percent=True),
        "three_month": normalizers._to_float(_extract_js_string(raw_text, "syl_3y"), percent=True),
        "one_month": normalizers._to_float(_extract_js_string(raw_text, "syl_1y"), percent=True),
    }
    raw_fS_code = _extract_js_string(raw_text, "fS_code")
    fallback = raw_fS_code or default_code
    if not fallback:
        # Page body present but no usable code -- this is the genuine
        # parse-error path. Surface it instead of writing a half-row.
        raise ValueError("could not extract fS_code from snapshot page")
    return {
        "fund_code": normalizers.normalize_fund_code(fallback),
        "fund_name": _extract_js_string(raw_text, "fS_name"),
        "source_rate": normalizers._to_float(_extract_js_string(raw_text, "fund_sourceRate")),
        "current_rate": normalizers._to_float(_extract_js_string(raw_text, "fund_Rate")),
        "min_purchase": normalizers._to_float(_extract_js_string(raw_text, "fund_minsg")),
        "stock_codes": _extract_js_array(raw_text, "stockCodesNew"),
        "returns": returns,
        "source": "eastmoney.snapshot",
    }
