import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import fund_data

SEARCH_PAYLOAD = json.dumps(
    {
        "ErrCode": 0,
        "ErrMsg": "fromes",
        "Datas": [
            {
                "CODE": "006600",
                "NAME": "人保沪深300A",
                "CATEGORYDESC": "基金",
                "FundBaseInfo": {
                    "FCODE": "006600",
                    "SHORTNAME": "人保沪深300A",
                    "JJGS": "人保资产",
                    "JJJL": "周剑",
                    "FTYPE": "指数型-股票",
                    "FSRQ": "2026-05-29",
                    "DWJZ": 1.4646,
                    "OTHERNAME": "人保沪深300,人保沪深300指数",
                },
            }
        ],
    },
    ensure_ascii=False,
)

NAV_PAYLOAD = """var apidata={ content:"<table class='w782 comm lsjz'><thead><tr><th class='first'>净值日期</th><th>单位净值</th><th>累计净值</th><th>日增长率</th><th>申购状态</th><th>赎回状态</th><th class='tor last'>分红送配</th></tr></thead><tbody><tr><td>2024-01-31</td><td class='tor bold'>3.1330</td><td class='tor bold'>3.1330</td><td class='tor bold grn'>-1.32%</td><td>开放申购</td><td>开放赎回</td><td class='red unbold'></td></tr><tr><td>2024-01-30</td><td class='tor bold'>3.1750</td><td class='tor bold'>3.1750</td><td class='tor bold grn'>-2.34%</td><td>开放申购</td><td>开放赎回</td><td class='red unbold'></td></tr></tbody></table>",records:22,pages:5,curpage:1};"""

SNAPSHOT_PAYLOAD = """var ishb=false;var fS_name = "易方达消费行业股票";var fS_code = "110022";var fund_sourceRate="1.50";var fund_Rate="0.15";var fund_minsg="10";var stockCodesNew =["1.600519","0.000333"];var syl_1n="-17.03";var syl_6y="-19.48";var syl_3y="-12.3";var syl_1y="-8.52";"""
FUND_CODE_LIST_PAYLOAD = 'var r = [["000001","HXCZHH","华夏成长混合","混合型-灵活","HUAXIACHENGZHANGHUNHE"],["110022","YFDXFHYGP","易方达消费行业股票","股票型","YIFANGDAXIAOFEIHANGYEGUPIAO"]];'


class SimpleFrame:
    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, orient):
        if orient != "records":
            raise ValueError(orient)
        return self.rows


PROFILE_ROWS = [
    {"item": "基金简称", "value": "易方达消费行业股票"},
    {"item": "基金全称", "value": "易方达消费行业股票型证券投资基金"},
    {"item": "基金类型", "value": "股票型"},
    {"item": "发行日期", "value": "2010-07-26"},
    {"item": "成立日期/规模", "value": "2010-08-20 / 63.7亿份"},
    {"item": "资产规模", "value": "218.53亿元（截止至：2024-12-31）"},
    {"item": "基金管理人", "value": "易方达基金"},
    {"item": "基金托管人", "value": "中国工商银行"},
    {"item": "基金经理人", "value": "萧楠"},
    {"item": "业绩比较基准", "value": "中证内地消费主题指数收益率*85%+活期存款利率*15%"},
]


class FundDataParserTests(unittest.TestCase):
    def test_parse_search_results_normalizes_core_fund_fields(self):
        rows = fund_data.parse_search_results(SEARCH_PAYLOAD)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["fund_code"], "006600")
        self.assertEqual(rows[0]["fund_name"], "人保沪深300A")
        self.assertEqual(rows[0]["fund_type"], "指数型-股票")
        self.assertEqual(rows[0]["company"], "人保资产")
        self.assertEqual(rows[0]["manager"], "周剑")
        self.assertEqual(rows[0]["nav"], 1.4646)
        self.assertEqual(rows[0]["nav_date"], "2026-05-29")

    def test_parse_nav_history_converts_percentages_to_decimals(self):
        rows = fund_data.parse_nav_history(NAV_PAYLOAD)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["nav_date"], "2024-01-31")
        self.assertEqual(rows[0]["unit_nav"], 3.133)
        self.assertEqual(rows[0]["accumulated_nav"], 3.133)
        self.assertAlmostEqual(rows[0]["daily_growth_rate"], -0.0132)
        self.assertEqual(rows[0]["subscribe_status"], "开放申购")
        self.assertEqual(rows[0]["redeem_status"], "开放赎回")

    def test_parse_snapshot_extracts_core_fields_and_holdings_codes(self):
        snapshot = fund_data.parse_snapshot(SNAPSHOT_PAYLOAD)

        self.assertEqual(snapshot["fund_code"], "110022")
        self.assertEqual(snapshot["fund_name"], "易方达消费行业股票")
        self.assertEqual(snapshot["source_rate"], 1.5)
        self.assertEqual(snapshot["current_rate"], 0.15)
        self.assertEqual(snapshot["min_purchase"], 10.0)
        self.assertEqual(snapshot["stock_codes"], ["1.600519", "0.000333"])
        self.assertAlmostEqual(snapshot["returns"]["one_year"], -0.1703)

    def test_parse_snapshot_returns_none_for_empty_page(self):
        # Back-end share classes (000002, 000012, ...) hit a stub
        # Eastmoney page that is essentially empty. parse_snapshot
        # must return None so the caller can distinguish "no
        # standalone snapshot" from a parse error -- the previous
        # behaviour raised ``fund code must contain 6 digits: ''``
        # and ended up in sync_failures with a confusing message.
        self.assertIsNone(fund_data.parse_snapshot(""))
        self.assertIsNone(fund_data.parse_snapshot("   \n  \t  "))

    def test_parse_snapshot_falls_back_to_default_code(self):
        # The JS body has the structure Eastmoney emits but the
        # embedded fS_code token is blank (a real edge case when
        # the server returns a partially populated response). The
        # caller-supplied default_code is used so the snapshot row
        # is still tied to the fund that the caller requested.
        body = (
            'var fS_name = "";var fS_code = "";var fund_sourceRate="";'
            'var fund_Rate="";var fund_minsg="";var stockCodesNew =[];'
            'var syl_1n="";var syl_6y="";var syl_3y="";var syl_1y="";'
        )
        snapshot = fund_data.parse_snapshot(body, default_code="000002")

        self.assertEqual(snapshot["fund_code"], "000002")
        self.assertEqual(snapshot["fund_name"], "")
        self.assertIsNone(snapshot["source_rate"])
        self.assertEqual(snapshot["stock_codes"], [])

    def test_parse_snapshot_raises_when_no_code_available(self):
        # No body content AND no default_code -- genuine parse
        # failure. The caller did not give us a fallback and the
        # page has nothing to extract, so the error must surface.
        body = 'var fS_name = "";var fS_code = "";'
        with self.assertRaises(ValueError) as ctx:
            fund_data.parse_snapshot(body)
        self.assertIn("fS_code", str(ctx.exception))

    def test_parse_fund_code_list_normalizes_full_market_rows(self):
        rows = fund_data.parse_fund_code_list(FUND_CODE_LIST_PAYLOAD)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["fund_code"], "000001")
        self.assertEqual(rows[0]["fund_name"], "华夏成长混合")
        self.assertEqual(rows[0]["source"], "eastmoney.fundcode_search")

    def test_parse_fund_codes_accepts_lines_commas_comments_and_dedupes(self):
        codes = fund_data.parse_fund_codes("""
            # core watchlist
            110022, 000001
            006600 人保沪深300A
            110022
            """)

        self.assertEqual(codes, ["110022", "000001", "006600"])


class FundDataProviderTests(unittest.TestCase):
    def test_run_provider_chain_falls_back_to_second_provider(self):
        class FailingProvider:
            name = "failing"

            def search_funds(self, keyword):
                raise fund_data.ProviderError("temporary failure")

        class WorkingProvider:
            name = "working"

            def search_funds(self, keyword):
                return [{"fund_code": "000001", "fund_name": keyword, "source": "working.search"}]

        result = fund_data.run_provider_chain(
            [FailingProvider(), WorkingProvider()], "search_funds", "华夏"
        )

        self.assertEqual(result.provider, "working")
        self.assertEqual(result.rows[0]["fund_code"], "000001")
        self.assertEqual(result.failures[0]["provider"], "failing")

    def test_run_provider_chain_can_allow_empty_sparse_domain_rows(self):
        class EmptyProvider:
            name = "empty"

            def dividends(self, code):
                return []

        result = fund_data.run_provider_chain(
            [EmptyProvider()], "dividends", "110022", allow_empty=True
        )

        self.assertEqual(result.rows, [])

    def test_build_providers_logs_warning_when_provider_init_fails_in_auto(self):
        """When auto mode cannot init a provider, it must not silently drop the
        failure: the user should see a warning explaining which provider is
        unavailable and why, so they can fix the environment instead of
        wondering why the chain returned empty results."""

        original_akshare_init = fund_data.AkshareProvider.__init__
        saved_investoday_env = {
            key: os.environ.pop(key, None)
            for key in ("INVESTODAY_API_KEY", "INVESTDATA_API_KEY")
        }
        fund_data.AkshareProvider.__init__ = lambda self: (_ for _ in ()).throw(
            fund_data.ProviderError("akshare is not installed for test")
        )

        try:
            with self.assertLogs("fund_data", level="WARNING") as log_ctx:
                providers = fund_data.build_providers("auto", capability="profile")
        finally:
            fund_data.AkshareProvider.__init__ = original_akshare_init
            for key, value in saved_investoday_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        # The remaining provider (Eastmoney) must still be returned so the
        # caller can keep working with whatever is available.
        self.assertEqual([type(p).__name__ for p in providers], ["EastmoneyProvider"])
        # The warning must mention the missing provider and that it is
        # unavailable so the user can act on it.
        joined = "\n".join(log_ctx.output)
        self.assertIn("akshare", joined)
        self.assertIn("unavailable", joined)

    def test_build_providers_still_raises_when_explicit_provider_missing(self):
        """Explicit --provider akshare must still raise loudly when akshare
        is not installed; auto mode is the only one allowed to degrade."""

        original_akshare_init = fund_data.AkshareProvider.__init__
        fund_data.AkshareProvider.__init__ = lambda self: (_ for _ in ()).throw(
            fund_data.ProviderError("akshare is not installed for test")
        )

        try:
            with self.assertRaises(fund_data.ProviderError):
                fund_data.build_providers("akshare", capability="profile")
        finally:
            fund_data.AkshareProvider.__init__ = original_akshare_init

    def test_akshare_profile_accepts_wide_overview_rows(self):
        class FakeAkshare:
            @staticmethod
            def fund_overview_em(symbol):
                return SimpleFrame(
                    [
                        {
                            "基金全称": "易方达消费行业股票型证券投资基金",
                            "基金简称": "易方达消费行业股票",
                            "基金代码": "110022（前端）",
                            "基金类型": "股票型",
                            "发行日期": "2010年07月26日",
                            "成立日期/规模": "2010年08月20日 / 63.727亿份",
                            "净资产规模": "126.86亿元（截止至：2026年03月31日）",
                            "基金管理人": "易方达基金",
                            "基金托管人": "农业银行",
                            "基金经理人": "萧楠",
                        }
                    ]
                )

        profile = fund_data.AkshareProvider(ak_module=FakeAkshare()).profile("110022")

        self.assertEqual(profile["fund_name"], "易方达消费行业股票")
        self.assertEqual(profile["issue_date"], "2010-07-26")
        self.assertEqual(profile["establishment_date"], "2010-08-20")
        self.assertEqual(profile["asset_size"], 126.86)
        self.assertEqual(profile["asset_size_date"], "2026-03-31")

    def test_akshare_provider_normalizes_fund_list_nav_and_stock_holdings(self):
        class FakeAkshare:
            @staticmethod
            def fund_name_em():
                return SimpleFrame(
                    [
                        {
                            "基金代码": "000001",
                            "拼音缩写": "HXCZHH",
                            "基金简称": "华夏成长混合",
                            "基金类型": "混合型-灵活",
                            "拼音全称": "HUAXIACHENGZHANGHUNHE",
                        },
                        {
                            "基金代码": "110022",
                            "拼音缩写": "YFDXF",
                            "基金简称": "易方达消费行业股票",
                            "基金类型": "股票型",
                            "拼音全称": "YIFANGDAXIAOFEI",
                        },
                    ]
                )

            @staticmethod
            def fund_open_fund_info_em(symbol, indicator="单位净值走势"):
                return SimpleFrame(
                    [
                        {
                            "净值日期": "2024-01-31",
                            "单位净值": "3.1330",
                            "累计净值": "3.1330",
                            "日增长率": "-1.32%",
                        }
                    ]
                )

            @staticmethod
            def fund_portfolio_hold_em(symbol, date):
                return SimpleFrame(
                    [
                        {
                            "序号": 1,
                            "股票代码": "600519",
                            "股票名称": "贵州茅台",
                            "占净值比例": 9.83,
                            "持股数": 12.34,
                            "持仓市值": 56789.0,
                            "季度": "2024年4季度股票投资明细",
                        }
                    ]
                )

            @staticmethod
            def fund_overview_em(symbol):
                return SimpleFrame(PROFILE_ROWS)

            @staticmethod
            def fund_portfolio_bond_hold_em(symbol, date):
                return SimpleFrame(
                    [
                        {
                            "债券代码": "127045",
                            "债券名称": "牧原转债",
                            "占净值比例": 0.31,
                            "持仓市值": 1234.56,
                            "季度": "2024年4季度债券投资明细",
                        }
                    ]
                )

            @staticmethod
            def fund_portfolio_industry_allocation_em(symbol, date):
                return SimpleFrame(
                    [
                        {
                            "行业类别": "制造业",
                            "占净值比例": 83.21,
                            "季度": "2024年4季度行业配置",
                        }
                    ]
                )

            @staticmethod
            def fund_fee_em(symbol, indicator):
                return SimpleFrame(
                    [
                        {
                            "费用类型": indicator,
                            "条件或名称": "小于100万元",
                            "费用": 0.15,
                        }
                    ]
                )

            @staticmethod
            def fund_fh_em():
                return SimpleFrame(
                    [
                        {
                            "基金代码": "110022",
                            "基金简称": "易方达消费行业股票",
                            "权益登记日": "2021-01-15",
                            "除息日期": "2021-01-15",
                            "分红": 0.1,
                            "分红发放日": "2021-01-19",
                        }
                    ]
                )

            @staticmethod
            def fund_cf_em():
                return SimpleFrame(
                    [
                        {
                            "基金代码": "110022",
                            "基金简称": "易方达消费行业股票",
                            "拆分折算日": "2019-01-10",
                            "拆分类型": "份额折算",
                            "拆分折算比例": 1.5,
                        }
                    ]
                )

            @staticmethod
            def fund_manager_em():
                return SimpleFrame(
                    [
                        {
                            "姓名": "萧楠",
                            "所属公司": "易方达基金",
                            "现任基金代码": "110022",
                            "现任基金": "易方达消费行业股票",
                            "累计从业时间": 4000,
                            "现任基金资产总规模": 218.53,
                            "现任基金最佳回报": 300.12,
                        }
                    ]
                )

        provider = fund_data.AkshareProvider(ak_module=FakeAkshare())

        fund_rows = provider.search_funds("消费")
        nav_rows = provider.nav_history("110022")
        holding_rows = provider.stock_holdings("110022", report_year="2024")
        profile = provider.profile("110022")
        bond_rows = provider.bond_holdings("110022", report_year="2024")
        industry_rows = provider.industry_allocations("110022", report_year="2024")
        fee_rows = provider.fee_structures("110022", indicators=["申购费率"])
        dividend_rows = provider.dividends("110022")
        split_rows = provider.splits("110022")
        manager_rows = provider.fund_managers("110022")

        self.assertEqual([row["fund_code"] for row in fund_rows], ["110022"])
        self.assertEqual(fund_rows[0]["source"], "akshare.fund_name_em")
        self.assertAlmostEqual(nav_rows[0]["daily_growth_rate"], -0.0132)
        self.assertEqual(nav_rows[0]["source"], "akshare.fund_open_fund_info_em")
        self.assertEqual(holding_rows[0]["stock_code"], "600519")
        self.assertEqual(holding_rows[0]["stock_name"], "贵州茅台")
        self.assertEqual(holding_rows[0]["net_value_ratio"], 0.0983)
        self.assertEqual(holding_rows[0]["source"], "akshare.fund_portfolio_hold_em")
        self.assertEqual(profile["fund_company"], "易方达基金")
        self.assertEqual(profile["custodian"], "中国工商银行")
        self.assertEqual(profile["source"], "akshare.fund_overview_em")
        self.assertEqual(bond_rows[0]["bond_code"], "127045")
        self.assertEqual(industry_rows[0]["industry_name"], "制造业")
        self.assertAlmostEqual(industry_rows[0]["net_value_ratio"], 0.8321)
        self.assertEqual(fee_rows[0]["fee_type"], "申购费率")
        self.assertEqual(dividend_rows[0]["dividend_date"], "2021-01-15")
        self.assertEqual(split_rows[0]["split_date"], "2019-01-10")
        self.assertEqual(manager_rows[0]["manager_name"], "萧楠")

    def test_akshare_snapshot_reuses_profile_and_holdings(self):
        """Eastmoney snapshot is empty for back-end share classes
        (000002/000012/...). The AkShare fallback must still return
        a parse_snapshot-shaped dict so upsert_snapshot can store it
        and the fund exits the sync_failures backlog."""

        class FakeAkshare:
            @staticmethod
            def fund_overview_em(symbol):
                return SimpleFrame(PROFILE_ROWS)

            @staticmethod
            def fund_portfolio_hold_em(symbol, date):
                return SimpleFrame(
                    [
                        {
                            "序号": 1,
                            "股票代码": "600519",
                            "股票名称": "贵州茅台",
                            "占净值比例": 9.83,
                            "持股数": 12.34,
                            "持仓市值": 56789.0,
                            "季度": "2024年4季度股票投资明细",
                        }
                    ]
                )

        provider = fund_data.AkshareProvider(ak_module=FakeAkshare())
        snapshot = provider.snapshot("000002")

        self.assertEqual(snapshot["fund_code"], "000002")
        self.assertEqual(snapshot["fund_name"], "易方达消费行业股票")
        self.assertEqual(snapshot["stock_codes"], ["600519"])
        # AkShare does not expose rate fields; they should land as
        # None / empty so the dict still matches parse_snapshot shape.
        self.assertIsNone(snapshot["source_rate"])
        self.assertIsNone(snapshot["current_rate"])
        self.assertIsNone(snapshot["min_purchase"])
        self.assertEqual(snapshot["returns"], {})
        self.assertIn("akshare", snapshot["source"])

    def test_akshare_snapshot_returns_empty_dict_on_failure(self):
        """If the underlying profile / holdings call raises, snapshot
        must return {} (not raise) so the provider chain can fall
        through and the caller can still see 'no snapshot' instead
        of a 500."""

        class FakeAkshare:
            @staticmethod
            def fund_overview_em(symbol):
                raise KeyError("基金简称 missing from wide overview")

            @staticmethod
            def fund_portfolio_hold_em(symbol, date):
                return SimpleFrame([])

        provider = fund_data.AkshareProvider(ak_module=FakeAkshare())
        self.assertEqual(provider.snapshot("000002"), {})

    def test_bond_holdings_accepts_alternate_ratio_and_market_value_keys(self):
        class FakeAkshare:
            @staticmethod
            def fund_portfolio_bond_hold_em(symbol, date):
                return SimpleFrame(
                    [
                        {
                            "债券代码": "019547",
                            "债券名称": "22国债07",
                            "占基金资产净值比例": 5.67,
                            "市值": 34567.89,
                            "季度": "2024年4季度债券投资明细",
                        }
                    ]
                )

        provider = fund_data.AkshareProvider(ak_module=FakeAkshare())
        rows = provider.bond_holdings("110022", report_year="2024")

        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["net_value_ratio"], 0.0567)
        self.assertEqual(rows[0]["market_value"], 34567.89)
        self.assertEqual(rows[0]["source"], "akshare.fund_portfolio_bond_hold_em")

    def test_bond_holdings_swallows_qdii_style_akshare_keyerror(self):
        class FakeAkshare:
            @staticmethod
            def fund_portfolio_bond_hold_em(symbol, date):
                raise KeyError("占净值比例")

        provider = fund_data.AkshareProvider(ak_module=FakeAkshare())
        rows = provider.bond_holdings("000041", report_year="2024")

        self.assertEqual(rows, [])

    def test_industry_allocations_returns_bond_fund_fallback_when_empty(self):
        class FakeAkshare:
            @staticmethod
            def fund_portfolio_industry_allocation_em(symbol, date):
                return SimpleFrame([])

            @staticmethod
            def fund_overview_em(symbol):
                return SimpleFrame(
                    [
                        {"item": "基金简称", "value": "纯债精选"},
                        {"item": "基金类型", "value": "债券型-纯债"},
                    ]
                )

        provider = fund_data.AkshareProvider(ak_module=FakeAkshare())
        rows = provider.industry_allocations("000001", report_year="2024")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["industry_name"], "债券/货币基金-无行业配置")
        self.assertIsNone(rows[0]["net_value_ratio"])
        self.assertIn("bond_fund_fallback", rows[0]["source"])

    def test_fee_structures_etf_fallback_when_akshare_and_page_return_empty(self):
        class FakeAkshare:
            @staticmethod
            def fund_fee_em(symbol, indicator):
                return SimpleFrame([])

            @staticmethod
            def fund_etf_fund_info_em(symbol):
                return SimpleFrame(
                    [
                        {
                            "基金代码": "510300",
                            "基金简称": "沪深300ETF",
                            "管理费率": "0.50%",
                            "托管费率": "0.10%",
                        }
                    ]
                )

        provider = fund_data.AkshareProvider(ak_module=FakeAkshare())
        # The Eastmoney page scraper hits the network; isolate the
        # fallback path by stubbing it out. Without this stub the
        # page scraper pulls live fundf10.eastmoney.com rows and
        # the test sees multiple "管理费率" entries.
        with patch.object(provider, "_fee_structures_from_eastmoney_page", return_value=[]):
            rows = provider.fee_structures("510300")

        self.assertGreater(len(rows), 0)
        mgmt_rows = [r for r in rows if r.get("condition_name") == "管理费率"]
        self.assertEqual(len(mgmt_rows), 1)
        self.assertAlmostEqual(mgmt_rows[0]["fee"], 0.005)
        self.assertIn("etf_fund_info_em", mgmt_rows[0]["source"])

    def test_industry_allocations_accepts_alternate_ratio_and_market_keys(self):
        class FakeAkshare:
            @staticmethod
            def fund_portfolio_industry_allocation_em(symbol, date):
                return SimpleFrame(
                    [
                        {
                            "行业名称": "金融业",
                            "市值占净值比例": 45.5,
                            "市场价值": 98765.43,
                            "截止时间": "2024-12-31",
                        }
                    ]
                )

        provider = fund_data.AkshareProvider(ak_module=FakeAkshare())
        rows = provider.industry_allocations("110022", report_year="2024")

        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["net_value_ratio"], 0.455)
        self.assertEqual(rows[0]["market_value"], 98765.43)

    def test_investoday_provider_normalizes_structured_records(self):
        class FakeInvestoday(fund_data.InvestodayProvider):
            def __init__(self):
                self.api_key = "test"
                self.base_url = "https://example.test"
                self._catalog_cache = None
                self._catalog_cache_ts = 0.0
                self._catalog_cache_ttl = 3600.0

            def _get_json(self, path, params):
                if path == "/fund/all":
                    return {
                        "data": [
                            {
                                "fundCode": "110022",
                                "fundName": "易方达消费行业股票",
                                "fundType": "股票型",
                                "manager": "萧楠",
                            }
                        ]
                    }
                raise AssertionError(path)

            def _post_json(self, path, params):
                if path == "/fund/nav/history":
                    return {
                        "data": [
                            {
                                "navDate": "2024-01-31",
                                "unitNav": 3.133,
                                "accumulatedNav": 3.133,
                                "dailyGrowthRate": -0.0132,
                            }
                        ]
                    }
                if path == "/fund/portfolio-stock-holdings":
                    return {
                        "data": [
                            {
                                "reportPeriod": "2024Q4",
                                "stockCode": "600519",
                                "stockName": "贵州茅台",
                                "netValueRatio": 9.83,
                                "shares": 12.34,
                                "marketValue": 56789.0,
                            }
                        ]
                    }
                raise AssertionError(path)

        provider = FakeInvestoday()

        self.assertEqual(provider.fund_list()[0]["source"], "investoday.fund_all")
        self.assertEqual(provider.nav_history("110022")[0]["source"], "investoday.fund_nav_history")
        self.assertEqual(
            provider.stock_holdings("110022", report_year="2024")[0]["source"],
            "investoday.fund_portfolio_stock_holdings",
        )


class FundDataStoreTests(unittest.TestCase):
    def test_store_connections_use_wal_and_long_busy_timeout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            store = fund_data.FundDataStore(db_path)

            with store.connect() as conn:
                journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]

            self.assertEqual(journal_mode.lower(), "wal")
            self.assertGreaterEqual(busy_timeout, 30000)

    def test_batch_sync_funds_does_not_record_back_end_share_as_failure(self):
        # Regression guard: back-end share classes (000002, 000012,
        # 000108, ...) hit a stub Eastmoney page and used to fail
        # with ``fund code must contain 6 digits: ''`` inside
        # parse_snapshot, ending up in sync_failures. After the
        # parse_snapshot default_code fix, the empty page is
        # recognised as "no snapshot available" and the sync
        # succeeds (with zero snapshot rows for the back-end
        # share).
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            store = fund_data.FundDataStore(db_path)
            for code in ("110022", "000002", "000012", "000108"):
                store.upsert_funds([
                    {
                        "fund_code": code,
                        "fund_name": code,
                        "fund_type": "股票型",
                        "company": "",
                        "manager": "",
                        "nav": None,
                        "nav_date": "",
                        "other_names": "",
                        "source": "test",
                    }
                ])

            result = fund_data.batch_sync_funds(
                ["110022", "000002", "000012", "000108"],
                db_path=db_path,
                provider="eastmoney",
            )

            self.assertEqual(result["failed"], 0)
            self.assertEqual(result["ok"], 4)
            with closing(sqlite3.connect(db_path)) as con:
                fails = list(
                    con.execute(
                        "SELECT fund_code, message FROM sync_failures"
                    ).fetchall()
                )
            self.assertEqual(fails, [])

    def test_store_upserts_funds_nav_snapshot_and_raw_responses(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            store = fund_data.FundDataStore(db_path)

            fund_rows = fund_data.parse_search_results(SEARCH_PAYLOAD)
            nav_rows = fund_data.parse_nav_history(NAV_PAYLOAD)
            snapshot = fund_data.parse_snapshot(SNAPSHOT_PAYLOAD)

            self.assertEqual(store.upsert_funds(fund_rows), 1)
            self.assertEqual(store.upsert_nav_history("110022", nav_rows), 2)
            self.assertEqual(store.upsert_snapshot(snapshot), 1)
            store.record_raw_response("eastmoney.search", "沪深300", SEARCH_PAYLOAD)

            with closing(sqlite3.connect(db_path)) as conn:
                fund_count = conn.execute("select count(*) from funds").fetchone()[0]
                nav_count = conn.execute("select count(*) from nav_history").fetchone()[0]
                raw_count = conn.execute("select count(*) from raw_responses").fetchone()[0]
                stock_codes_json = conn.execute(
                    "select stock_codes_json from snapshots where fund_code = '110022'"
                ).fetchone()[0]

            self.assertEqual(fund_count, 1)
            self.assertEqual(nav_count, 2)
            self.assertEqual(raw_count, 1)
            self.assertEqual(json.loads(stock_codes_json), ["1.600519", "0.000333"])

    def test_fetch_nav_history_reads_fresh_local_rows_before_provider_chain(self):
        class ProviderShouldNotRun:
            name = "network"

            def nav_history(self, code, **kwargs):
                raise AssertionError("provider chain should not run on a fresh local NAV hit")

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            store = fund_data.FundDataStore(db_path)
            store.upsert_nav_history("110022", fund_data.parse_nav_history(NAV_PAYLOAD))

            with patch.object(fund_data, "build_providers", return_value=[ProviderShouldNotRun()]):
                rows = fund_data.fetch_nav_history(
                    "110022",
                    start_date="2024-01-30",
                    end_date="2024-01-31",
                    per=2,
                    db_path=db_path,
                )

            self.assertEqual([row["nav_date"] for row in rows], ["2024-01-31", "2024-01-30"])
            self.assertNotIn("fetched_at", rows[0])

    def test_fetch_nav_history_refreshes_when_local_rows_are_missing(self):
        class RefreshProvider:
            name = "refresh"

            def __init__(self):
                self.calls = 0

            def nav_history(self, code, **kwargs):
                self.calls += 1
                return [
                    {
                        "nav_date": "2024-01-31",
                        "unit_nav": 4.0,
                        "accumulated_nav": 4.1,
                        "daily_growth_rate": 0.01,
                        "source": "refresh.nav",
                    }
                ]

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            provider = RefreshProvider()

            with patch.object(fund_data, "build_providers", return_value=[provider]):
                rows = fund_data.fetch_nav_history(
                    "110022",
                    start_date="2024-01-31",
                    end_date="2024-01-31",
                    per=1,
                    db_path=db_path,
                )

            self.assertEqual(provider.calls, 1)
            self.assertEqual(rows[0]["unit_nav"], 4.0)
            self.assertEqual(
                fund_data.FundDataStore(db_path).select_nav_history("110022", per=1)[0]["source"],
                "refresh.nav",
            )

    def test_fetch_nav_history_refreshes_when_local_rows_are_stale(self):
        class RefreshProvider:
            name = "refresh"

            def __init__(self):
                self.calls = 0

            def nav_history(self, code, **kwargs):
                self.calls += 1
                return [
                    {
                        "nav_date": "2024-01-31",
                        "unit_nav": 4.0,
                        "accumulated_nav": 4.1,
                        "daily_growth_rate": 0.01,
                        "source": "refresh.nav",
                    }
                ]

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            store = fund_data.FundDataStore(db_path)
            store.upsert_nav_history(
                "110022",
                [
                    {
                        "nav_date": "2024-01-31",
                        "unit_nav": 1.0,
                        "accumulated_nav": 1.1,
                        "daily_growth_rate": 0.001,
                        "source": "local.nav",
                    }
                ],
            )
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    "update nav_history set fetched_at = ? where fund_code = ?",
                    ("2000-01-01T00:00:00+00:00", "110022"),
                )
                conn.commit()

            provider = RefreshProvider()
            with patch.object(fund_data, "build_providers", return_value=[provider]):
                rows = fund_data.fetch_nav_history(
                    "110022",
                    start_date="2024-01-31",
                    end_date="2024-01-31",
                    per=1,
                    db_path=db_path,
                )

            self.assertEqual(provider.calls, 1)
            self.assertEqual(rows[0]["unit_nav"], 4.0)
            self.assertEqual(
                fund_data.FundDataStore(db_path).select_nav_history("110022", per=1)[0]["source"],
                "refresh.nav",
            )

    def test_store_upserts_stock_holdings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = fund_data.FundDataStore(Path(tmpdir) / "fund_data.sqlite")

            count = store.upsert_stock_holdings(
                "110022",
                [
                    {
                        "report_period": "2024年4季度股票投资明细",
                        "stock_code": "600519",
                        "stock_name": "贵州茅台",
                        "net_value_ratio": 0.0983,
                        "shares": 12.34,
                        "market_value": 56789.0,
                        "source": "akshare.fund_portfolio_hold_em",
                    }
                ],
            )

            rows = store.export_table("stock_holdings", fund_code="110022")
            self.assertEqual(count, 1)
            self.assertEqual(rows[0]["stock_code"], "600519")
            self.assertAlmostEqual(rows[0]["net_value_ratio"], 0.0983)

    def test_store_upserts_new_fund_domain_tables_and_coverage(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = fund_data.FundDataStore(Path(tmpdir) / "fund_data.sqlite")
            store.upsert_funds(fund_data.parse_search_results(SEARCH_PAYLOAD))
            store.upsert_profile(
                {
                    "fund_code": "006600",
                    "fund_name": "人保沪深300A",
                    "full_name": "人保沪深300指数型证券投资基金",
                    "fund_type": "指数型-股票",
                    "fund_company": "人保资产",
                    "custodian": "托管行",
                    "manager": "周剑",
                    "benchmark": "沪深300指数收益率",
                    "asset_size": 12.34,
                    "source": "akshare.fund_overview_em",
                }
            )
            store.upsert_bond_holdings(
                "006600",
                [
                    {
                        "report_period": "2024Q4",
                        "bond_code": "127045",
                        "bond_name": "牧原转债",
                        "net_value_ratio": 0.0031,
                        "market_value": 1234.56,
                        "source": "akshare.fund_portfolio_bond_hold_em",
                    }
                ],
            )
            store.upsert_industry_allocations(
                "006600",
                [
                    {
                        "report_period": "2024Q4",
                        "industry_name": "制造业",
                        "net_value_ratio": 0.8321,
                        "market_value": 12345.67,
                        "source": "akshare.fund_portfolio_industry_allocation_em",
                    }
                ],
            )
            store.upsert_fee_structures(
                "006600",
                [
                    {
                        "fee_type": "申购费率",
                        "condition_name": "小于100万元",
                        "fee": 0.0015,
                        "fee_text": "0.15%",
                        "discount_fee": 0.00015,
                        "discount_fee_text": "0.015%",
                        "source": "akshare.fund_fee_em",
                    }
                ],
            )
            store.upsert_dividends(
                "006600",
                [
                    {
                        "dividend_date": "2021-01-15",
                        "ex_dividend_date": "2021-01-15",
                        "dividend_per_share": 0.1,
                        "payment_date": "2021-01-19",
                        "source": "akshare.fund_fh_em",
                    }
                ],
            )
            store.upsert_splits(
                "006600",
                [
                    {
                        "split_date": "2019-01-10",
                        "split_type": "份额折算",
                        "split_ratio": 1.5,
                        "source": "akshare.fund_cf_em",
                    }
                ],
            )
            store.upsert_fund_managers(
                [
                    {
                        "manager_name": "周剑",
                        "company": "人保资产",
                        "current_fund_codes": "006600",
                        "current_funds": "人保沪深300A",
                        "tenure_days": 1000,
                        "current_aum": 12.34,
                        "best_return": 0.2,
                        "source": "akshare.fund_manager_em",
                    }
                ]
            )

            coverage = store.coverage_rows(fund_code="006600")

            self.assertEqual(
                store.export_table("fund_profiles", fund_code="006600")[0]["manager"], "周剑"
            )
            self.assertEqual(
                store.export_table("bond_holdings", fund_code="006600")[0]["bond_code"], "127045"
            )
            self.assertEqual(
                store.export_table("industry_allocations", fund_code="006600")[0]["industry_name"],
                "制造业",
            )
            self.assertEqual(
                store.export_table("industry_allocations", fund_code="006600")[0]["market_value"],
                12345.67,
            )
            self.assertEqual(
                store.export_table("fee_structures", fund_code="006600")[0]["fee_type"], "申购费率"
            )
            self.assertEqual(
                store.export_table("fee_structures", fund_code="006600")[0]["fee_text"], "0.15%"
            )
            self.assertEqual(
                store.export_table("dividends", fund_code="006600")[0]["dividend_date"],
                "2021-01-15",
            )
            self.assertEqual(
                store.export_table("splits", fund_code="006600")[0]["split_date"], "2019-01-10"
            )
            self.assertEqual(store.export_table("fund_managers")[0]["manager_name"], "周剑")
            self.assertEqual(coverage[0]["has_profile"], 1)
            self.assertEqual(coverage[0]["bond_holding_rows"], 1)

    def test_export_table_returns_dict_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = fund_data.FundDataStore(Path(tmpdir) / "fund_data.sqlite")
            store.upsert_funds(fund_data.parse_search_results(SEARCH_PAYLOAD))

            rows = store.export_table("funds")

            self.assertEqual(rows[0]["fund_code"], "006600")
            self.assertEqual(rows[0]["fund_name"], "人保沪深300A")

    def test_fetch_fund_list_from_offline_raw_persists_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rows = fund_data.fetch_fund_list(
                db_path=Path(tmpdir) / "fund_data.sqlite",
                raw_text=FUND_CODE_LIST_PAYLOAD,
            )

            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1]["fund_code"], "110022")

    def test_sync_include_all_persists_full_fund_base_and_coverage(self):
        class FullSyncProvider:
            name = "fake"

            def snapshot(self, code):
                return {
                    "fund_code": "110022",
                    "fund_name": "易方达消费行业股票",
                    "source_rate": 1.5,
                    "current_rate": 0.15,
                    "min_purchase": 10,
                    "stock_codes": ["1.600519"],
                    "returns": {"one_year": -0.1703},
                    "source": "fake.snapshot",
                }

            def nav_history(self, code, **kwargs):
                return [
                    {
                        "nav_date": "2024-01-31",
                        "unit_nav": 3.133,
                        "accumulated_nav": 3.133,
                        "daily_growth_rate": -0.0132,
                        "source": "fake.nav",
                    },
                    {
                        "nav_date": "2024-01-30",
                        "unit_nav": 3.175,
                        "accumulated_nav": 3.175,
                        "daily_growth_rate": -0.0234,
                        "source": "fake.nav",
                    },
                ]

            def profile(self, code):
                return {
                    "fund_code": "110022",
                    "fund_name": "易方达消费行业股票",
                    "full_name": "易方达消费行业股票型证券投资基金",
                    "fund_type": "股票型",
                    "fund_company": "易方达基金",
                    "manager": "萧楠",
                    "source": "fake.profile",
                }

            def stock_holdings(self, code, report_year=None):
                return [
                    {
                        "report_period": "2024Q4",
                        "stock_code": "600519",
                        "stock_name": "贵州茅台",
                        "net_value_ratio": 0.0983,
                        "source": "fake.stock_holdings",
                    }
                ]

            def bond_holdings(self, code, report_year=None):
                return [
                    {
                        "report_period": "2024Q4",
                        "bond_code": "127045",
                        "bond_name": "牧原转债",
                        "net_value_ratio": 0.0031,
                        "source": "fake.bond_holdings",
                    }
                ]

            def industry_allocations(self, code, report_year=None):
                return [
                    {
                        "report_period": "2024-12-31",
                        "industry_name": "制造业",
                        "net_value_ratio": 0.8695,
                        "source": "fake.industry_allocations",
                    }
                ]

            def fee_structures(self, code, indicators=None):
                return [
                    {
                        "fee_type": "申购费率",
                        "condition_name": "小于100万元",
                        "fee": 0.015,
                        "fee_text": "1.50%",
                        "source": "fake.fee_structures",
                    }
                ]

            def dividends(self, code):
                return [
                    {
                        "dividend_date": "2021-01-15",
                        "ex_dividend_date": "2021-01-15",
                        "dividend_per_share": 0.1,
                        "payment_date": "2021-01-19",
                        "source": "fake.dividends",
                    }
                ]

            def splits(self, code):
                return [
                    {
                        "split_date": "2019-01-10",
                        "split_type": "份额折算",
                        "split_ratio": 1.5,
                        "source": "fake.splits",
                    }
                ]

            def fund_managers(self, code=None):
                return [
                    {
                        "manager_name": "萧楠",
                        "company": "易方达基金",
                        "current_fund_codes": "110022",
                        "current_funds": "易方达消费行业股票",
                        "tenure_days": 4000,
                        "source": "fake.fund_managers",
                    }
                ]

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            original_build_providers = fund_data.build_providers
            fund_data.build_providers = lambda provider, capability=None: [FullSyncProvider()]
            try:
                result = fund_data.sync_fund(
                    "110022",
                    db_path=db_path,
                    provider="auto",
                    include_all=True,
                    report_year="2024",
                    fee_indicators=["申购费率"],
                )
            finally:
                fund_data.build_providers = original_build_providers

            store = fund_data.FundDataStore(db_path)
            coverage = store.coverage_rows(fund_code="110022")[0]

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["fund_rows"], 1)
            self.assertEqual(result["snapshot_rows"], 1)
            self.assertEqual(result["nav_rows"], 2)
            self.assertEqual(result["profile_rows"], 1)
            self.assertEqual(result["stock_holding_rows"], 1)
            self.assertEqual(result["bond_holding_rows"], 1)
            self.assertEqual(result["industry_rows"], 1)
            self.assertEqual(result["fee_rows"], 1)
            self.assertEqual(result["dividend_rows"], 1)
            self.assertEqual(result["split_rows"], 1)
            self.assertEqual(result["manager_rows"], 1)
            self.assertEqual(coverage["has_profile"], 1)
            self.assertEqual(coverage["stock_holding_rows"], 1)
            self.assertEqual(coverage["bond_holding_rows"], 1)
            self.assertEqual(coverage["industry_rows"], 1)
            self.assertEqual(coverage["fee_rows"], 1)
            self.assertEqual(coverage["dividend_rows"], 1)
            self.assertEqual(coverage["split_rows"], 1)

    def test_sync_include_all_keeps_optional_dataset_failures_nonfatal(self):
        class SparseProvider:
            name = "sparse"

            def snapshot(self, code):
                return {
                    "fund_code": "000015",
                    "fund_name": "华夏纯债债券A",
                    "stock_codes": [],
                    "returns": {},
                    "source": "sparse.snapshot",
                }

            def nav_history(self, code, **kwargs):
                return [
                    {
                        "nav_date": "2024-01-31",
                        "unit_nav": 1.0,
                        "accumulated_nav": 1.1,
                        "daily_growth_rate": 0.001,
                        "source": "sparse.nav",
                    }
                ]

            def profile(self, code):
                return {
                    "fund_code": "000015",
                    "fund_name": "华夏纯债债券A",
                    "fund_type": "债券型-长债",
                    "source": "sparse.profile",
                }

            def stock_holdings(self, code, report_year=None):
                raise fund_data.ProviderError("provider returned no rows")

            def bond_holdings(self, code, report_year=None):
                raise KeyError("占净值比例")

            def industry_allocations(self, code, report_year=None):
                return []

            def fee_structures(self, code, indicators=None):
                raise RuntimeError("fee page unavailable")

            def dividends(self, code):
                return []

            def splits(self, code):
                return []

            def fund_managers(self, code=None):
                raise RuntimeError("manager endpoint unavailable")

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            original_build_providers = fund_data.build_providers
            fund_data.build_providers = lambda provider, capability=None: [SparseProvider()]
            try:
                result = fund_data.sync_fund(
                    "000015",
                    db_path=db_path,
                    provider="auto",
                    include_all=True,
                    report_year="2024",
                )
            finally:
                fund_data.build_providers = original_build_providers

            coverage = fund_data.FundDataStore(db_path).coverage_rows(fund_code="000015")[0]

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["fund_code"], "000015")
            self.assertEqual(result["nav_rows"], 1)
            self.assertEqual(result["stock_holding_rows"], 0)
            self.assertEqual(result["bond_holding_rows"], 0)
            self.assertEqual(result["fee_rows"], 0)
            self.assertEqual(result["manager_rows"], 0)
            self.assertGreaterEqual(len(result["dataset_errors"]), 4)
            self.assertEqual(coverage["has_profile"], 1)
            self.assertEqual(coverage["nav_rows"], 1)

    def test_batch_sync_continues_after_failure_and_records_failure_queue(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            original_sync_fund = fund_data.sync_fund

            def fake_sync_fund(code, **kwargs):
                if code == "000001":
                    raise RuntimeError("temporary provider failure")
                store = fund_data.FundDataStore(kwargs["db_path"])
                store.upsert_funds(
                    [
                        {
                            "fund_code": code,
                            "fund_name": f"基金{code}",
                            "source": "fake.batch",
                        }
                    ]
                )
                return {"fund_code": code, "status": "ok", "rows_changed": 1}

            fund_data.sync_fund = fake_sync_fund
            try:
                result = fund_data.batch_sync_funds(
                    ["110022", "000001", "006600"],
                    db_path=db_path,
                    provider="auto",
                    include_all=True,
                    report_year="2024",
                    batch_id="test-batch",
                )
            finally:
                fund_data.sync_fund = original_sync_fund

            store = fund_data.FundDataStore(db_path)
            failures = store.export_table("sync_failures")

            self.assertEqual(result["batch_id"], "test-batch")
            self.assertEqual(result["total"], 3)
            self.assertEqual(result["ok"], 2)
            self.assertEqual(result["failed"], 1)
            self.assertEqual(result["results"][1]["fund_code"], "000001")
            self.assertEqual(result["results"][1]["status"], "error")
            self.assertIn("temporary provider failure", result["results"][1]["message"])
            self.assertEqual(len(result["coverage"]), 2)
            self.assertEqual(failures[0]["batch_id"], "test-batch")
            self.assertEqual(failures[0]["fund_code"], "000001")
            self.assertIn("temporary provider failure", failures[0]["message"])

    def test_batch_sync_runs_in_parallel_with_concurrency(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            original_sync_fund = fund_data.sync_fund
            concurrent_invocations: list[float] = []
            serialization_lock = threading.Lock()

            def fake_sync_fund(code, **kwargs):
                with serialization_lock:
                    concurrent_invocations.append(time.monotonic())
                time.sleep(0.1)
                store = fund_data.FundDataStore(kwargs["db_path"])
                store.upsert_funds(
                    [
                        {
                            "fund_code": code,
                            "fund_name": f"基金{code}",
                            "source": "fake.concurrent",
                        }
                    ]
                )
                return {"fund_code": code, "status": "ok", "rows_changed": 1}

            fund_data.sync_fund = fake_sync_fund
            try:
                started = time.monotonic()
                result = fund_data.batch_sync_funds(
                    ["110022", "000001", "006600", "510300"],
                    db_path=db_path,
                    provider="auto",
                    concurrency=4,
                    batch_id="parallel-batch",
                )
                elapsed = time.monotonic() - started
            finally:
                fund_data.sync_fund = original_sync_fund

            self.assertEqual(result["total"], 4)
            self.assertEqual(result["ok"], 4)
            self.assertEqual(result["failed"], 0)
            self.assertEqual(result["concurrency"], 4)
            self.assertEqual(result["min_interval_seconds"], 0.25)
            self.assertEqual(
                {r["fund_code"] for r in result["results"]},
                {"110022", "000001", "006600", "510300"},
            )
            self.assertLess(elapsed, 0.5, f"expected concurrent execution, got {elapsed:.2f}s")
            deltas = sorted(
                t2 - t1
                for t1, t2 in zip(concurrent_invocations, concurrent_invocations[1:], strict=False)
            )
            self.assertGreater(
                sum(1 for d in deltas if d < 0.05),
                0,
                "expected at least one near-simultaneous invocation, got deltas: " + str(deltas),
            )

    def test_batch_sync_default_min_interval_is_one_second_sequential(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            original_sync_fund = fund_data.sync_fund

            def fake_sync_fund(code, **kwargs):
                store = fund_data.FundDataStore(kwargs["db_path"])
                store.upsert_funds(
                    [{"fund_code": code, "fund_name": f"基金{code}", "source": "fake.seq"}]
                )
                return {"fund_code": code, "status": "ok", "rows_changed": 1}

            fund_data.sync_fund = fake_sync_fund
            try:
                result = fund_data.batch_sync_funds(
                    ["110022", "000001"],
                    db_path=db_path,
                    provider="auto",
                    batch_id="seq-batch",
                )
            finally:
                fund_data.sync_fund = original_sync_fund

            self.assertEqual(result["concurrency"], 1)
            self.assertEqual(result["min_interval_seconds"], 1.0)

    def test_coverage_report_returns_completeness_and_missing_datasets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            store = fund_data.FundDataStore(db_path)
            store.upsert_funds(
                [
                    {
                        "fund_code": "110022",
                        "fund_name": "基金A",
                        "fund_type": "股票型",
                        "source": "t",
                    },
                    {
                        "fund_code": "000001",
                        "fund_name": "基金B",
                        "fund_type": "债券型",
                        "source": "t",
                    },
                ]
            )
            store.upsert_profile({"fund_code": "110022", "fund_name": "基金A", "fund_company": "X"})
            store.upsert_nav_history("110022", [{"nav_date": "2024-01-31", "unit_nav": 1.0}])
            store.upsert_nav_history("000001", [{"nav_date": "2024-01-31", "unit_nav": 1.0}])

            report = fund_data.coverage_report(db_path=db_path)

            by_code = {row["fund_code"]: row for row in report}
            self.assertEqual(by_code["110022"]["completeness"], 0.25)
            self.assertEqual(
                sorted(by_code["110022"]["missing"]),
                sorted(
                    ["stock_holdings", "bond_holdings", "industry", "fees", "dividends", "splits"]
                ),
            )
            self.assertEqual(by_code["000001"]["completeness"], 0.125)
            self.assertIn("profile", by_code["000001"]["missing"])

    def test_coverage_report_filters_by_fund_type_and_only_incomplete(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            store = fund_data.FundDataStore(db_path)
            store.upsert_funds(
                [
                    {"fund_code": "110022", "fund_name": "A", "fund_type": "股票型", "source": "t"},
                    {
                        "fund_code": "000015",
                        "fund_name": "B",
                        "fund_type": "债券型-纯债",
                        "source": "t",
                    },
                ]
            )
            report = fund_data.coverage_report(db_path=db_path, fund_type="债券")
            self.assertEqual([r["fund_code"] for r in report], ["000015"])

            incomplete = fund_data.coverage_report(db_path=db_path, only_incomplete=True)
            self.assertEqual({r["fund_code"] for r in incomplete}, {"110022", "000015"})

    def test_upsert_fund_managers_fans_out_to_fund_manager_links(self):
        """``upsert_fund_managers`` writes both the legacy
        manager-centric row and the new fund-centric projection so
        ``fund_manager_links`` is hot for the O(1) reverse query
        without a separate backfill step. The legacy
        ``fund_managers`` table must keep working unchanged -- it
        is the natural shape for "list every fund this manager
        runs" and several consumers still read it.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            store = fund_data.FundDataStore(Path(tmpdir) / "fund_data.sqlite")
            store.upsert_fund_managers(
                [
                    {
                        "manager_name": "萧楠",
                        "company": "易方达基金",
                        "current_fund_codes": "110022",
                        "current_funds": "易方达消费行业股票",
                        "tenure_days": 4994,
                        "current_aum": 225.82,
                        "best_return": 2.7587,
                        "source": "akshare.fund_manager_em",
                    },
                    {
                        "manager_name": "刘睿聪",
                        "company": "华夏基金",
                        "current_fund_codes": "000001",
                        "current_funds": "华夏成长混合",
                        "tenure_days": 1251,
                        "current_aum": 26.61,
                        "best_return": 0.5827,
                        "source": "akshare.fund_manager_em",
                    },
                    # Empty code must NOT pollute the link table --
                    # otherwise a `fund_code = ''` agent query would
                    # accidentally match every malformed row.
                    {
                        "manager_name": "test",
                        "company": "test",
                        "current_fund_codes": "",
                        "current_funds": "",
                        "tenure_days": 0,
                        "current_aum": 0,
                        "best_return": 0,
                        "source": "fake",
                    },
                ]
            )

            # Legacy manager-centric table still works (the new
            # ``upsert_fund_managers`` fan-out is additive -- the
            # original row shape is preserved so existing
            # consumers reading the legacy table see no change).
            legacy = store.export_table("fund_managers")
            self.assertEqual(len(legacy), 3)
            self.assertEqual(
                {r["manager_name"] for r in legacy}, {"萧楠", "刘睿聪", "test"}
            )

            # New fund-centric projection is hot and queryable.
            links = store.export_table("fund_manager_links", fund_code="110022")
            self.assertEqual(len(links), 1)
            self.assertEqual(links[0]["manager_name"], "萧楠")
            self.assertEqual(links[0]["company"], "易方达基金")
            self.assertEqual(links[0]["current_funds"], "易方达消费行业股票")
            # Empty-code row was dropped -- assert no blank code
            # leaked into the projection.
            all_codes = {
                r["fund_code"]
                for r in store.export_table("fund_manager_links")
            }
            self.assertNotIn("", all_codes)
            self.assertEqual(all_codes, {"110022", "000001"})

    def test_fund_manager_links_migration_backfills_from_legacy(self):
        """Migration 6 must create ``fund_manager_links`` and
        backfill from existing ``fund_managers`` rows so the
        reverse query is hot on first open (and not gated on a
        re-fetch)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Path(tmpdir) / "fund_data.sqlite"
            # Bootstrap with the bootstrap-create path (which
            # creates fund_managers but not fund_manager_links on
            # a fresh DB), insert a legacy row directly, then
            # simulate "migration hasn't run yet" by dropping the
            # new table if it exists.
            store = fund_data.FundDataStore(str(db))
            with sqlite3.connect(str(db)) as conn:
                conn.execute(
                    """
                    insert into fund_managers (
                        manager_name, company, current_fund_codes, current_funds,
                        tenure_days, current_aum, best_return, source, fetched_at
                    ) values ('萧楠', '易方达基金', '110022', '易方达消费行业股票',
                              4994, 225.82, 2.7587, 'akshare', '2026-06-01T17:16:21+00:00')
                    """
                )
                conn.execute("drop table if exists fund_manager_links")
                # Rewind user_version so the migration will re-run
                # on next open.
                conn.execute("PRAGMA user_version = 5")

            # Re-open -- should apply migration 6 and backfill.
            fund_data.FundDataStore(str(db)).ensure_schema()
            with sqlite3.connect(str(db)) as conn:
                rows = conn.execute(
                    "select * from fund_manager_links where fund_code = ?",
                    ("110022",),
                ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][1], "萧楠")  # manager_name column
            self.assertEqual(rows[0][2], "易方达基金")  # company

    def test_fund_manager_links_o1_lookup_vs_legacy_scan(self):
        """The whole point of the projection: an O(1) index hit
        on ``fund_code`` rather than a full table scan with
        ``LIKE '%<code>%'``. Verified with EXPLAIN QUERY PLAN so
        the test fails loudly if a future schema change
        accidentally re-introduces the SCAN path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = fund_data.FundDataStore(Path(tmpdir) / "fund_data.sqlite")
            store.upsert_fund_managers(
                [
                    {
                        "manager_name": f"mgr_{i}",
                        "company": f"co_{i}",
                        "current_fund_codes": "110022" if i == 0 else f"99999{i:02d}",
                        "current_funds": f"fund_{i}",
                        "tenure_days": 1,
                        "current_aum": 1.0,
                        "best_return": 0.1,
                        "source": "fake",
                    }
                    for i in range(5)
                ]
            )
            db_path = str(Path(tmpdir) / "fund_data.sqlite")
            with sqlite3.connect(db_path) as conn:
                plan = conn.execute(
                    "EXPLAIN QUERY PLAN select * from fund_manager_links where fund_code = ?",
                    ("110022",),
                ).fetchall()
            joined = " ".join(row[3] for row in plan)
            self.assertIn("USING INDEX", joined, f"expected index scan, got plan: {plan}")


class SchemaMigrationTests(unittest.TestCase):
    """``FundDataStore.ensure_schema`` runs a registered migration
    list and records every applied version in ``schema_migrations``
    plus ``PRAGMA user_version``. Old DBs (pre-registry) upgrade
    transparently; fresh DBs apply every migration once on first
    open; the whole flow is idempotent.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.sqlite"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_ensure_schema_is_idempotent(self) -> None:
        """Running ``ensure_schema`` twice must not re-apply
        migrations and must not bump ``user_version``."""
        fund_data.FundDataStore(str(self.db)).ensure_schema()
        with sqlite3.connect(str(self.db)) as conn:
            first_version = conn.execute("PRAGMA user_version").fetchone()[0]
            first_rows = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]

        fund_data.FundDataStore(str(self.db)).ensure_schema()
        with sqlite3.connect(str(self.db)) as conn:
            second_version = conn.execute("PRAGMA user_version").fetchone()[0]
            second_rows = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]

        self.assertEqual(first_version, fund_data.FUND_DATA_SCHEMA_VERSION)
        self.assertEqual(first_version, second_version)
        self.assertEqual(first_rows, second_rows)
        self.assertEqual(first_rows, fund_data.FUND_DATA_SCHEMA_VERSION)

    def test_migrations_skip_already_applied(self) -> None:
        """A v0.2-shaped DB (user_version=2) must skip the first
        two migrations and only run the remaining ones."""
        # Bootstrap: build the schema, then rewind user_version to
        # simulate an "old" DB that already had migrations 1 and 2
        # applied.
        fund_data.FundDataStore(str(self.db)).ensure_schema()
        with sqlite3.connect(str(self.db)) as conn:
            # Delete the audit log for migrations 3+ and rewind
            # user_version. This mimics an out-of-band database
            # that was upgraded by the old ``_ensure_column`` calls
            # but never recorded into the registry.
            conn.execute("DELETE FROM schema_migrations WHERE version > 2")
            conn.execute("PRAGMA user_version = 2")

        fund_data.FundDataStore(str(self.db)).ensure_schema()
        with sqlite3.connect(str(self.db)) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            rows = sorted(r[0] for r in conn.execute("SELECT version FROM schema_migrations"))
        self.assertEqual(version, fund_data.FUND_DATA_SCHEMA_VERSION)
        self.assertEqual(rows, list(range(1, fund_data.FUND_DATA_SCHEMA_VERSION + 1)))

    def test_fresh_db_applies_every_migration_once(self) -> None:
        """A brand-new DB (no user_version) should end up with
        user_version == FUND_DATA_SCHEMA_VERSION and a row in
        schema_migrations for every migration."""
        fund_data.FundDataStore(str(self.db)).ensure_schema()
        with sqlite3.connect(str(self.db)) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            rows = sorted(r[0] for r in conn.execute("SELECT version FROM schema_migrations"))
        self.assertEqual(version, fund_data.FUND_DATA_SCHEMA_VERSION)
        self.assertEqual(rows, list(range(1, fund_data.FUND_DATA_SCHEMA_VERSION + 1)))
        # Every column the four migrations add must exist now.
        with sqlite3.connect(str(self.db)) as conn:
            for table, column in [
                ("industry_allocations", "market_value"),
                ("fee_structures", "fee_text"),
                ("fee_structures", "discount_fee"),
                ("fee_structures", "discount_fee_text"),
            ]:
                cols = {row[1] for row in conn.execute(f"pragma table_info({table})")}
                self.assertIn(column, cols, f"{table}.{column} missing after migration")

    def test_fresh_db_has_canonical_column_order(self) -> None:
        """After v5, a brand-new DB's industry_allocations and
        fee_structures tables must declare the migration-added
        columns at the END of the column list (matching what
        ALTER TABLE produces on an upgraded DB). Otherwise
        the next schema-drift incident is just a code change
        away."""
        fund_data.FundDataStore(str(self.db)).ensure_schema()
        with sqlite3.connect(str(self.db)) as conn:
            industry_cols = [
                row[1] for row in conn.execute("PRAGMA table_info(industry_allocations)")
            ]
            fee_cols = [
                row[1] for row in conn.execute("PRAGMA table_info(fee_structures)")
            ]
        self.assertEqual(
            industry_cols,
            [
                "fund_code", "report_period", "industry_name",
                "net_value_ratio", "source", "fetched_at", "market_value",
            ],
        )
        self.assertEqual(
            fee_cols,
            [
                "fund_code", "fee_type", "condition_name",
                "fee", "source", "fetched_at",
                "fee_text", "discount_fee", "discount_fee_text",
            ],
        )

    def test_v5_migration_reorders_drifted_columns_preserving_data(self) -> None:
        """Regression guard for the 2026-06-02 schema-drift incident:
        a v4 DB whose ``industry_allocations.market_value`` sits at
        column position 5 (mid-table) and ``fee_structures`` whose
        fee_text / discount_fee / discount_fee_text also sit
        mid-table must be re-ordered by v5 into the canonical
        order, preserving every row's values."""
        # Build a v4-shaped DB: run ensure_schema, then drop the
        # v5 migration entry and rewind user_version to 4 so v5
        # will run on next open. Then manually recreate the
        # mid-table column order that the pre-fix schema produced.
        fund_data.FundDataStore(str(self.db)).ensure_schema()
        with sqlite3.connect(str(self.db)) as conn:
            # Delete the v5 audit row (if any) and rewind.
            conn.execute("DELETE FROM schema_migrations WHERE version >= 5")
            conn.execute("PRAGMA user_version = 4")
            # Recreate the two tables in the OLD order (market_value
            # mid-table for industry, fee_text / discount_fee /
            # discount_fee_text mid-table for fee). We re-insert
            # the rows so we can assert v5 preserves them.
            conn.executescript("""
                ALTER TABLE industry_allocations RENAME TO industry_allocations__old;
                CREATE TABLE industry_allocations (
                    fund_code text not null,
                    report_period text not null,
                    industry_name text not null,
                    net_value_ratio real,
                    market_value real,
                    source text,
                    fetched_at text not null,
                    primary key (fund_code, report_period, industry_name)
                );
                INSERT INTO industry_allocations
                    SELECT fund_code, report_period, industry_name,
                           net_value_ratio, market_value, source, fetched_at
                    FROM industry_allocations__old;
                DROP TABLE industry_allocations__old;

                ALTER TABLE fee_structures RENAME TO fee_structures__old;
                CREATE TABLE fee_structures (
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
                INSERT INTO fee_structures
                    SELECT fund_code, fee_type, condition_name, fee,
                           fee_text, discount_fee, discount_fee_text,
                           source, fetched_at
                    FROM fee_structures__old;
                DROP TABLE fee_structures__old;
            """)
            # Seed a row we can verify survives the rebuild.
            conn.execute(
                """INSERT INTO industry_allocations
                       (fund_code, report_period, industry_name,
                        net_value_ratio, market_value, source, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    "110022", "2024Q4", "制造业",
                    0.8321, 12345.67, "v4-drift", "2025-01-01T00:00:00+00:00",
                ),
            )
            conn.execute(
                """INSERT INTO fee_structures
                       (fund_code, fee_type, condition_name, fee,
                        fee_text, discount_fee, discount_fee_text,
                        source, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "110022", "申购费率", "小于100万元", 0.15,
                    "0.15%", 0.10, "0.10%",
                    "v4-drift", "2025-01-01T00:00:00+00:00",
                ),
            )
            conn.commit()

        # Open the DB. ensure_schema must run v5 and reorder.
        fund_data.FundDataStore(str(self.db)).ensure_schema()

        with sqlite3.connect(str(self.db)) as conn:
            industry_cols = [
                row[1] for row in conn.execute("PRAGMA table_info(industry_allocations)")
            ]
            fee_cols = [
                row[1] for row in conn.execute("PRAGMA table_info(fee_structures)")
            ]
            self.assertEqual(
                industry_cols,
                [
                    "fund_code", "report_period", "industry_name",
                    "net_value_ratio", "source", "fetched_at", "market_value",
                ],
            )
            self.assertEqual(
                fee_cols,
                [
                    "fund_code", "fee_type", "condition_name",
                    "fee", "source", "fetched_at",
                    "fee_text", "discount_fee", "discount_fee_text",
                ],
            )
            # The seeded rows survived the rebuild.
            industry_row = conn.execute(
                """SELECT fund_code, report_period, industry_name,
                          net_value_ratio, market_value, source
                   FROM industry_allocations WHERE fund_code = ?""",
                ("110022",),
            ).fetchone()
            self.assertEqual(industry_row[0], "110022")
            self.assertEqual(industry_row[1], "2024Q4")
            self.assertEqual(industry_row[2], "制造业")
            self.assertAlmostEqual(industry_row[3], 0.8321)
            self.assertAlmostEqual(industry_row[4], 12345.67)
            self.assertEqual(industry_row[5], "v4-drift")

            fee_row = conn.execute(
                """SELECT fund_code, fee_type, condition_name, fee,
                          fee_text, discount_fee, discount_fee_text, source
                   FROM fee_structures WHERE fund_code = ?""",
                ("110022",),
            ).fetchone()
            self.assertEqual(fee_row[0], "110022")
            self.assertEqual(fee_row[1], "申购费率")
            self.assertEqual(fee_row[2], "小于100万元")
            self.assertAlmostEqual(fee_row[3], 0.15)
            self.assertEqual(fee_row[4], "0.15%")
            self.assertAlmostEqual(fee_row[5], 0.10)
            self.assertEqual(fee_row[6], "0.10%")
            self.assertEqual(fee_row[7], "v4-drift")

    def test_v5_migration_refuses_when_column_set_differs(self) -> None:
        """Defensive: if a DB's column set for one of the affected
        tables has drifted beyond reordering (a column was added
        or removed out of band), v5 must raise rather than
        silently dropping/adding columns. Otherwise a half-broken
        migration could be worse than the original schema drift."""
        fund_data.FundDataStore(str(self.db)).ensure_schema()
        with sqlite3.connect(str(self.db)) as conn:
            conn.execute("DELETE FROM schema_migrations WHERE version >= 5")
            conn.execute("PRAGMA user_version = 4")
            # Add a column that the canonical schema does not have.
            conn.execute("ALTER TABLE industry_allocations ADD COLUMN rogue TEXT")
            conn.commit()

        with self.assertRaises(RuntimeError) as ctx:
            fund_data.FundDataStore(str(self.db)).ensure_schema()
        self.assertIn("industry_allocations", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
