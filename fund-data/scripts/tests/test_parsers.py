"""Unit tests for ``scripts/fund_data/parsers.py``.

Lifted out of the package-level test bundle during the 0.3.0
split (RFC ``docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md``).
This file pins the four shape-detection branches (search
envelope vs fundcode_search.js vs NAV table vs snapshot
page) so a future Eastmoney API change does not silently
start writing malformed rows to SQLite.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from fund_data import parsers  # noqa: E402

# Sample Eastmoney ``fundcode_search.js`` page body. Three
# rows: a stock 混合型, a money-market 货币型, and a row with
# only 3 cells (malformed -- skipped).
FUND_CODE_LIST_RAW = (
    'var r = ['
    '["110022", "yifangdayanglan", "易方达蓝筹精选", "混合型-偏股", "蓝筹"],'
    '["000013", "yifangtiantianlicaib", "易方达天天理财货币R", "货币型", ""],'
    '["000003", "too-short"],'
    '["000001", "huaxiachengzhang", "华夏成长", "股票型", "成长"]'
    '];'
)

# Sample Eastmoney search response (newer API shape). One
# row in the Datas envelope, plus an empty ErrMsg so the
# ErrCode != 0 branch is not triggered.
SEARCH_RESULTS_RAW = (
    '{"ErrCode": 0, "ErrMsg": "", "Datas": ['
    '{'
    '"CODE": "110022",'
    '"NAME": "易方达蓝筹精选",'
    '"FundBaseInfo": {'
    '"FCODE": "110022",'
    '"SHORTNAME": "易方达蓝筹",'
    '"FTYPE": "混合型-偏股",'
    '"JJGS": "易方达基金",'
    '"JJJL": "张坤",'
    '"DWJZ": "1.234",'
    '"FSRQ": "2024-12-31",'
    '"OTHERNAME": "蓝筹精选"'
    '}'
    '},'
    '{'
    '"_id": "_000013",'
    '"NAME": "易方达天天理财货币R"'
    '}'
    ']}'
)

# Sample Eastmoney NAV page. The ``content:"..."`` block
# holds an *HTML table* (not a pipe-separated blob) — the
# regex in :func:`parse_nav_history` anchors on the
# ``content:"..."`` marker and the inner HTML is fed to
# ``_TableParser`` for the actual row extraction. This is
# the same shape as ``test_fund_data.NAV_PAYLOAD``.
NAV_HISTORY_RAW = (
    'var apidata={ content:"<table>'
    '<tr><th>净值日期</th><th>单位净值</th><th>累计净值</th>'
    '<th>日增长率</th><th>申购状态</th><th>赎回状态</th>'
    '<th>分红送配</th></tr>'
    '<tr><td>2024-12-31</td><td>1.234</td><td>1.500</td>'
    '<td>+0.5%</td><td>开放申购</td><td>开放赎回</td><td>0.0</td></tr>'
    '<tr><td>2024-12-30</td><td>1.228</td><td>1.492</td>'
    '<td>-0.3%</td><td>开放申购</td><td>开放赎回</td><td>0.0</td></tr>'
    '</table>",records:22,pages:5,curpage:1};'
)


class ParseFundCodeListTests(unittest.TestCase):
    def test_parses_canonical_shape(self) -> None:
        rows = parsers.parse_fund_code_list(FUND_CODE_LIST_RAW)
        self.assertEqual(len(rows), 3)  # the 3-cell row is dropped
        self.assertEqual(rows[0]["fund_code"], "110022")
        self.assertEqual(rows[0]["fund_name"], "易方达蓝筹精选")
        self.assertEqual(rows[0]["fund_type"], "混合型-偏股")
        self.assertEqual(rows[0]["other_names"], "蓝筹")
        self.assertEqual(rows[0]["source"], "eastmoney.fundcode_search")

    def test_currency_fund_row(self) -> None:
        rows = parsers.parse_fund_code_list(FUND_CODE_LIST_RAW)
        # The second valid row is 货币型, which should land
        # with an empty other_names (the source has "" not
        # "—" or some sentinel).
        self.assertEqual(rows[1]["fund_code"], "000013")
        self.assertEqual(rows[1]["fund_type"], "货币型")
        self.assertEqual(rows[1]["other_names"], "")

    def test_missing_var_r_raises(self) -> None:
        with self.assertRaises(ValueError) as cm:
            parsers.parse_fund_code_list("not a fundcode_search page")
        self.assertIn("fund code list array", str(cm.exception))

    def test_bom_prefix_stripped(self) -> None:
        # The Eastmoney page body sometimes starts with a
        # UTF-8 BOM. The parser strips it before regex match.
        rows = parsers.parse_fund_code_list("\ufeff" + FUND_CODE_LIST_RAW)
        self.assertEqual(len(rows), 3)


class ParseSearchResultsTests(unittest.TestCase):
    def test_parses_envelope_shape(self) -> None:
        rows = parsers.parse_search_results(SEARCH_RESULTS_RAW)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["fund_code"], "110022")
        self.assertEqual(rows[0]["fund_name"], "易方达蓝筹")
        self.assertEqual(rows[0]["fund_type"], "混合型-偏股")
        self.assertEqual(rows[0]["company"], "易方达基金")
        self.assertEqual(rows[0]["manager"], "张坤")
        self.assertEqual(rows[0]["nav"], 1.234)
        self.assertEqual(rows[0]["nav_date"], "2024-12-31")
        self.assertEqual(rows[0]["other_names"], "蓝筹精选")
        self.assertEqual(rows[0]["source"], "eastmoney.search")

    def test_dispatches_to_fundcode_list_for_var_r_shape(self) -> None:
        # A page body starting with ``var r =`` is the
        # fundcode_search.js universe-list shape, not the
        # search envelope. The parser dispatches to
        # :func:`parse_fund_code_list` in that case.
        rows = parsers.parse_search_results(FUND_CODE_LIST_RAW)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["fund_code"], "110022")
        # ``source`` flips to the fundcode_search variant.
        self.assertEqual(rows[0]["source"], "eastmoney.fundcode_search")

    def test_raises_on_errcode_nonzero(self) -> None:
        bad = '{"ErrCode": 999, "ErrMsg": "rate limited"}'
        with self.assertRaises(ValueError) as cm:
            parsers.parse_search_results(bad)
        self.assertIn("rate limited", str(cm.exception))

    def test_skips_row_without_code(self) -> None:
        # A search result that has no FCODE / CODE / _id
        # field is silently dropped. The single bad row
        # must not abort the parse.
        raw = (
            '{"ErrCode": 0, "Datas": ['
            '{"NAME": "no-code fund"},'  # no FCODE/CODE/_id
            '{"CODE": "000001", "NAME": "valid"}'
            ']}'
        )
        rows = parsers.parse_search_results(raw)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["fund_code"], "000001")


class ParseNavHistoryTests(unittest.TestCase):
    def test_parses_two_rows(self) -> None:
        rows = parsers.parse_nav_history(NAV_HISTORY_RAW)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["nav_date"], "2024-12-31")
        self.assertEqual(rows[0]["unit_nav"], 1.234)
        self.assertEqual(rows[0]["accumulated_nav"], 1.5)
        self.assertEqual(rows[0]["daily_growth_rate"], 0.005)
        self.assertEqual(rows[0]["subscribe_status"], "开放申购")
        self.assertEqual(rows[0]["redeem_status"], "开放赎回")
        self.assertEqual(rows[0]["dividend"], "0.0")
        self.assertEqual(rows[0]["source"], "eastmoney.nav_history")

    def test_raises_when_table_not_found(self) -> None:
        with self.assertRaises(ValueError) as cm:
            parsers.parse_nav_history("no content block here")
        self.assertIn("NAV table", str(cm.exception))


class ParseFundCodesTests(unittest.TestCase):
    def test_pulls_six_digit_codes(self) -> None:
        codes = parsers.parse_fund_codes("110022 000001\n110022 000003")
        self.assertEqual(codes, ["110022", "000001", "000003"])

    def test_dedupes_in_order(self) -> None:
        # The same code appearing twice is kept only once,
        # in the order it was first seen.
        codes = parsers.parse_fund_codes("000001 110022 000001 110022")
        self.assertEqual(codes, ["000001", "110022"])

    def test_strips_comment_marker(self) -> None:
        # ``#`` is the comment marker; everything after it
        # on a line is dropped. Pin so a watchlist file like
        # ``110022  # 易方达蓝筹`` parses to ["110022"].
        codes = parsers.parse_fund_codes(
            "110022  # 易方达蓝筹\n000001  # 华夏成长"
        )
        self.assertEqual(codes, ["110022", "000001"])

    def test_no_codes_returns_empty(self) -> None:
        self.assertEqual(parsers.parse_fund_codes("hello world"), [])

    def test_normalize_fund_codes_thin_wrapper(self) -> None:
        # The list-based version concatenates with newlines
        # and delegates to parse_fund_codes. The dedup
        # semantics are the same.
        codes = parsers.normalize_fund_codes(
            ["110022", "000001", "110022", "abc 000003"]
        )
        self.assertEqual(codes, ["110022", "000001", "000003"])


class ParseSnapshotTests(unittest.TestCase):
    def test_returns_none_on_empty_body(self) -> None:
        # Empty / whitespace-only bodies return None so the
        # provider chain can skip the row instead of
        # writing a half-row to SQLite. Back-end share
        # classes ``000002``/``000012``/``000108`` are the
        # main cause.
        self.assertIsNone(parsers.parse_snapshot(""))
        self.assertIsNone(parsers.parse_snapshot("   \n  "))

    def test_raises_on_present_but_unparseable(self) -> None:
        # A non-empty body with no fS_code and no
        # default_code is the genuine parse-error path.
        # Surface it; do not write a half-row.
        with self.assertRaises(ValueError) as cm:
            parsers.parse_snapshot(
                "var other_field = 'value';", default_code=""
            )
        self.assertIn("fS_code", str(cm.exception))

    def test_default_code_used_when_fS_code_blank(self) -> None:
        # When the body has the per-fund scalars but
        # fS_code is blank, the caller-supplied
        # default_code is the fund_code fallback. This is
        # the back-end share class path: the front-end
        # class's snapshot page has the data but the
        # embedded code is empty.
        body = (
            'var fS_name = "易方达蓝筹精选";'
            'var fund_sourceRate = "1.50";'
            'var syl_1n = "12.5%";'
        )
        snapshot = parsers.parse_snapshot(body, default_code="110022")
        self.assertIsNotNone(snapshot)
        assert snapshot is not None  # for the type checker
        self.assertEqual(snapshot["fund_code"], "110022")
        self.assertEqual(snapshot["fund_name"], "易方达蓝筹精选")
        # fund_sourceRate is in *decimal* form (NOT percent)
        # on the Eastmoney page body, so 1.50 stays 1.5.
        self.assertEqual(snapshot["source_rate"], 1.5)
        # syl_* are percent: "12.5%" -> 0.125.
        self.assertEqual(snapshot["returns"]["one_year"], 0.125)

    def test_parses_full_body(self) -> None:
        # The actual Eastmoney page body (per
        # test_fund_data.NAV_PAYLOAD) emits HTML tables
        # inside the ``content:"..."`` block. Pin the
        # full-body snapshot parse against the same shape.
        # fund_sourceRate / fund_Rate / fund_minsg are
        # already in decimal form (NOT percent). The four
        # ``syl_XX`` returns are percent and
        # ``parse_snapshot`` calls ``_to_float(percent=True)``
        # for them.
        body = (
            'var fS_code = "110022";'
            'var fS_name = "易方达蓝筹精选";'
            'var fund_sourceRate = "1.50";'
            'var fund_Rate = "0.15";'
            'var fund_minsg = "10";'
            'var syl_1n = "12.5%";'
            'var syl_6y = "5.0%";'
            'var syl_3y = "2.0%";'
            'var syl_1y = "0.5%";'
            'var stockCodesNew = ["000001", "000002"];'
        )
        snapshot = parsers.parse_snapshot(body)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot["fund_code"], "110022")
        self.assertEqual(snapshot["fund_name"], "易方达蓝筹精选")
        self.assertEqual(snapshot["source_rate"], 1.5)
        self.assertEqual(snapshot["current_rate"], 0.15)
        self.assertEqual(snapshot["min_purchase"], 10.0)
        self.assertEqual(snapshot["stock_codes"], ["000001", "000002"])
        self.assertEqual(snapshot["returns"]["one_year"], 0.125)
        self.assertEqual(snapshot["returns"]["six_month"], 0.05)
        self.assertEqual(snapshot["returns"]["three_month"], 0.02)
        self.assertEqual(snapshot["returns"]["one_month"], 0.005)
        self.assertEqual(snapshot["source"], "eastmoney.snapshot")


class DecodeJsFragmentTests(unittest.TestCase):
    def test_unicode_escape_decoded(self) -> None:
        # Eastmoney NAV page embeds table content with
        # \\uXXXX escapes inline; this helper turns them
        # back into UTF-8.
        self.assertEqual(
            parsers._decode_js_fragment(r"\u4e2d\u6587"),
            "中文",
        )

    def test_unicode_decode_error_falls_through(self) -> None:
        # Malformed escape sequences fall through to the
        # literal-replacement path so the parse does not
        # raise.
        result = parsers._decode_js_fragment(r"\uZZZZ plain text")
        self.assertIn("plain text", result)

    def test_literal_replacements(self) -> None:
        # When the body has no \u / \x escapes, the helper
        # still rewrites the common JS escape sequences
        # so the HTML parser sees clean text.
        self.assertEqual(
            parsers._decode_js_fragment(r"a\nb\tc"),
            "a\nb\tc",
        )


class ExtractJsStringTests(unittest.TestCase):
    def test_finds_top_level_var(self) -> None:
        body = 'var fS_name = "易方达蓝筹精选"; var other = 1;'
        self.assertEqual(
            parsers._extract_js_string(body, "fS_name"),
            "易方达蓝筹精选",
        )

    def test_returns_empty_when_not_found(self) -> None:
        body = 'var other_field = "value";'
        self.assertEqual(parsers._extract_js_string(body, "missing"), "")

    def test_unescapes_html_entities(self) -> None:
        # The NAV table body sometimes embeds HTML entities
        # inside the JS string; unescape them so the
        # resulting string is clean.
        body = 'var fS_name = "易方达 &amp; 蓝筹精选";'
        self.assertEqual(
            parsers._extract_js_string(body, "fS_name"),
            "易方达 & 蓝筹精选",
        )


class ExtractJsArrayTests(unittest.TestCase):
    def test_finds_top_level_var(self) -> None:
        body = 'var stockCodesNew = ["000001", "000002", "000003"];'
        self.assertEqual(
            parsers._extract_js_array(body, "stockCodesNew"),
            ["000001", "000002", "000003"],
        )

    def test_returns_empty_when_not_found(self) -> None:
        body = "var other = 1;"
        self.assertEqual(parsers._extract_js_array(body, "missing"), [])

    def test_returns_empty_on_json_decode_error(self) -> None:
        # A truncated array literal during a connectivity
        # blip is downgraded to [] instead of raising.
        body = 'var stockCodesNew = ["000001", "000002"'
        self.assertEqual(parsers._extract_js_array(body, "stockCodesNew"), [])


class TableParserTests(unittest.TestCase):
    def test_parses_simple_table(self) -> None:
        # The class is module-private, but a future parser
        # refactor is the most likely place to break it
        # (a new tag, an entity in cell text, etc.). Pin
        # the basic shape so a regression here is caught
        # at unit-test time rather than on a live NAV pull.
        parser = parsers._TableParser()
        parser.feed(
            "<table>"
            "<tr><th>A</th><th>B</th></tr>"
            "<tr><td>1</td><td>2</td></tr>"
            "<tr><td>3</td><td>4</td></tr>"
            "</table>"
        )
        self.assertEqual(
            parser.rows,
            [["A", "B"], ["1", "2"], ["3", "4"]],
        )

    def test_collapses_whitespace_in_cells(self) -> None:
        parser = parsers._TableParser()
        parser.feed("<table><tr><td>  hello  \n  world  </td></tr></table>")
        # Multiple whitespace characters inside a cell are
        # collapsed to a single space; surrounding whitespace
        # is stripped.
        self.assertEqual(parser.rows, [["hello world"]])

    def test_ignores_empty_rows(self) -> None:
        parser = parsers._TableParser()
        parser.feed("<table><tr></tr><tr><td>x</td></tr><tr></tr></table>")
        # Only the row with one non-empty cell survives.
        self.assertEqual(parser.rows, [["x"]])


if __name__ == "__main__":
    unittest.main()
