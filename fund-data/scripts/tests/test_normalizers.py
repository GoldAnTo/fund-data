"""Unit tests for ``scripts/fund_data/normalizers.py``.

Lifted out of the package-level test bundle during the 0.3.0
split (RFC ``docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md``).
The helpers here are pure functions over raw provider values;
this file pins the *defensive* behaviour (return None /
``""`` / original value on unparseable input) so a future
refactor cannot silently start raising ValueError inside a
backfill run on a single bad row.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from fund_data import normalizers  # noqa: E402


class NormalizeFundCodeTests(unittest.TestCase):
    def test_pulls_first_six_digit_run(self) -> None:
        # The Eastmoney search response occasionally embeds
        # the code inside HTML or a URL; the helper accepts
        # any 6-digit run rather than "value is exactly 6
        # digits" so it stays forgiving.
        self.assertEqual(normalizers.normalize_fund_code("110022"), "110022")
        self.assertEqual(normalizers.normalize_fund_code("fund 110022 page"), "110022")
        self.assertEqual(normalizers.normalize_fund_code("_110022"), "110022")
        self.assertEqual(normalizers.normalize_fund_code(110022), "110022")  # int gets str()'d

    def test_raises_on_no_six_digit_run(self) -> None:
        # The error message includes the original value so
        # ``sync_failures`` rows in the audit table are
        # self-explanatory (back-end share classes ``000002``
        # / ``000012`` were the original symptom).
        with self.assertRaises(ValueError) as cm:
            normalizers.normalize_fund_code("not-a-fund-code")
        self.assertIn("not-a-fund-code", str(cm.exception))


class ToFloatTests(unittest.TestCase):
    def test_plain_numeric(self) -> None:
        self.assertEqual(normalizers._to_float("1.5"), 1.5)
        self.assertEqual(normalizers._to_float("-1.5"), -1.5)

    def test_strips_thousands_separator(self) -> None:
        # AkShare / Tushare sometimes emit ``"1,234.56"``;
        # a future bug here would silently round NAV history
        # values. Pin the behaviour.
        self.assertEqual(normalizers._to_float("1,234.56"), 1234.56)

    def test_percent_divides_by_100(self) -> None:
        self.assertEqual(normalizers._to_float("1.5%", percent=True), 0.015)
        self.assertEqual(normalizers._to_float("100%", percent=True), 1.0)

    def test_returns_none_on_missing_tokens(self) -> None:
        # Eastmoney / AkShare / Tushare use different strings
        # to signal "no data". All collapse to None so the
        # SQLite column is NULL, not "0.0".
        for token in ("", "-", "--", "---", "暂无数据", "暂未披露", "nan", "NaN"):
            with self.subTest(token=token):
                self.assertIsNone(normalizers._to_float(token))
                self.assertIsNone(normalizers._to_float(token, percent=True))

    def test_returns_none_on_unparseable(self) -> None:
        self.assertIsNone(normalizers._to_float("not a number"))
        self.assertIsNone(normalizers._to_float(None))


class IsMissingTests(unittest.TestCase):
    def test_missing_tokens(self) -> None:
        for token in ("", " ", "-", "--", "---", "暂无数据", "暂未披露", "nan", "NaN"):
            with self.subTest(token=token):
                self.assertTrue(normalizers._is_missing(token))

    def test_non_missing_values(self) -> None:
        for value in ("hello", "0", "0.0", "-1.5", "100%"):
            with self.subTest(value=value):
                self.assertFalse(normalizers._is_missing(value))

    def test_none_and_nan(self) -> None:
        self.assertTrue(normalizers._is_missing(None))
        self.assertTrue(normalizers._is_missing(float("nan")))


class CleanTextTests(unittest.TestCase):
    def test_collapses_whitespace(self) -> None:
        self.assertEqual(
            normalizers._clean_text("  hello   world  \n\t"),
            "hello world",
        )

    def test_empty_on_missing(self) -> None:
        for token in ("", "-", "暂无数据", None):
            with self.subTest(token=token):
                self.assertEqual(normalizers._clean_text(token), "")


class FirstValueTests(unittest.TestCase):
    def test_returns_first_present(self) -> None:
        row = {"a": "", "项目": "基金全称", "value": "易方达蓝筹精选", "other": "ignored"}
        self.assertEqual(
            normalizers._first_value(row, "item", "项目", "名称", "key", "label"),
            "基金全称",
        )

    def test_skips_missing_keys(self) -> None:
        row = {"item": "---", "项目": "---", "名称": "基金全称"}
        self.assertEqual(
            normalizers._first_value(row, "item", "项目", "名称"),
            "基金全称",
        )

    def test_returns_none_when_all_missing(self) -> None:
        row = {"item": "暂无数据", "项目": "---"}
        self.assertIsNone(
            normalizers._first_value(row, "item", "项目")
        )

    def test_returns_none_when_no_key_matches(self) -> None:
        row = {"fund_code": "110022"}
        self.assertIsNone(
            normalizers._first_value(row, "item", "项目", "名称")
        )


class FirstNumberTests(unittest.TestCase):
    def test_extracts_decimal(self) -> None:
        self.assertEqual(normalizers._first_number("1.5"), 1.5)
        self.assertEqual(normalizers._first_number("-1.5"), -1.5)

    def test_strips_thousands_separator(self) -> None:
        self.assertEqual(normalizers._first_number("1,234.56"), 1234.56)

    def test_extracts_from_text(self) -> None:
        # _first_number returns the *first* numeric run in the
        # string, which is the integer run before the decimal
        # run in "近1年 +1.5%". The decimal run is only matched
        # by a regex that includes the optional fractional part
        # anchored to the integer; the helper is deliberately
        # simple and does not try to merge adjacent runs.
        self.assertEqual(normalizers._first_number("近1年 +1.5%"), 1.0)

    def test_returns_none_on_no_digit(self) -> None:
        self.assertIsNone(normalizers._first_number("no number"))
        self.assertIsNone(normalizers._first_number(None))


class RateToDecimalTests(unittest.TestCase):
    def test_basic_percent(self) -> None:
        self.assertEqual(normalizers._rate_to_decimal("1.5%"), 0.015)
        self.assertEqual(normalizers._rate_to_decimal("100%"), 1.0)

    def test_returns_none_on_no_percent_sign(self) -> None:
        # Defensive: a bare number is not silently re-interpreted.
        # Use _to_float(percent=True) for that.
        self.assertIsNone(normalizers._rate_to_decimal("1.5"))
        self.assertIsNone(normalizers._rate_to_decimal("100"))


class RatioValueTests(unittest.TestCase):
    def test_ratio_string(self) -> None:
        # "30:70" → 70/30 = 2.3333...
        self.assertAlmostEqual(normalizers._ratio_value("30:70"), 2.33333333, places=7)

    def test_plain_number_falls_through(self) -> None:
        self.assertEqual(normalizers._ratio_value("0.875"), 0.875)

    def test_empty_or_missing(self) -> None:
        self.assertIsNone(normalizers._ratio_value(""))
        self.assertIsNone(normalizers._ratio_value(None))
        self.assertIsNone(normalizers._ratio_value("--"))


class NormalizeDateTextTests(unittest.TestCase):
    def test_iso_with_dash(self) -> None:
        self.assertEqual(normalizers._normalize_date_text("2024-12-31"), "2024-12-31")

    def test_iso_with_slash(self) -> None:
        self.assertEqual(normalizers._normalize_date_text("2024/12/31"), "2024-12-31")

    def test_chinese_format(self) -> None:
        self.assertEqual(
            normalizers._normalize_date_text("2024年12月31日"),
            "2024-12-31",
        )

    def test_zero_pads_single_digit(self) -> None:
        self.assertEqual(
            normalizers._normalize_date_text("2024年1月2日"),
            "2024-01-02",
        )

    def test_empty_or_missing_returns_empty(self) -> None:
        self.assertEqual(normalizers._normalize_date_text(""), "")
        self.assertEqual(normalizers._normalize_date_text(None), "")

    def test_unrecognised_returns_as_is(self) -> None:
        # Defensive: an unparseable date does not break a
        # backfill run; the row is written verbatim and the
        # operator can decide.
        self.assertEqual(
            normalizers._normalize_date_text("Q1 2024"),
            "Q1 2024",
        )


class NormalizeReportPeriodTests(unittest.TestCase):
    """Pin the four shapes that show up in the local data base
    (and the fifth that AkShare emits with a suffix). See the
    function docstring for the table."""

    def test_quarter_with_akshare_suffix_equity(self) -> None:
        self.assertEqual(
            normalizers._normalize_report_period("2024年4季度股票投资明细"),
            "2024-12-31",
        )

    def test_quarter_with_akshare_suffix_bond(self) -> None:
        self.assertEqual(
            normalizers._normalize_report_period("2024年4季度债券投资明细"),
            "2024-12-31",
        )

    def test_quarter_bare(self) -> None:
        self.assertEqual(
            normalizers._normalize_report_period("2024年4季度"),
            "2024-12-31",
        )

    def test_iso_idempotent(self) -> None:
        self.assertEqual(
            normalizers._normalize_report_period("2024-12-31"),
            "2024-12-31",
        )

    def test_year_only(self) -> None:
        self.assertEqual(
            normalizers._normalize_report_period("2024"),
            "2024-12-31",
        )

    def test_empty_or_missing(self) -> None:
        self.assertEqual(normalizers._normalize_report_period(""), "")
        self.assertEqual(normalizers._normalize_report_period(None), "")

    def test_all_four_quarters_map_to_correct_end_day(self) -> None:
        # Q1 → 03-31, Q2 → 06-30, Q3 → 09-30, Q4 → 12-31
        self.assertEqual(
            normalizers._normalize_report_period("2024年1季度"), "2024-03-31"
        )
        self.assertEqual(
            normalizers._normalize_report_period("2024年2季度"), "2024-06-30"
        )
        self.assertEqual(
            normalizers._normalize_report_period("2024年3季度"), "2024-09-30"
        )
        self.assertEqual(
            normalizers._normalize_report_period("2024年4季度"), "2024-12-31"
        )


class FeeIndicatorAliasTests(unittest.TestCase):
    """The two fee tables AkShare emits prefix every indicator
    with ``（前端）`` or ``（后端）``. The SQL schema is keyed
    on the un-prefixed label, so the helper strips the suffix."""

    def test_strips_frontend_suffix(self) -> None:
        self.assertEqual(
            normalizers._fee_indicator_alias("申购费率（前端）"),
            "申购费率",
        )

    def test_strips_backend_suffix(self) -> None:
        self.assertEqual(
            normalizers._fee_indicator_alias("申购费率（后端）"),
            "申购费率",
        )

    def test_passes_through_unmapped_label(self) -> None:
        self.assertEqual(
            normalizers._fee_indicator_alias("管理费率"),
            "管理费率",
        )


class ProfileDictTests(unittest.TestCase):
    def test_labeled_row_shape(self) -> None:
        records = [
            {"item": "基金全称", "value": "易方达蓝筹精选"},
            {"item": "基金代码", "value": "110022"},
        ]
        self.assertEqual(
            normalizers._profile_dict(records),
            {"基金全称": "易方达蓝筹精选", "基金代码": "110022"},
        )

    def test_single_row_shape_treats_keys_as_labels(self) -> None:
        # When the row has no recognised label key, the helper
        # treats each (key, value) pair as a (label, value).
        records = [{"fund_name": "易方达", "fund_code": "110022"}]
        self.assertEqual(
            normalizers._profile_dict(records),
            {"fund_name": "易方达", "fund_code": "110022"},
        )

    def test_skips_rows_with_missing_key_or_value(self) -> None:
        records = [
            {"item": "基金全称", "value": "易方达"},
            {"item": "---", "value": "skipped because key is missing"},
            {"item": "基金代码", "value": "---"},  # value missing, skipped
        ]
        self.assertEqual(
            normalizers._profile_dict(records),
            {"基金全称": "易方达"},
        )


class RecordsTests(unittest.TestCase):
    def test_none_returns_empty(self) -> None:
        self.assertEqual(normalizers._records(None), [])

    def test_list_passes_through(self) -> None:
        self.assertEqual(
            normalizers._records([{"a": 1}, {"a": 2}]),
            [{"a": 1}, {"a": 2}],
        )

    def test_pandas_dataframe_via_to_dict(self) -> None:
        # The AkShare provider sometimes returns a pandas
        # DataFrame instead of a list of dicts. Mock the
        # duck-typed ``to_dict`` so we exercise the path
        # without a pandas dependency in the test env.
        class FakeFrame:
            def to_dict(self, _orient: str) -> list[dict[str, object]]:
                return [{"a": 1}, {"a": 2}]

        self.assertEqual(
            normalizers._records(FakeFrame()),
            [{"a": 1}, {"a": 2}],
        )

    def test_unsupported_type_raises(self) -> None:
        with self.assertRaises(TypeError):
            normalizers._records(42)  # type: ignore[arg-type]


class ExtractPayloadRecordsTests(unittest.TestCase):
    """Each provider uses a different envelope key. The helper
    tries a list of common ones and a single level of recursion
    so callers do not have to."""

    def test_list_passes_through(self) -> None:
        self.assertEqual(
            normalizers._extract_payload_records([{"a": 1}]),
            [{"a": 1}],
        )

    def test_data_envelope(self) -> None:
        self.assertEqual(
            normalizers._extract_payload_records({"data": [{"a": 1}]}),
            [{"a": 1}],
        )

    def test_datas_envelope_eastmoney(self) -> None:
        # Eastmoney capitalises the key.
        self.assertEqual(
            normalizers._extract_payload_records({"Datas": [{"a": 1}]}),
            [{"a": 1}],
        )

    def test_nested_envelope(self) -> None:
        # The ``data`` key is itself a dict (Tushare's
        # ``data.fields + data.items`` shape), so the helper
        # recurses one level.
        self.assertEqual(
            normalizers._extract_payload_records(
                {"data": {"items": [{"a": 1}]}}
            ),
            [{"a": 1}],
        )

    def test_returns_empty_when_no_match(self) -> None:
        self.assertEqual(
            normalizers._extract_payload_records({"unknown_key": "no rows"}),
            [],
        )


class JsonDumpsTests(unittest.TestCase):
    def test_unicode_preserved(self) -> None:
        # ``ensure_ascii=False`` so a row with a Chinese
        # fund_name round-trips through JSON without
        # ``\\uXXXX`` escape noise in the SQLite store.
        self.assertIn("易方达", normalizers._json_dumps({"name": "易方达"}))

    def test_keys_sorted(self) -> None:
        # ``sort_keys=True`` so two equivalent dicts produce
        # the same SQLite row text. A regression here would
        # silently double-write the same row.
        self.assertEqual(
            normalizers._json_dumps({"b": 1, "a": 2}),
            '{"a": 2, "b": 1}',
        )


if __name__ == "__main__":
    unittest.main()
