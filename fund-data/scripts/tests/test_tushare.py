import unittest
from unittest.mock import MagicMock

SCRIPT_DIR = __file__.rsplit("/", 2)[0]
import sys

sys.path.insert(0, SCRIPT_DIR.rsplit("/", 1)[0])

from scripts import fund_data  # noqa: E402


def _df_from_rows(rows: list[dict]) -> MagicMock:
    """Build a tiny pandas-like DataFrame for mocking tushare responses."""
    df = MagicMock()
    df.empty = len(rows) == 0
    df.iterrows = lambda: list(rows.items() if False else enumerate(rows))
    if rows:
        df.iloc = MagicMock()
        df.iloc.__getitem__.return_value.to_dict.return_value = rows[0]
    return df


class FakePro:
    """Minimal stand-in for tushare.pro_api()."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def fund_basic(self, **kwargs):
        self.calls.append(("fund_basic", kwargs))
        return [
            {
                "ts_code": "110022.OF",
                "name": "易方达消费",
                "fund_type": "股票型",
                "management": "易方达基金",
                "custodian": "农业银行",
            },
        ]

    def fund_nav(self, **kwargs):
        self.calls.append(("fund_nav", kwargs))
        return [
            {
                "nav_date": "2024-01-31",
                "unit_nav": "3.1330",
                "accum_nav": "3.1330",
                "adj_nav": "-0.0132",
            },
            {
                "nav_date": "2024-01-30",
                "unit_nav": "3.1750",
                "accum_nav": "3.1750",
                "adj_nav": "-0.0234",
            },
        ]

    def fund_portfolio(self, **kwargs):
        self.calls.append(("fund_portfolio", kwargs))
        return [
            {
                "end_date": "2024-12-31",
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "ratio": 9.83,
                "amount": 12345.0,
                "mkv": 23456789.0,
            },
        ]

    def fund_manager(self, **kwargs):
        self.calls.append(("fund_manager", kwargs))
        return [
            {
                "name": "萧楠",
                "gender": "M",
                "ts_code": "110022.OF",
                "fund_name": "易方达消费",
                "return_rate": 2.75,
            },
        ]


class TushareProviderInitTests(unittest.TestCase):
    def test_constructs_with_pro_module(self):
        pro = FakePro()
        provider = fund_data.TushareProvider(pro_module=pro)
        self.assertIs(provider.pro, pro)

    def test_raises_when_no_token_and_no_pro(self):
        import os

        old = os.environ.pop("TUSHARE_TOKEN", None)
        try:
            with self.assertRaises(fund_data.ProviderError):
                fund_data.TushareProvider()
        finally:
            if old is not None:
                os.environ["TUSHARE_TOKEN"] = old


class TushareProviderMethodTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pro = FakePro()
        self.provider = fund_data.TushareProvider(pro_module=self.pro)

    def test_fund_list_normalizes_to_6_digit_codes(self):
        rows = self.provider.fund_list()
        self.assertEqual(rows[0]["fund_code"], "110022")
        self.assertEqual(rows[0]["fund_name"], "易方达消费")
        self.assertEqual(rows[0]["source"], "tushare.fund_basic")

    def test_search_funds_delegates_to_fund_basic(self):
        rows = self.provider.search_funds("易方达")
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.pro.calls[-1][0], "fund_basic")
        self.assertEqual(self.pro.calls[-1][1].get("name"), "易方达")

    def test_nav_history_strips_dates_and_normalizes(self):
        rows = self.provider.nav_history("110022", start_date="2024-01-01", end_date="2024-12-31")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["nav_date"], "2024-01-31")
        self.assertEqual(rows[0]["unit_nav"], 3.133)
        # The Tushare call should have been made with YYYYMMDD dates.
        call = self.pro.calls[-1]
        self.assertEqual(call[0], "fund_nav")
        self.assertEqual(call[1]["start_date"], "20240101")
        self.assertEqual(call[1]["end_date"], "20241231")
        self.assertEqual(call[1]["ts_code"], "110022.OF")

    def test_profile_maps_management_to_fund_company(self):
        profile = self.provider.profile("110022")
        self.assertEqual(profile["fund_code"], "110022")
        self.assertEqual(profile["fund_company"], "易方达基金")
        self.assertEqual(profile["custodian"], "农业银行")
        self.assertEqual(profile["source"], "tushare.fund_basic")

    def test_stock_holdings_translates_ratio_to_decimal(self):
        rows = self.provider.stock_holdings("110022", report_year="2024")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stock_code"], "600519")
        self.assertEqual(rows[0]["net_value_ratio"], 0.0983)  # 9.83 / 100
        self.assertEqual(rows[0]["market_value"], 23456789.0)
        # Period should be the year-end quarter.
        self.assertEqual(self.pro.calls[-1][1]["period"], "20241231")

    def test_stock_holdings_passes_through_existing_decimal_ratio(self):
        # If ratio is already <=1.0, do not divide again.
        self.pro.fund_portfolio = lambda **kw: [
            {
                "end_date": "2024-12-31",
                "stock_code": "000001",
                "stock_name": "X",
                "ratio": 0.05,
                "amount": 100.0,
                "mkv": 1000.0,
            }
        ]
        rows = self.provider.stock_holdings("110022", report_year="2024")
        self.assertEqual(rows[0]["net_value_ratio"], 0.05)

    def test_fund_managers_extracts_current_fund_code(self):
        rows = self.provider.fund_managers("110022")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["manager_name"], "萧楠")
        self.assertEqual(rows[0]["current_fund_codes"], "110022")
        self.assertEqual(rows[0]["best_return"], 2.75)


class BuildProvidersTushareTests(unittest.TestCase):
    def test_tushare_token_routes_into_chain(self):
        import os
        from unittest.mock import patch

        old = os.environ.get("TUSHARE_TOKEN")
        os.environ["TUSHARE_TOKEN"] = "fake"
        try:
            # Patch TushareProvider.__init__ so it does not need a real pro.
            with patch.object(fund_data.TushareProvider, "__init__", lambda self: None):
                providers, warnings = fund_data.build_providers_full("auto", capability="profile")
            names = [type(p).__name__ for p in providers]
            # Tushare should be tried first, then AkShare, then Eastmoney.
            # AkShare may be skipped if not installed in the running Python;
            # we only assert Tushare is in the chain.
            self.assertIn("TushareProvider", names)
        finally:
            if old is None:
                os.environ.pop("TUSHARE_TOKEN", None)
            else:
                os.environ["TUSHARE_TOKEN"] = old

    def test_unknown_provider_in_explicit_mode_raises(self):
        with self.assertRaises(fund_data.ProviderError):
            fund_data.build_providers_full("not_a_real_provider", capability="profile")


if __name__ == "__main__":
    unittest.main()
