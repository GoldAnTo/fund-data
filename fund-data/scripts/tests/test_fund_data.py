import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path

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
        fund_data.AkshareProvider.__init__ = lambda self: (_ for _ in ()).throw(
            fund_data.ProviderError("akshare is not installed for test")
        )

        try:
            with self.assertLogs("fund_data", level="WARNING") as log_ctx:
                providers = fund_data.build_providers("auto", capability="profile")
        finally:
            fund_data.AkshareProvider.__init__ = original_akshare_init

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
                                "netValueRatio": 0.0983,
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


if __name__ == "__main__":
    unittest.main()
