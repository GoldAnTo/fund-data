"""Investoday provider (paid, opt-in via API key).

Lifted from ``fund_data.py`` in the 0.3.0 split (RFC
``docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md``).
The Investoday catalog (``/fund/all``) is the source of the
``fund_profiles`` rows that drive the 98.9 % profile
coverage. Set ``INVESTODAY_API_KEY`` (or the legacy
``INVESTDATA_API_KEY``) to enable; without a key the
class is still importable but every method raises
:class:`ProviderError`.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .. import normalizers
from ..paths import PROVIDER_INVESTODAY
from .base import ProviderError

__all__ = ["InvestodayProvider"]


class InvestodayProvider:
    name = PROVIDER_INVESTODAY

    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        # Accept either env var. ``INVESTODAY_API_KEY`` is the canonical
        # name we expose in PROVIDERS.md / SKILL.md; ``INVESTDATA_API_KEY``
        # is the legacy / Investoday-console-exported name and is kept
        # as a fallback for older setups.
        self.api_key = (
            api_key or os.environ.get("INVESTODAY_API_KEY") or os.environ.get("INVESTDATA_API_KEY")
        )
        if not self.api_key:
            raise ProviderError("INVESTODAY_API_KEY (or INVESTDATA_API_KEY) is not set")
        self.base_url = (
            base_url
            or os.environ.get("FINANCIAL_DATA_BASE_URL")
            or "https://data-api.investoday.net/data"
        ).rstrip("/")
        # Cache the /fund/all catalog for the lifetime of this provider
        # instance so repeated calls (search, profile per code, list) do
        # not re-hit the network. The catalog is ~27k records / ~10 MB;
        # the 1-hour TTL is a safety net for long-lived backfills.
        self._catalog_cache: list[dict[str, Any]] | None = None
        self._catalog_cache_ts: float = 0.0
        self._catalog_cache_ttl: float = 3600.0

    def _get_json(self, path: str, params: dict[str, Any]) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        request = Request(url, headers={"apiKey": self.api_key, "User-Agent": "fund-data-skill"})
        with urlopen(request, timeout=30) as response:
            data = response.read().decode("utf-8", errors="replace")
        return json.loads(data)

    @staticmethod
    def _normalize_fund_record(item: dict[str, Any]) -> dict[str, Any] | None:
        code = normalizers._first_value(item, "fundCode", "fund_code", "code", "FCODE", "基金代码")
        name = normalizers._first_value(
            item, "fundName", "fund_name", "name", "SHORTNAME", "基金名称", "基金简称"
        )
        if not code or not name:
            return None
        return {
            "fund_code": normalizers.normalize_fund_code(code),
            "fund_name": str(name),
            "fund_type": str(normalizers._first_value(item, "fundType", "fund_type", "type", "基金类型") or ""),
            "company": str(
                normalizers._first_value(item, "company", "fundCompany", "managerCompany", "基金公司") or ""
            ),
            "manager": str(normalizers._first_value(item, "manager", "fundManager", "基金经理") or ""),
            "nav": normalizers._to_float(normalizers._first_value(item, "nav", "unitNav", "DWJZ", "单位净值")),
            "nav_date": str(normalizers._first_value(item, "navDate", "date", "FSRQ", "净值日期") or ""),
            "other_names": str(normalizers._first_value(item, "otherNames", "alias", "aliases") or ""),
            "source": "investoday.fund_all",
            # Pass-through profile fields so ``profile()`` can build
            # a full row without a second API call.
            "_raw": item,
        }

    def _fetch_catalog(self) -> list[dict[str, Any]]:
        """Auto-paginate ``/fund/all`` until the full universe is in hand.

        The Investoday API caps ``pageSize`` at 500. The total universe
        is ~27k funds, so we walk ~55 pages and stop early when the
        page is short.
        """
        rows: list[dict[str, Any]] = []
        page = 1
        page_size = 500
        while True:
            payload = self._get_json("/fund/all", {"pageNum": page, "pageSize": page_size})
            records = normalizers._extract_payload_records(payload)
            if not records:
                break
            for item in records:
                row = self._normalize_fund_record(item)
                if row is not None:
                    rows.append(row)
            total = int(payload.get("totalCount") or 0)
            if total and page * page_size >= total:
                break
            if len(records) < page_size:
                break
            page += 1
            if page > 200:  # safety stop; 200 * 500 = 100k, well over 27k
                break
        return rows

    def _get_catalog(self) -> list[dict[str, Any]]:
        now = time.time()
        if self._catalog_cache is None or (now - self._catalog_cache_ts) > self._catalog_cache_ttl:
            self._catalog_cache = self._fetch_catalog()
            self._catalog_cache_ts = now
        return self._catalog_cache

    def fund_list(self) -> list[dict[str, Any]]:
        """Return every fund in the Investoday catalog (auto-paginated).

        Strips the internal ``_raw`` payload before returning so callers
        do not accidentally re-serialize the upstream record.
        """
        return [{k: v for k, v in row.items() if k != "_raw"} for row in self._get_catalog()]

    def profile(self, code: str) -> dict[str, Any]:
        """Look up the profile for ``code`` from the cached catalog.

        Raises :class:`ProviderError` if the code is not in the
        Investoday universe, or if the catalog cannot be fetched.
        """
        target = normalizers.normalize_fund_code(code)
        for row in self._get_catalog():
            if row["fund_code"] == target:
                raw = row.get("_raw") or {}
                # ``establishDate`` comes as ``"2010-08-20 00:00:00"``;
                # trim to ISO date for the ``establishment_date`` slot.
                raw_establish = raw.get("establishDate") or ""
                est_date = str(raw_establish)[:10] if raw_establish else ""
                return {
                    "fund_code": target,
                    "fund_name": str(raw.get("fundName") or row["fund_name"]),
                    "full_name": str(
                        raw.get("fundNameFull") or raw.get("fundName") or row["fund_name"]
                    ),
                    "fund_type": str(raw.get("fundType") or row.get("fund_type") or ""),
                    "issue_date": "",
                    "establishment_date": est_date,
                    "asset_size": None,
                    "asset_size_date": "",
                    "fund_company": str(
                        raw.get("managementCompanyName") or row.get("company") or ""
                    ),
                    "custodian": str(raw.get("custodianName") or ""),
                    "manager": "",
                    "benchmark": str(raw.get("benchmarkCode") or ""),
                    "tracking_target": "",
                    "is_qdii": bool(int(raw.get("isQdii") or 0)),
                    "is_fof": bool(int(raw.get("isFof") or 0)),
                    "investment_objective": str(raw.get("investmentObjective") or ""),
                    "investment_strategy": str(raw.get("investmentStrategy") or ""),
                    "risk_return_profile": str(raw.get("riskReturnProfile") or ""),
                    "source": "investoday.fund_all",
                }
        raise ProviderError(f"investoday: {code} not found in /fund/all catalog")

    def search_funds(self, keyword: str) -> list[dict[str, Any]]:
        keyword_text = str(keyword).lower()
        return [
            row
            for row in self.fund_list()
            if keyword_text in row["fund_code"].lower()
            or keyword_text in row["fund_name"].lower()
            or keyword_text in row.get("other_names", "").lower()
        ]

    def nav_history(
        self,
        code: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        page: int = 1,
        per: int = 20,
    ) -> list[dict[str, Any]]:
        records = normalizers._extract_payload_records(
            self._get_json(
                "/fund/nav/history",
                {
                    "fundCode": normalizers.normalize_fund_code(code),
                    "code": normalizers.normalize_fund_code(code),
                    "startDate": start_date or "",
                    "endDate": end_date or "",
                    "pageNum": page,
                    "pageSize": per,
                },
            )
        )
        rows = []
        for item in records:
            nav_date = str(normalizers._first_value(item, "navDate", "date", "tradeDate", "净值日期") or "")
            rows.append(
                {
                    "nav_date": nav_date,
                    "unit_nav": normalizers._to_float(normalizers._first_value(item, "unitNav", "nav", "单位净值")),
                    "accumulated_nav": normalizers._to_float(
                        normalizers._first_value(item, "accumulatedNav", "accNav", "累计净值")
                    ),
                    "daily_growth_rate": normalizers._to_float(
                        normalizers._first_value(item, "dailyGrowthRate", "dailyReturn", "日增长率"),
                        percent="%"
                        in str(
                            normalizers._first_value(item, "dailyGrowthRate", "dailyReturn", "日增长率") or ""
                        ),
                    ),
                    "subscribe_status": str(
                        normalizers._first_value(item, "subscribeStatus", "申购状态") or ""
                    ),
                    "redeem_status": str(normalizers._first_value(item, "redeemStatus", "赎回状态") or ""),
                    "dividend": str(normalizers._first_value(item, "dividend", "分红送配") or ""),
                    "source": "investoday.fund_nav_history",
                }
            )
        return rows

    def stock_holdings(self, code: str, *, report_year: str | None = None) -> list[dict[str, Any]]:
        records = normalizers._extract_payload_records(
            self._get_json(
                "/fund/portfolio-stock-holdings",
                {
                    "fundCode": normalizers.normalize_fund_code(code),
                    "code": normalizers.normalize_fund_code(code),
                    "reportYear": report_year or "",
                },
            )
        )
        rows = []
        for item in records:
            stock_code = normalizers._first_value(item, "stockCode", "stock_code", "股票代码")
            stock_name = normalizers._first_value(item, "stockName", "stock_name", "股票名称")
            if not stock_code or not stock_name:
                continue
            rows.append(
                {
                    "report_period": str(
                        normalizers._first_value(item, "reportPeriod", "quarter", "reportDate", "季度")
                        or report_year
                        or ""
                    ),
                    "stock_code": str(stock_code).zfill(6),
                    "stock_name": str(stock_name),
                    "net_value_ratio": normalizers._to_float(
                        normalizers._first_value(item, "netValueRatio", "holdingRatio", "占净值比例"),
                        percent="%"
                        in str(
                            normalizers._first_value(item, "netValueRatio", "holdingRatio", "占净值比例") or ""
                        ),
                    ),
                    "shares": normalizers._to_float(normalizers._first_value(item, "shares", "holdingShares", "持股数")),
                    "market_value": normalizers._to_float(
                        normalizers._first_value(item, "marketValue", "holdingMarketValue", "持仓市值")
                    ),
                    "source": "investoday.fund_portfolio_stock_holdings",
                }
            )
        return rows


