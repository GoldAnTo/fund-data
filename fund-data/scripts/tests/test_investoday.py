"""Unit tests for ``InvestodayProvider``.

The provider is exercised through a fake ``_get_json`` so the test
suite stays offline. We cover:

- env var resolution (INVESTODAY_API_KEY preferred, INVESTDATA_API_KEY
  kept as a legacy fallback)
- the failure mode when neither env var is set
- ``fund_list`` auto-paginating across ``/fund/all`` and stripping the
  internal ``_raw`` payload before returning
- ``search_funds`` reusing the cached catalog
- ``profile`` field mapping from the raw Investoday record, including
  the ``establishDate`` ISO-date trim
- ``profile`` raising ``ProviderError`` for codes not in the catalog
- the catalog cache TTL behavior (a second call within the TTL does
  not hit the network a second time)
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import fund_data  # noqa: E402


def _build_record(code: str = "110022") -> dict:
    """Mirror the shape of one real /fund/all record (subset)."""
    return {
        "fundCode": code,
        "fundName": "易方达消费行业股票",
        "fundNameFull": "易方达消费行业股票型证券投资基金",
        "fundType": None,
        "managementCompanyName": "易方达基金管理有限公司",
        "custodianName": "中国农业银行股份有限公司",
        "establishDate": "2010-08-20 00:00:00",
        "benchmarkCode": "中证内地消费指数×85%+中债总指数×15%",
        "isQdii": 0,
        "isFof": 0,
        "investmentObjective": "    本基金主要投资消费行业股票。",
        "investmentStrategy": "    定量+定性宏观及市场分析。",
        "riskReturnProfile": "主动股票基金，风险收益高于混合和债券基金。",
    }


class FakeInvestoday(fund_data.InvestodayProvider):
    """Drop-in replacement that swaps the HTTP layer for a MagicMock
    and skips ``__init__`` (which would otherwise require a real key).
    """

    def __init__(
        self,
        get_json_responses: list | None = None,
        post_json_responses: list | None = None,
    ) -> None:
        self.api_key = "test"
        self.base_url = "https://example.test"
        self._catalog_cache = None
        self._catalog_cache_ts = 0.0
        self._catalog_cache_ttl = 3600.0
        self._get_json = MagicMock(side_effect=get_json_responses or [])
        self._post_json = MagicMock(side_effect=post_json_responses or [])


class EnvVarFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            k: os.environ.pop(k, None) for k in ("INVESTODAY_API_KEY", "INVESTDATA_API_KEY")
        }

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_init_prefers_investoday_api_key(self) -> None:
        os.environ["INVESTODAY_API_KEY"] = "preferred"
        os.environ["INVESTDATA_API_KEY"] = "legacy"
        provider = fund_data.InvestodayProvider(api_key=None)
        self.assertEqual(provider.api_key, "preferred")

    def test_init_falls_back_to_investdata_api_key(self) -> None:
        os.environ["INVESTDATA_API_KEY"] = "legacy"
        provider = fund_data.InvestodayProvider(api_key=None)
        self.assertEqual(provider.api_key, "legacy")

    def test_init_raises_when_neither_set(self) -> None:
        with self.assertRaises(fund_data.ProviderError) as ctx:
            fund_data.InvestodayProvider(api_key=None)
        self.assertIn("INVESTODAY_API_KEY", str(ctx.exception))

    def test_explicit_api_key_wins_over_env(self) -> None:
        os.environ["INVESTODAY_API_KEY"] = "from-env"
        provider = fund_data.InvestodayProvider(api_key="explicit")
        self.assertEqual(provider.api_key, "explicit")


class FundListPaginationTests(unittest.TestCase):
    @staticmethod
    def _tiny_record(code: str) -> dict:
        # `normalize_fund_code` requires a 6-digit string; keep the
        # fixture within that range.
        six = f"{(hash(code) % 900000) + 100000:06d}"
        return {"fundCode": six, "fundName": f"fund-{code}"}

    def test_auto_paginates_until_total(self) -> None:
        page1 = {
            "totalCount": 750,
            "data": [self._tiny_record(f"AAAAAA{i:02d}") for i in range(500)],
        }
        page2 = {
            "totalCount": 750,
            "data": [self._tiny_record(f"BBBBBB{i:02d}") for i in range(250)],
        }
        provider = FakeInvestoday([page1, page2])
        rows = provider.fund_list()
        self.assertEqual(len(rows), 750)
        self.assertEqual(provider._get_json.call_count, 2)

    def test_stops_on_short_page(self) -> None:
        # When a page is short, we stop even if totalCount is wrong.
        short = {
            "totalCount": 99999,
            "data": [self._tiny_record(f"CCCC{i:02d}") for i in range(3)],
        }
        provider = FakeInvestoday([short])
        rows = provider.fund_list()
        self.assertEqual(len(rows), 3)
        self.assertEqual(provider._get_json.call_count, 1)

    def test_strips_raw_payload_from_returned_rows(self) -> None:
        page = {"totalCount": 1, "data": [_build_record("110022")]}
        provider = FakeInvestoday([page])
        rows = provider.fund_list()
        self.assertEqual(len(rows), 1)
        self.assertNotIn("_raw", rows[0])
        self.assertEqual(rows[0]["fund_code"], "110022")
        self.assertEqual(rows[0]["source"], "investoday.fund_all")

    def test_cache_avoids_second_network_call(self) -> None:
        page = {"totalCount": 1, "data": [_build_record("110022")]}
        provider = FakeInvestoday([page, page])  # second response would error if called
        provider.fund_list()
        provider.fund_list()  # should hit cache, not _get_json again
        self.assertEqual(provider._get_json.call_count, 1)


class SearchFundsTests(unittest.TestCase):
    def test_search_uses_cache_and_matches_code_or_name(self) -> None:
        second = _build_record("110023")
        second["fundName"] = "易方达消费行业股票C"
        page = {"totalCount": 2, "data": [_build_record("110022"), second]}
        provider = FakeInvestoday([page])
        provider.search_funds("110022")
        provider.search_funds("C")  # second call should not re-hit the network
        self.assertEqual(provider._get_json.call_count, 1)


class ProfileTests(unittest.TestCase):
    def test_profile_field_mapping(self) -> None:
        page = {"totalCount": 1, "data": [_build_record("110022")]}
        provider = FakeInvestoday([page])
        profile = provider.profile("110022")
        self.assertEqual(profile["fund_code"], "110022")
        self.assertEqual(profile["fund_name"], "易方达消费行业股票")
        self.assertEqual(profile["full_name"], "易方达消费行业股票型证券投资基金")
        self.assertEqual(profile["establishment_date"], "2010-08-20")
        self.assertEqual(profile["fund_company"], "易方达基金管理有限公司")
        self.assertEqual(profile["custodian"], "中国农业银行股份有限公司")
        self.assertEqual(profile["benchmark"], "中证内地消费指数×85%+中债总指数×15%")
        self.assertEqual(profile["source"], "investoday.fund_all")
        self.assertFalse(profile["is_qdii"])
        self.assertFalse(profile["is_fof"])
        # Strategy text was non-empty in the fixture
        self.assertIn("定量+定性", profile["investment_strategy"])

    def test_profile_raises_for_unknown_code(self) -> None:
        page = {"totalCount": 1, "data": [_build_record("110022")]}
        provider = FakeInvestoday([page])
        with self.assertRaises(fund_data.ProviderError) as ctx:
            provider.profile("999999")
        self.assertIn("999999", str(ctx.exception))
        self.assertIn("not found", str(ctx.exception))

    def test_profile_does_not_re_initialize_catalog_when_cache_warm(self) -> None:
        page = {"totalCount": 1, "data": [_build_record("110022")]}
        provider = FakeInvestoday([page])
        provider.fund_list()  # warm the cache
        provider.profile("110022")  # should not call _get_json again
        self.assertEqual(provider._get_json.call_count, 1)


class NavHistoryTests(unittest.TestCase):
    def test_nav_history_posts_and_maps_live_investoday_fields(self) -> None:
        provider = FakeInvestoday(
            post_json_responses=[
                {
                    "code": 0,
                    "message": "success",
                    "totalCount": 1,
                    "data": [
                        {
                            "fundCode": "110022",
                            "date": "2023-12-29",
                            "nav": 3.199,
                            "navAcc": 3.199,
                        },
                        {
                            "fundCode": "110022",
                            "date": "2024-01-02",
                            "nav": 3.211,
                            "navAcc": 3.211,
                        }
                    ],
                }
            ]
        )
        rows = provider.nav_history("110022", start_date="2024-01-01", end_date="2024-01-10")
        self.assertEqual(provider._post_json.call_count, 1)
        self.assertEqual(provider._post_json.call_args.args[0], "/fund/nav/history")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["nav_date"], "2024-01-02")
        self.assertEqual(rows[0]["unit_nav"], 3.211)
        self.assertEqual(rows[0]["accumulated_nav"], 3.211)
        self.assertEqual(rows[0]["source"], "investoday.fund_nav_history")


class StockHoldingsTests(unittest.TestCase):
    def test_stock_holdings_posts_and_maps_live_investoday_fields(self) -> None:
        provider = FakeInvestoday(
            post_json_responses=[
                {
                    "code": 0,
                    "message": "success",
                    "totalCount": 1,
                    "data": [
                        {
                            "fundCode": "110022",
                            "date": "2026-04-22",
                            "stockCode": "000333",
                            "stockName": "美的集团",
                            "navRatio": 9.64,
                            "holdingShares": 1600,
                            "marketValue": 1220000,
                        },
                        {
                            "fundCode": "110022",
                            "date": "2024-12-31",
                            "stockCode": "600519",
                            "stockName": "贵州茅台",
                            "navRatio": 7.5,
                            "holdingShares": 1000,
                            "marketValue": 1500000,
                        }
                    ],
                }
            ]
        )
        rows = provider.stock_holdings("110022", report_year="2024")
        self.assertEqual(provider._post_json.call_count, 1)
        self.assertEqual(provider._post_json.call_args.args[0], "/fund/portfolio-stock-holdings")
        self.assertEqual(rows[0]["report_period"], "2024-12-31")
        self.assertEqual(rows[0]["stock_code"], "600519")
        self.assertEqual(rows[0]["stock_name"], "贵州茅台")
        self.assertEqual(rows[0]["net_value_ratio"], 0.075)
        self.assertEqual(rows[0]["shares"], 1000)
        self.assertEqual(rows[0]["market_value"], 1500000)
        self.assertEqual(rows[0]["source"], "investoday.fund_portfolio_stock_holdings")


if __name__ == "__main__":
    unittest.main()
