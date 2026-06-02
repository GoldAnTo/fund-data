"""HTTP client for the Eastmoney public endpoints.

Lifted from ``fund_data.py`` in the 0.3.0 split (RFC
``docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md``).
A small stdlib-only HTTP client (no requests / urllib3 / httpx
dependency) that the EastmoneyProvider uses for the four
no-key endpoints. The AkShare / Investoday / Tushare
providers are not stdlib ``urllib`` clients -- they wrap
their own SDK.

The class is dependency-free on purpose: the project does
not want a 5 MB transitive dep just to issue four GETs.
``macOS happy-eyeballs + urllib deadlock`` (see
``fund-data/AGENTS.md``) is patched in :mod:`_net_compat`
at import time; the client picks up the patched
``socket.getaddrinfo`` automatically.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import normalizers

__all__ = ["FundDataClient", "_RateLimiter"]


def _normalize(code: str) -> str:
    """Thin wrapper so FundDataClient.snapshot / nav_history
    have a local handle on ``normalize_fund_code`` without
    importing it from a top-level symbol that may shadow."""
    return normalizers.normalize_fund_code(code)


@dataclass
class FundDataClient:
    min_interval_seconds: float = 1.0
    timeout_seconds: int = 20
    rate_limiter: _RateLimiter | None = None

    SEARCH_URL = "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx"
    FUND_CODE_URL = "https://fund.eastmoney.com/js/fundcode_search.js"
    NAV_URL = "https://fundf10.eastmoney.com/F10DataApi.aspx"
    SNAPSHOT_URL_TEMPLATE = "https://fund.eastmoney.com/pingzhongdata/{code}.js"

    def __post_init__(self) -> None:
        self._last_call = 0.0
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://fund.eastmoney.com/",
        }

    def get_text(self, url: str, params: dict[str, Any] | None = None) -> str:
        full_url = url if not params else f"{url}?{urlencode(params)}"
        request = Request(full_url, headers=self.headers)
        if self.rate_limiter is not None:
            with self.rate_limiter:
                response = urlopen(request, timeout=self.timeout_seconds)
                charset = response.headers.get_content_charset() or "utf-8"
                data = response.read()
        else:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self.min_interval_seconds:
                time.sleep(self.min_interval_seconds - elapsed)
            response = urlopen(request, timeout=self.timeout_seconds)
            charset = response.headers.get_content_charset() or "utf-8"
            data = response.read()
            self._last_call = time.monotonic()
        return data.decode(charset, errors="replace")

    def search(self, keyword: str) -> str:
        return self.get_text(self.SEARCH_URL, {"m": "1", "key": keyword})

    def fund_code_list(self) -> str:
        return self.get_text(self.FUND_CODE_URL)

    def nav_history(
        self,
        code: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        page: int = 1,
        per: int = 20,
    ) -> str:
        params = {
            "type": "lsjz",
            "code": _normalize(code),
            "page": page,
            "per": per,
        }
        if start_date:
            params["sdate"] = start_date
        if end_date:
            params["edate"] = end_date
        return self.get_text(self.NAV_URL, params)

    def snapshot(self, code: str) -> str:
        return self.get_text(self.SNAPSHOT_URL_TEMPLATE.format(code=_normalize(code)))


class _RateLimiter:
    def __init__(self, min_interval_seconds: float) -> None:
        self.min_interval_seconds = min_interval_seconds
        self._lock = threading.Lock()
        self._last_call = 0.0

    def __enter__(self) -> _RateLimiter:
        self._lock.acquire()
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._last_call = time.monotonic()
        self._lock.release()


