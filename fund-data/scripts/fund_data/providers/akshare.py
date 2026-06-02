"""AkShare provider (optional dependency, ``pip install akshare``).

Lifted from ``fund_data.py`` in the 0.3.0 split (RFC
``docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md``).
This is the workhorse for the AkShare-only capabilities
(profile, stock / bond / industry holdings, fees, dividends,
splits, fund managers) and the fallback snapshot for the
back-end share classes (``000002`` / ``000012`` / ``000108`` /
...) whose Eastmoney ``pingzhongdata/{code}.js`` page is a
stub that ``parse_snapshot`` rejects with ``None``.

The class is imported lazily so a deploy that does not have
``akshare`` installed does not break import time. Set
``FUND_DATA_DISABLE_AKSHARE=1`` to skip the package import
even when it is installed (CI runners do this).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from .. import http, normalizers, parsers
from ..http import FundDataClient
from ..paths import PROVIDER_AKSHARE
from .base import ProviderError

__all__ = ["AkshareProvider"]


class AkshareProvider:
    name = PROVIDER_AKSHARE

    def __init__(
        self,
        ak_module: Any | None = None,
        client: FundDataClient | None = None,
    ) -> None:
        if ak_module is None and os.environ.get("FUND_DATA_DISABLE_AKSHARE") == "1":
            raise ProviderError("akshare is disabled by FUND_DATA_DISABLE_AKSHARE=1")
        if ak_module is not None:
            self.ak = ak_module
        else:
            try:
                import akshare as ak  # type: ignore
            except Exception as exc:
                raise ProviderError(
                    "akshare is not installed; run `python3 -m pip install akshare`"
                ) from exc
            self.ak = ak
        # Lazy FundDataClient: the Eastmoney direct endpoint is the
        # fallback target of :meth:`nav_history` when AkShare's V8
        # eval explodes on Eastmoney CDN-injected JS garbage. The
        # client is dependency-free (stdlib urllib only -- see
        # :mod:`http`), so constructing it is cheap.
        self.client = client or FundDataClient()

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
        for item in normalizers._records(self.ak.fund_name_em()):
            code = item.get("基金代码") or item.get("基金代码 ") or item.get("代码")
            name = item.get("基金简称") or item.get("基金名称") or item.get("名称")
            if not code or not name:
                continue
            rows.append(
                {
                    "fund_code": normalizers.normalize_fund_code(code),
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
        normalized = normalizers.normalize_fund_code(code)
        try:
            raw_rows = normalizers._records(
                self.ak.fund_open_fund_info_em(
                    symbol=normalized, indicator="单位净值走势"
                )
            )
        except Exception as exc:
            # AkShare's V8 eval (py_mini_racer) over the
            # ``pingzhongdata/{code}.js`` body can fail in two ways
            # that come from Eastmoney's CDN, not from our code:
            #
            #   1. ``ReferenceError: Data_netWorthTrend is not
            #      defined`` -- the JS header that declares the
            #      ``var Data_netWorthTrend = [...]`` block has
            #      been truncated or had garbage injected before
            #      it (CDN/WAF noise).
            #   2. ``SyntaxError: Unexpected token '<'`` --
            #      Eastmoney returned an HTML error page (5xx or
            #      rate-limit) and py_mini_racer's V8 sees a ``<``
            #      outside any JS context.
            #
            # Both manifest in
            # ``sync_failures.operation='batch-sync'`` /
            # ``provider='akshare'`` with the message
            # ``all providers failed for nav_history: akshare: ...``
            # (553/562 rows on 2026-06-03). When the ``fund_cli
            # batch-sync --provider akshare`` path or a cron job
            # uses Akshare as the explicit provider, the chain
            # does not fall through to Eastmoney, so the only
            # way to recover is to do the fallback inside this
            # method. The fallback hits
            # ``https://fundf10.eastmoney.com/F10DataApi.aspx``
            # via :class:`FundDataClient` + :func:`parse_nav_history`
            # -- the same URL and parser
            # :class:`EastmoneyProvider` uses, so we keep one
            # canonical NAV path.
            err = str(exc)
            if "ReferenceError" in err or "SyntaxError" in err:
                return self._nav_history_fallback(
                    normalized,
                    start_date=start_date,
                    end_date=end_date,
                    page=page,
                    per=per,
                )
            raise
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
                    "unit_nav": normalizers._to_float(item.get("单位净值")),
                    "accumulated_nav": normalizers._to_float(item.get("累计净值")),
                    "daily_growth_rate": normalizers._to_float(item.get("日增长率"), percent=True),
                    "subscribe_status": "",
                    "redeem_status": "",
                    "dividend": "",
                    "source": "akshare.fund_open_fund_info_em",
                }
            )
        rows.sort(key=lambda row: row["nav_date"], reverse=True)
        return rows

    def _nav_history_fallback(
        self,
        code: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        page: int = 1,
        per: int = 20,
    ) -> list[dict[str, Any]]:
        """Recover from AkShare V8 eval failures via
        :class:`FundDataClient` + :func:`parse_nav_history`.

        The fallback URL is
        ``https://fundf10.eastmoney.com/F10DataApi.aspx`` -- the
        same endpoint :class:`EastmoneyProvider` uses -- and the
        ``content:"<table>"`` regex parser is robust to the CDN
        noise that breaks ``py_mini_racer``. The source tag on
        every row is rewritten to make it obvious in the
        ``nav_history`` table that the data came from the
        fallback path (and to keep the regression test
        pinpointing the branch).

        Any exception from the Eastmoney direct path is
        re-raised as :class:`ProviderError` so the failure
        surfaces in ``run_provider_chain`` (and from there in
        ``sync_failures``) under a uniform shape (rather than
        leaking the raw ``RuntimeError`` from the ``urllib``
        layer).
        """
        try:
            raw = self.client.nav_history(
                code,
                start_date=start_date,
                end_date=end_date,
                page=page,
                per=per,
            )
            rows = parsers.parse_nav_history(raw)
        except Exception as exc:
            raise ProviderError(
                f"akshare fallback to eastmoney F10DataApi also failed: {exc}"
            ) from exc
        for row in rows:
            row["source"] = "akshare.fallback.eastmoney.f10dataapi"
        # Match the main-path ordering so callers do not have
        # to know which branch produced the rows. Eastmoney's
        # F10DataApi.aspx returns rows in HTML-document order
        # (typically descending, but not guaranteed), so sort
        # explicitly.
        rows.sort(key=lambda row: row["nav_date"], reverse=True)
        return rows

    def snapshot(self, code: str) -> dict[str, Any]:
        """AkShare fallback for funds whose Eastmoney snapshot is empty.

        Background: back-end share classes (``000002`` / ``000012`` /
        ``000108`` / ...) and a few delisted / merged share classes
        hit a stub Eastmoney ``pingzhongdata/{code}.js`` page that
        parses to ``None``. With no ``AkshareProvider.snapshot`` the
        provider chain cannot recover and the fund lands in
        ``sync_failures``. This method assembles a snapshot dict in
        the same shape as :func:`parse_snapshot` (so
        :meth:`FundDataStore.upsert_snapshot` stores it without
        branching on provider) by reusing the existing
        :meth:`profile` and :meth:`stock_holdings` calls.

        AkShare does not expose ``source_rate`` / ``current_rate`` /
        ``min_purchase`` / a returns panel on its public endpoints
        in a form the parser can trust, so those are returned as
        ``None`` / empty. The goal here is "row exists, partial but
        structured" so the 380-fund ``sync_failures`` backlog can
        close; richer fields can be filled in by a later patch that
        re-fetches from Investoday / Eastmoney when those come back
        online.

        Returns an empty dict on any failure so the provider chain
        can fall through to the next provider (or so the caller
        surfaces "no snapshot" instead of a 500).
        """
        normalized = normalizers.normalize_fund_code(code)
        try:
            profile = self.profile(normalized)
            holdings = self.stock_holdings(normalized)
        except Exception:
            return {}
        stock_codes = [
            str(h["stock_code"]) for h in holdings if h.get("stock_code")
        ]
        return {
            "fund_code": normalized,
            "fund_name": profile.get("fund_name", ""),
            "source_rate": None,
            "current_rate": None,
            "min_purchase": None,
            "stock_codes": stock_codes,
            "returns": {},
            "source": "akshare.profile+stock_holdings",
        }

    def stock_holdings(self, code: str, *, report_year: str | None = None) -> list[dict[str, Any]]:
        year = report_year or str(datetime.now().year - 1)
        rows = []
        for item in normalizers._records(
            self.ak.fund_portfolio_hold_em(symbol=normalizers.normalize_fund_code(code), date=year)
        ):
            stock_code = item.get("股票代码") or item.get("代码")
            stock_name = item.get("股票名称") or item.get("名称")
            if not stock_code or not stock_name:
                continue
            rows.append(
                {
                    "report_period": normalizers._normalize_report_period(item.get("季度") or year),
                    "stock_code": str(stock_code).zfill(6),
                    "stock_name": str(stock_name),
                    "net_value_ratio": normalizers._to_float(item.get("占净值比例"), percent=True),
                    "shares": normalizers._to_float(item.get("持股数")),
                    "market_value": normalizers._to_float(item.get("持仓市值")),
                    "source": "akshare.fund_portfolio_hold_em",
                }
            )
        return rows

    def profile(self, code: str) -> dict[str, Any]:
        profile = normalizers._profile_dict(
            normalizers._records(self.ak.fund_overview_em(symbol=normalizers.normalize_fund_code(code)))
        )
        established = profile.get("成立日期/规模", "")
        established_date = normalizers._normalize_date_text(established.split("/")[0]) if established else ""
        asset_size_text = profile.get("净资产规模") or profile.get("资产规模", "")
        return {
            "fund_code": normalizers.normalize_fund_code(code),
            "fund_name": profile.get("基金简称", ""),
            "full_name": profile.get("基金全称", ""),
            "fund_type": profile.get("基金类型", ""),
            "issue_date": normalizers._normalize_date_text(profile.get("发行日期", "")),
            "establishment_date": established_date,
            "asset_size": normalizers._first_number(asset_size_text),
            "asset_size_date": normalizers._normalize_date_text(asset_size_text),
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
                symbol=normalizers.normalize_fund_code(code), date=year
            )
        except Exception:
            return []
        rows = []
        for item in normalizers._records(raw_df):
            bond_code = item.get("债券代码") or item.get("代码")
            bond_name = item.get("债券名称") or item.get("名称")
            if not bond_code or not bond_name:
                continue
            net_value_ratio = normalizers._to_float(
                normalizers._first_value(
                    item,
                    "占净值比例",
                    "占基金资产净值比例",
                    "占净值比",
                    "持仓占净值比",
                    "ratio",
                ),
                percent=True,
            )
            market_value = normalizers._to_float(
                normalizers._first_value(
                    item,
                    "持仓市值",
                    "市值",
                    "债券市值",
                    "market_value",
                )
            )
            rows.append(
                {
                    "report_period": normalizers._normalize_report_period(item.get("季度") or year),
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
        for item in normalizers._records(
            self.ak.fund_portfolio_industry_allocation_em(
                symbol=normalizers.normalize_fund_code(code), date=year
            )
        ):
            industry = normalizers._first_value(
                item,
                "行业类别",
                "行业名称",
                "行业",
                "行业分布",
                "类别",
            )
            if not industry:
                continue
            net_value_ratio = normalizers._to_float(
                normalizers._first_value(
                    item,
                    "占净值比例",
                    "占基金资产净值比例",
                    "占净值比",
                    "持仓占净值比",
                    "市值占净值比例",
                ),
                percent=True,
            )
            market_value = normalizers._to_float(
                normalizers._first_value(
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
                        normalizers._first_value(item, "季度", "截止时间", "报告期", "report_period") or year
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
                profile_rows = normalizers._records(self.ak.fund_overview_em(symbol=normalizers.normalize_fund_code(code)))
                profile = normalizers._profile_dict(profile_rows)
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
            normalizers._fee_indicator_alias(item)
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
                raw_rows = normalizers._records(
                    self.ak.fund_fee_em(symbol=normalizers.normalize_fund_code(code), indicator=indicator)
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
        normalized = normalizers.normalize_fund_code(code)
        rows: list[dict[str, Any]] = []
        try:
            etf_info = normalizers._records(self.ak.fund_etf_fund_info_em(symbol=normalized))
            if etf_info:
                first_row = etf_info[0] if isinstance(etf_info, list) else etf_info
                management_fee = normalizers._first_value(
                    first_row,
                    "管理费率",
                    "管理费",
                    "mgmt_fee",
                    "management_fee",
                )
                custody_fee = normalizers._first_value(
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
                                "fee": normalizers._rate_to_decimal(management_fee),
                                "fee_text": normalizers._clean_text(management_fee),
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
                                "fee": normalizers._rate_to_decimal(custody_fee),
                                "fee_text": normalizers._clean_text(custody_fee),
                                "discount_fee": None,
                                "discount_fee_text": "",
                                "source": "akshare.fund_etf_fund_info_em:custody_fee",
                            }
                        )
        except Exception:
            pass
        if not rows:
            try:
                open_fund_info = normalizers._records(
                    self.ak.fund_open_fund_info_em(symbol=normalized, indicator="基本信息")
                )
                for item in open_fund_info:
                    management_fee = normalizers._first_value(
                        item,
                        "管理费率",
                        "管理费",
                        "mgmt_fee",
                    )
                    custody_fee = normalizers._first_value(
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
                                "fee": normalizers._rate_to_decimal(management_fee),
                                "fee_text": normalizers._clean_text(management_fee),
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
                                "fee": normalizers._rate_to_decimal(custody_fee),
                                "fee_text": normalizers._clean_text(custody_fee),
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
            fee_text = normalizers._clean_text(
                normalizers._first_value(
                    item,
                    "费用",
                    "费率",
                    "赎回费率",
                    "原费率",
                    "天天基金优惠费率",
                    "天天基金优惠费率-银行卡购买",
                )
            )
            condition = normalizers._clean_text(
                normalizers._first_value(item, "条件或名称", "适用金额", "适用期限", "项目", "名称")
            )
            if not condition:
                values = [normalizers._clean_text(value) for value in item.values()]
                if len(values) >= 2:
                    condition = values[0]
                    fee_text = fee_text or values[1]
            if not condition and not fee_text:
                continue
            rows.append(
                {
                    "fee_type": normalizers._clean_text(normalizers._first_value(item, "费用类型") or indicator),
                    "condition_name": condition,
                    "fee": normalizers._rate_to_decimal(fee_text),
                    "fee_text": fee_text,
                    "discount_fee": normalizers._rate_to_decimal(
                        normalizers._first_value(
                            item,
                            "优惠费率",
                            "天天基金优惠费率",
                            "天天基金优惠费率-银行卡购买",
                            "天天基金优惠费率-活期宝购买",
                        )
                    ),
                    "discount_fee_text": normalizers._clean_text(
                        normalizers._first_value(
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

        url = f"https://fundf10.eastmoney.com/jjfl_{normalizers.normalize_fund_code(code)}.html"
        try:
            response = requests.get(url, timeout=20)
            response.raise_for_status()
        except Exception:
            return []

        wanted = {normalizers._fee_indicator_alias(indicator) for indicator in indicators}
        soup = BeautifulSoup(response.text, features="html.parser")
        rows: list[dict[str, Any]] = []
        for title_elem in soup.find_all(name="h4", class_="t"):
            title = normalizers._fee_indicator_alias(title_elem.get_text(strip=True))
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
            values = [normalizers._clean_text(value) for value in record.values()]
            for index in range(0, len(values) - 1, 2):
                condition = values[index]
                fee_text = values[index + 1]
                if not condition or not fee_text:
                    continue
                rows.append(
                    {
                        "fee_type": title,
                        "condition_name": condition,
                        "fee": normalizers._rate_to_decimal(fee_text),
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
            condition = normalizers._clean_text(normalizers._first_value(record, "适用金额", "适用期限", "条件或名称"))
            if not condition and record:
                condition = normalizers._clean_text(next(iter(record.values())))
            fee_text = normalizers._clean_text(normalizers._first_value(record, "费率", "赎回费率", "原费率", "费用"))
            discount_text = normalizers._clean_text(
                normalizers._first_value(
                    record,
                    "优惠费率",
                    "天天基金优惠费率",
                    "天天基金优惠费率-银行卡购买",
                    "天天基金优惠费率-活期宝购买",
                )
            )
            combined = normalizers._clean_text(normalizers._first_value(record, "原费率|天天基金优惠费率"))
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
                    "fee": normalizers._rate_to_decimal(fee_text),
                    "fee_text": fee_text,
                    "discount_fee": normalizers._rate_to_decimal(discount_text),
                    "discount_fee_text": discount_text,
                    "source": "eastmoney.fund_fee_page",
                }
            )
        return rows

    def dividends(self, code: str) -> list[dict[str, Any]]:
        code = normalizers.normalize_fund_code(code)
        try:
            dividend_records = normalizers._records(
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
            normalizers._records(self.ak.fund_fh_em()),
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
            item_code = normalizers._first_value(item, "基金代码", "代码", "fund_code")
            if require_code_match and (not item_code or normalizers.normalize_fund_code(item_code) != code):
                continue
            dividend_date = normalizers._first_value(item, "权益登记日", "登记日", "dividend_date")
            if not dividend_date:
                continue
            rows.append(
                {
                    "dividend_date": normalizers._normalize_date_text(dividend_date),
                    "ex_dividend_date": normalizers._normalize_date_text(
                        normalizers._first_value(item, "除息日期", "除息日", "ex_dividend_date")
                    ),
                    "dividend_per_share": normalizers._first_number(
                        normalizers._first_value(item, "分红", "每份分红", "dividend")
                    ),
                    "payment_date": normalizers._normalize_date_text(
                        normalizers._first_value(item, "分红发放日", "发放日", "payment_date")
                    ),
                    "source": source,
                }
            )
        return rows

    def splits(self, code: str) -> list[dict[str, Any]]:
        code = normalizers.normalize_fund_code(code)
        try:
            split_records = normalizers._records(
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
            normalizers._records(self.ak.fund_cf_em()),
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
            item_code = normalizers._first_value(item, "基金代码", "代码", "fund_code")
            if require_code_match and (not item_code or normalizers.normalize_fund_code(item_code) != code):
                continue
            split_date = normalizers._first_value(item, "拆分折算日", "拆分日", "split_date")
            if not split_date:
                continue
            rows.append(
                {
                    "split_date": normalizers._normalize_date_text(split_date),
                    "split_type": normalizers._clean_text(normalizers._first_value(item, "拆分类型", "类型", "split_type")),
                    "split_ratio": normalizers._ratio_value(
                        normalizers._first_value(item, "拆分折算比例", "拆分比例", "拆分折算", "split_ratio")
                    ),
                    "source": source,
                }
            )
        return rows

    def fund_managers(self, code: str | None = None) -> list[dict[str, Any]]:
        normalized_code = normalizers.normalize_fund_code(code) if code else None
        rows = []
        for item in normalizers._records(self.ak.fund_manager_em()):
            current_codes = str(normalizers._first_value(item, "现任基金代码", "current_fund_codes") or "")
            if normalized_code and normalized_code not in current_codes:
                continue
            rows.append(
                {
                    "manager_name": str(normalizers._first_value(item, "姓名", "manager_name") or ""),
                    "company": str(normalizers._first_value(item, "所属公司", "company") or ""),
                    "current_fund_codes": current_codes,
                    "current_funds": str(normalizers._first_value(item, "现任基金", "current_funds") or ""),
                    "tenure_days": int(
                        normalizers._to_float(normalizers._first_value(item, "累计从业时间", "tenure_days")) or 0
                    ),
                    "current_aum": normalizers._to_float(
                        normalizers._first_value(item, "现任基金资产总规模", "current_aum")
                    ),
                    "best_return": normalizers._to_float(
                        normalizers._first_value(item, "现任基金最佳回报", "best_return"), percent=True
                    ),
                    "source": "akshare.fund_manager_em",
                }
            )
        return rows


