"""Regression tests for the V8-eval fallback in
``AkshareProvider.nav_history``.

Background
----------

``AkshareProvider.nav_history`` calls
``ak.fund_open_fund_info_em(symbol=..., indicator="单位净值走势")``.
Internally that function ``GET``\ s
``https://fund.eastmoney.com/pingzhongdata/{code}.js`` and feeds
the response body to a ``py_mini_racer`` V8 engine via
``MiniRacer().eval(data_text)``.

Two Eastmoney CDN / WAF failure modes blow up V8:

  1. ``ReferenceError: Data_netWorthTrend is not defined`` -- the
     JS header that declares ``var Data_netWorthTrend = [...]`` is
     truncated or has garbage injected before it. V8 reaches the
     ``execute("Data_netWorthTrend")`` call with no such variable
     in scope.
  2. ``SyntaxError: Unexpected token '<'`` (followed by
     ``<!doctype html>``) -- Eastmoney returns an HTML error page
     on 5xx / rate-limit and V8 sees a literal ``<`` outside any
     JS context.

Both surface in ``sync_failures`` as
``all providers failed for nav_history: akshare: ...`` /
``provider='akshare'`` rows. 553/562 of the 2026-06-03
``sync_failures`` backlog is this. When the caller asks
``fund_cli batch-sync --provider akshare`` (or the
``industry-fill-2026-06-02`` cron job does), the provider chain
is not in play and the fund lands in ``sync_failures`` instead
of being recovered via the Eastmoney fallback.

The fix: ``AkshareProvider.nav_history`` catches the V8
``ReferenceError`` / ``SyntaxError`` exception classes and
falls through to ``FundDataClient.nav_history`` +
``parsers.parse_nav_history`` -- the same URL
(``https://fundf10.eastmoney.com/F10DataApi.aspx``) and the same
regex parser :class:`EastmoneyProvider` uses. The ``source``
column on the recovered rows is rewritten to
``akshare.fallback.eastmoney.f10dataapi`` so the recovery is
visible downstream.

What these tests pin
--------------------

  - **Main path still works.** A successful AkShare call still
    returns rows tagged ``akshare.fund_open_fund_info_em``; the
    fallback path does not steal control when AkShare is
    healthy.
  - **V8 ReferenceError falls through.** The recovered rows
    carry the ``akshare.fallback.eastmoney.f10dataapi`` source
    tag and the same shape as the EastmoneyProvider output
    (so :func:`FundDataStore.upsert_nav_history` is happy).
  - **V8 SyntaxError (HTML body) falls through.** Same as the
    ReferenceError case -- both V8 failure modes funnel
    through the same fallback.
  - **Non-V8 exceptions do not fall through.** A
    ``ValueError`` from inside the AkShare pandas pipeline (e.g.
    a numeric coercion failure) keeps the original
    exception -- the fallback is intentionally narrow to
    the V8 eval failures it can recover from.
  - **Fallback also fails cleanly.** If both AkShare and the
    Eastmoney direct path fail, the caller still sees a
    :class:`ProviderError` so ``run_provider_chain`` records
    the failure in ``sync_failures`` rather than swallowing
    it.
  - **``AkshareProvider(ak_module=...)`` constructor accepts
    a client override.** Tests that want to control the
    fallback path (without monkey-patching the module-level
    ``FundDataClient``) pass an explicit ``client=`` and
    verify the constructor wires it up. This matches the
    EastmoneyProvider constructor shape.
  - **Real F10DataApi.aspx HTML is parsed correctly.** A
    sample of the real Eastmoney ASPX body (not AkShare
    JavaScript) round-trips through the fallback to the
    canonical row shape.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from fund_data.providers.akshare import AkshareProvider
from fund_data.providers.base import ProviderError


def _akshare_rows():
    """A representative list-of-dicts that AkShare would
    return from ``fund_open_fund_info_em`` *after* AkShare
    has wrapped the V8 eval result in a DataFrame and
    ``normalizers._records`` has normalized it back to a
    list of plain dicts.
    """
    return [
        {
            "净值日期": "2024-01-01",
            "单位净值": 1.234,
            "累计净值": 1.567,
            "日增长率": "0.50%",
        }
    ]


F10_RAW = (
    'some js prefix '
    'content:"<table>'
    '<tr><th>净值日期</th><th>单位净值</th><th>累计净值</th>'
    '<th>日增长率</th><th>申购状态</th><th>赎回状态</th>'
    '<th>分红送配</th></tr>'
    '<tr><td>2024-01-01</td><td>1.234</td><td>1.567</td>'
    '<td>0.50%</td><td>开放</td><td>开放</td><td></td></tr>'
    '<tr><td>2024-01-02</td><td>1.240</td><td>1.580</td>'
    '<td>0.48%</td><td>开放</td><td>开放</td><td></td></tr>'
    '</table>",records:[1,2,3]'
)


class AkshareNavHistoryMainPathTests(unittest.TestCase):
    """When AkShare is healthy, the fallback path does not
    steal control and the canonical source tag is preserved."""

    def test_main_path_uses_akshare_source_tag(self) -> None:
        provider = AkshareProvider(ak_module=MagicMock())
        provider.ak.fund_open_fund_info_em = lambda **kw: _akshare_rows()
        # Set a client to detect a fallback we did not intend.
        provider.client.nav_history = MagicMock(return_value=F10_RAW)
        rows = provider.nav_history("110022")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "akshare.fund_open_fund_info_em")
        provider.client.nav_history.assert_not_called()

    def test_main_path_date_filter_respected(self) -> None:
        provider = AkshareProvider(ak_module=MagicMock())
        provider.ak.fund_open_fund_info_em = lambda **kw: [
            {"净值日期": "2023-12-31", "单位净值": 1.0, "累计净值": 1.0, "日增长率": "0%"},
            {"净值日期": "2024-01-01", "单位净值": 1.234, "累计净值": 1.567, "日增长率": "0.50%"},
        ]
        rows = provider.nav_history("110022", start_date="2024-01-01")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["nav_date"], "2024-01-01")


class AkshareNavHistoryV8FallbackTests(unittest.TestCase):
    """V8 eval failures funnel through FundDataClient +
    parsers.parse_nav_history, not ProviderError."""

    def test_reference_error_triggers_fallback(self) -> None:
        provider = AkshareProvider(ak_module=MagicMock())

        def v8_fail(**_kw):
            raise RuntimeError("ReferenceError: Data_netWorthTrend is not defined")

        provider.ak.fund_open_fund_info_em = v8_fail
        provider.client.nav_history = MagicMock(return_value=F10_RAW)

        rows = provider.nav_history("110022")
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["source"], "akshare.fallback.eastmoney.f10dataapi")
        provider.client.nav_history.assert_called_once()

    def test_syntax_error_triggers_fallback(self) -> None:
        provider = AkshareProvider(ak_module=MagicMock())

        def v8_syntax_error(**_kw):
            raise RuntimeError(
                "SyntaxError: Unexpected token '<'\n"
                "<!doctype html>\n<html>...5xx...</html>"
            )

        provider.ak.fund_open_fund_info_em = v8_syntax_error
        provider.client.nav_history = MagicMock(return_value=F10_RAW)

        rows = provider.nav_history("110022")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["source"], "akshare.fallback.eastmoney.f10dataapi")

    def test_fallback_falls_back_to_providererror_when_eastmoney_also_fails(self) -> None:
        provider = AkshareProvider(ak_module=MagicMock())

        def v8_fail(**_kw):
            raise RuntimeError("ReferenceError: Data_netWorthTrend is not defined")

        provider.ak.fund_open_fund_info_em = v8_fail
        provider.client.nav_history = MagicMock(
            side_effect=RuntimeError("F10DataApi.aspx 503")
        )

        with self.assertRaises(ProviderError) as ctx:
            provider.nav_history("110022")
        # The fallback re-raises the Eastmoney failure under
        # ProviderError so ``run_provider_chain`` can record it
        # in sync_failures with a useful prefix.
        self.assertIn("F10DataApi.aspx", str(ctx.exception))


class AkshareNavHistoryNonV8ExceptionTests(unittest.TestCase):
    """Only V8 eval failures fall through. A ``ValueError`` from
    inside the AkShare pandas pipeline (e.g. a numeric coercion
    failure) must keep the original exception -- the fallback is
    intentionally narrow to V8 noise."""

    def test_value_error_is_not_swallowed(self) -> None:
        provider = AkshareProvider(ak_module=MagicMock())

        def pandas_fail(**_kw):
            raise ValueError("akshare 内部 pandas 错: could not convert string to float")

        provider.ak.fund_open_fund_info_em = pandas_fail
        provider.client.nav_history = MagicMock(return_value=F10_RAW)

        with self.assertRaises(ValueError) as ctx:
            provider.nav_history("110022")
        self.assertIn("could not convert", str(ctx.exception))
        # The fallback path was not taken.
        provider.client.nav_history.assert_not_called()


class AkshareProviderClientOverrideTests(unittest.TestCase):
    """``AkshareProvider(ak_module=..., client=...)`` should
    accept an explicit ``FundDataClient`` so tests and the
    future "shared client" wiring can hand one in instead of
    paying for a default construction."""

    def test_default_client_is_a_fund_data_client(self) -> None:
        from fund_data.http import FundDataClient

        provider = AkshareProvider(ak_module=MagicMock())
        self.assertIsInstance(provider.client, FundDataClient)

    def test_explicit_client_is_preserved(self) -> None:
        from fund_data.http import FundDataClient

        sentinel = FundDataClient()
        provider = AkshareProvider(ak_module=MagicMock(), client=sentinel)
        self.assertIs(provider.client, sentinel)

    def test_ak_module_only_constructor_still_works(self) -> None:
        # Back-compat: callers that only pass ak_module= should
        # not be broken by the new client= kwarg.
        provider = AkshareProvider(ak_module=MagicMock())
        self.assertTrue(hasattr(provider, "client"))


class AkshareNavHistoryF10SampleTests(unittest.TestCase):
    """A real-shape F10DataApi.aspx body round-trips through
    the fallback. This is the integration check the
    smaller unit tests above build on."""

    def test_f10_body_yields_canonical_rows(self) -> None:
        provider = AkshareProvider(ak_module=MagicMock())

        def v8_fail(**_kw):
            raise RuntimeError("ReferenceError: Data_netWorthTrend is not defined")

        provider.ak.fund_open_fund_info_em = v8_fail
        provider.client.nav_history = MagicMock(return_value=F10_RAW)

        rows = provider.nav_history("110022", start_date="2024-01-01")
        # F10_RAW has two rows: 2024-01-01 and 2024-01-02.
        # The start_date filter is applied by the parser (not
        # by the V8 eval) so both rows pass the date filter.
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["nav_date"], "2024-01-02")  # sorted desc
        self.assertEqual(rows[1]["nav_date"], "2024-01-01")
        self.assertAlmostEqual(rows[0]["unit_nav"], 1.240)
        self.assertEqual(rows[0]["subscribe_status"], "开放")
        self.assertEqual(rows[0]["source"], "akshare.fallback.eastmoney.f10dataapi")


if __name__ == "__main__":
    unittest.main()
