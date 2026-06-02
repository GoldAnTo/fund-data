"""Eastmoney direct provider (no API key required).

Lifted from ``fund_data.py`` in the 0.3.0 split (RFC
``docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md``).
Covers the four no-key Eastmoney endpoints: fund search,
fund universe list, NAV history, and snapshot. The other
capabilities (stock / bond / industry / fees / dividends /
splits / managers) raise :class:`ProviderError` so the
chain falls through to AkShare / Tushare for them.
"""

from __future__ import annotations

from typing import Any

from .. import http, parsers
from ..http import FundDataClient
from ..paths import PROVIDER_EASTMONEY
from .base import ProviderError

__all__ = ["EastmoneyProvider"]


class EastmoneyProvider:
    name = PROVIDER_EASTMONEY

    def __init__(self, client: FundDataClient | None = None) -> None:
        self.client = client or FundDataClient()

    def search_funds(self, keyword: str) -> list[dict[str, Any]]:
        return parsers.parse_search_results(self.client.search(keyword))

    def fund_list(self) -> list[dict[str, Any]]:
        return parsers.parse_fund_code_list(self.client.fund_code_list())

    def nav_history(
        self,
        code: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        page: int = 1,
        per: int = 20,
    ) -> list[dict[str, Any]]:
        return parsers.parse_nav_history(
            self.client.nav_history(
                code, start_date=start_date, end_date=end_date, page=page, per=per
            )
        )

    def snapshot(self, code: str) -> dict[str, Any]:
        # parse_snapshot returns None when the Eastmoney page is empty
        # (back-end share classes). Translate that to an empty dict
        # so the provider chain does not raise "provider returned
        # no rows" and we still surface the no-snapshot case to the
        # caller as a falsy value.
        return parsers.parse_snapshot(self.client.snapshot(code), default_code=code) or {}

    def stock_holdings(self, code: str, *, report_year: str | None = None) -> list[dict[str, Any]]:
        raise ProviderError(
            "Eastmoney direct stock holdings are not implemented; use akshare or investoday"
        )


