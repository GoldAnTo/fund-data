"""Tushare provider (paid, opt-in via API key).

Lifted from ``fund_data.py`` in the 0.3.0 split (RFC
``docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md``).
Tushare Pro is the structured-JSON alternative to AkShare
for the same set of capabilities. Set ``TUSHARE_TOKEN`` to
enable; without a token the class is still importable but
every method raises :class:`ProviderError`.
"""

from __future__ import annotations

import os
from typing import Any

from .. import normalizers
from ..paths import PROVIDER_TUSHARE
from .base import ProviderError

__all__ = ["TushareProvider", "_tushare_period"]


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
        return f"{normalizers.normalize_fund_code(fund_code)}.OF"

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
                    "fund_code": normalizers.normalize_fund_code(code),
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
                    "unit_nav": normalizers._to_float(item.get("unit_nav")),
                    "accumulated_nav": normalizers._to_float(item.get("accum_nav")),
                    "daily_growth_rate": normalizers._to_float(item.get("adj_nav")),
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
            "fund_code": normalizers.normalize_fund_code(code),
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
            ratio = normalizers._to_float(item.get("ratio"), percent=False)
            if ratio is not None and ratio > 1.0:
                ratio = ratio / 100.0
            rows.append(
                {
                    "report_period": str(item.get("end_date", period)),
                    "stock_code": str(stock_code).zfill(6),
                    "stock_name": str(item.get("stock_name", "")),
                    "net_value_ratio": ratio,
                    "shares": normalizers._to_float(item.get("amount")),
                    "market_value": normalizers._to_float(item.get("mkv")),
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
                    "best_return": normalizers._to_float(item.get("return_rate")),
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


