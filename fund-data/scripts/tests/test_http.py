"""Unit tests for ``scripts/fund_data/http.py``.

Lifted out of the package-level test bundle during the 0.3.0
split (RFC ``docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md``).
This file pins the stdlib-only HTTP client that the
EastmoneyProvider uses. The client is a thin wrapper
around ``urllib.request.urlopen`` with rate limiting;
behavior worth locking includes the URL templates, the
parameter encoding, and the rate-limiter contract.
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from fund_data import http  # noqa: E402


class ConstantsTests(unittest.TestCase):
    """URL templates are part of the agent contract; a
    future rename of the Eastmoney endpoint must come with
    a corresponding test edit."""

    def test_search_url(self) -> None:
        self.assertEqual(
            http.FundDataClient.SEARCH_URL,
            "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx",
        )

    def test_fund_code_list_url(self) -> None:
        self.assertEqual(
            http.FundDataClient.FUND_CODE_URL,
            "https://fund.eastmoney.com/js/fundcode_search.js",
        )

    def test_nav_url(self) -> None:
        self.assertEqual(
            http.FundDataClient.NAV_URL,
            "https://fundf10.eastmoney.com/F10DataApi.aspx",
        )

    def test_snapshot_url_template(self) -> None:
        self.assertEqual(
            http.FundDataClient.SNAPSHOT_URL_TEMPLATE,
            "https://fund.eastmoney.com/pingzhongdata/{code}.js",
        )


class ClientDefaultsTests(unittest.TestCase):
    def test_default_constructor_args(self) -> None:
        # All three dataclass fields have defaults; calling
        # without arguments is the supported constructor shape.
        client = http.FundDataClient()
        self.assertEqual(client.min_interval_seconds, 1.0)
        self.assertEqual(client.timeout_seconds, 20)
        self.assertIsNone(client.rate_limiter)

    def test_post_init_sets_user_agent_and_referer(self) -> None:
        # The user agent is a standard desktop Chrome string;
        # the referer is the Eastmoney domain. The site
        # serves the snapshot data only when both headers
        # are present.
        client = http.FundDataClient()
        self.assertIn("User-Agent", client.headers)
        self.assertIn("Mozilla", client.headers["User-Agent"])
        self.assertIn("Referer", client.headers)
        self.assertEqual(
            client.headers["Referer"],
            "https://fund.eastmoney.com/",
        )


class NavHistoryUrlTests(unittest.TestCase):
    def test_url_encodes_code_and_default_pagination(self) -> None:
        # The client appends ?type=lsjz&code=NNNNNN&page=1&per=20
        # when no date range is given. Pin the parameter shape
        # so a future change to the Eastmoney URL contract
        # is caught here rather than in a 5xx storm.
        client = http.FundDataClient()
        with patch.object(
            client, "get_text", return_value=""
        ) as mock_get:
            client.nav_history("110022")
        mock_get.assert_called_once()
        url = mock_get.call_args[0][0]
        params = mock_get.call_args[0][1]
        self.assertEqual(url, http.FundDataClient.NAV_URL)
        self.assertEqual(params["type"], "lsjz")
        self.assertEqual(params["code"], "110022")
        self.assertEqual(params["page"], 1)
        self.assertEqual(params["per"], 20)
        # No date range by default.
        self.assertNotIn("sdate", params)
        self.assertNotIn("edate", params)

    def test_passes_through_date_range(self) -> None:
        client = http.FundDataClient()
        with patch.object(
            client, "get_text", return_value=""
        ) as mock_get:
            client.nav_history(
                "110022",
                start_date="2024-01-01",
                end_date="2024-12-31",
            )
        params = mock_get.call_args[0][1]
        self.assertEqual(params["sdate"], "2024-01-01")
        self.assertEqual(params["edate"], "2024-12-31")

    def test_normalizes_code_via_wrapper(self) -> None:
        # nav_history normalizes the fund code before it
        # hits the URL. The HTTP client must not leak
        # whatever un-normalized form the caller passed
        # (e.g. ``"fund 110022"`` should become ``"110022"``).
        client = http.FundDataClient()
        with patch.object(client, "get_text", return_value="") as mock_get:
            client.nav_history("fund 110022")
        params = mock_get.call_args[0][1]
        self.assertEqual(params["code"], "110022")


class SnapshotUrlTests(unittest.TestCase):
    def test_url_format_includes_code(self) -> None:
        # The snapshot URL is ``/pingzhongdata/{code}.js``;
        # the ``{code}`` is filled with the normalized fund
        # code, never the raw caller input.
        client = http.FundDataClient()
        with patch.object(client, "get_text", return_value="") as mock_get:
            client.snapshot("110022")
        url = mock_get.call_args[0][0]
        self.assertEqual(
            url,
            "https://fund.eastmoney.com/pingzhongdata/110022.js",
        )


class RateLimiterTests(unittest.TestCase):
    """The rate limiter is a context manager that sleeps to
    enforce ``min_interval_seconds`` between calls. Used by
    the per-fund backfill runner to back off the
    Eastmoney endpoint without holding a global lock."""

    def test_does_not_sleep_on_first_call(self) -> None:
        limiter = http._RateLimiter(min_interval_seconds=10.0)
        with limiter:
            pass  # first call: no sleep

    def test_sleeps_when_called_twice_within_window(self) -> None:
        limiter = http._RateLimiter(min_interval_seconds=0.1)
        t0 = time.monotonic()
        with limiter:
            pass
        with limiter:
            elapsed = time.monotonic() - t0
            self.assertGreaterEqual(elapsed, 0.05)  # ~0.1s slept

    def test_serialised_via_lock(self) -> None:
        # Two threads entering the context manager at the
        # same time should still see the second one wait
        # for the first -- the lock is the point of the
        # class.
        limiter = http._RateLimiter(min_interval_seconds=0.1)
        call_times: list[float] = []

        def worker() -> None:
            with limiter:
                call_times.append(time.monotonic())

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Spacing between adjacent calls is at least 0.05s
        # (we accept 0.05 to avoid flakiness on busy CI).
        for i in range(1, len(call_times)):
            self.assertGreaterEqual(call_times[i] - call_times[i - 1], 0.05)


if __name__ == "__main__":
    unittest.main()
