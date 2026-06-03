"""Unit tests for ``scripts/fund_data/providers/``.

Lifted out of the package-level test bundle during the 0.3.0
split (RFC ``docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md``).
This file pins the four-provider facade (constant
identifiers, ``build_providers`` capability matrix, and the
auto-chain fallback contract) and the base error /
result types. The four provider classes are tested by
their own existing test files (``test_fund_data``,
``test_tushare``, ``test_investoday``); this file focuses
on the facade.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from fund_data import paths  # noqa: E402
from fund_data import providers  # noqa: E402


class ProviderIdConstantsTests(unittest.TestCase):
    """The provider id strings are the agent / CLI contract.
    ``fund_cli --provider <X>`` and ``build_providers(X)``
    must keep matching; pinning the strings here is
    cheap insurance against a typo that would silently
    fall back to the Eastmoney-only path."""

    def test_auto_constant(self) -> None:
        self.assertEqual(paths.PROVIDER_AUTO, "auto")
        self.assertEqual(providers.PROVIDER_AUTO, "auto")

    def test_eastmoney_constant(self) -> None:
        self.assertEqual(paths.PROVIDER_EASTMONEY, "eastmoney")
        self.assertEqual(providers.PROVIDER_EASTMONEY, "eastmoney")

    def test_akshare_constant(self) -> None:
        self.assertEqual(paths.PROVIDER_AKSHARE, "akshare")
        self.assertEqual(providers.PROVIDER_AKSHARE, "akshare")

    def test_investoday_constant(self) -> None:
        self.assertEqual(paths.PROVIDER_INVESTODAY, "investoday")
        self.assertEqual(providers.PROVIDER_INVESTODAY, "investoday")

    def test_tushare_constant(self) -> None:
        self.assertEqual(paths.PROVIDER_TUSHARE, "tushare")
        self.assertEqual(providers.PROVIDER_TUSHARE, "tushare")


class ProviderNameAttributesTests(unittest.TestCase):
    """Each provider class exposes ``name = <id string>``
    so the auto-chain can attribute rows / failures to the
    concrete provider that produced them. Pin the strings
    so a rename does not silently desync the attribute
    from the constant."""

    def test_eastmoney_name(self) -> None:
        self.assertEqual(providers.EastmoneyProvider.name, "eastmoney")

    def test_akshare_name(self) -> None:
        self.assertEqual(providers.AkshareProvider.name, "akshare")

    def test_investoday_name(self) -> None:
        self.assertEqual(providers.InvestodayProvider.name, "investoday")

    def test_tushare_name(self) -> None:
        self.assertEqual(providers.TushareProvider.name, "tushare")


class BuildProvidersCapabilityMatrixTests(unittest.TestCase):
    """``build_providers(name, capability=...)`` decides which
    providers to instantiate for a given capability. The
    matrix here is the single source of truth for the
    "which provider handles which capability" answer; if
    a provider gains a new capability, the matrix must
    be updated and the test below updated to match."""

    def test_eastmoney_handles_search_fund_list_nav_snapshot(self) -> None:
        # Eastmoney covers the four no-key endpoints; the
        # other capabilities raise :class:`ProviderError`
        # so the chain falls through to AkShare / Tushare.
        for capability in ("search", "fund_list", "nav_history", "snapshot"):
            with self.subTest(capability=capability):
                chain = providers.build_providers("eastmoney", capability=capability)
                names = [p.name for p in chain]
                self.assertIn("eastmoney", names)

    def test_auto_returns_at_least_eastmoney_for_search(self) -> None:
        # ``auto`` for a capability Eastmoney handles always
        # includes Eastmoney (no other no-key provider
        # covers search / fund_list / nav_history /
        # snapshot).
        chain = providers.build_providers("auto", capability="search")
        names = [p.name for p in chain]
        self.assertIn("eastmoney", names)

    def test_auto_prepends_investoday_when_canonical_key_is_set(self) -> None:
        # ``INVESTODAY_API_KEY`` is the canonical documented name.
        # The legacy ``INVESTDATA_API_KEY`` must not be required for
        # auto mode to try Investoday first.
        saved = {
            "INVESTODAY_API_KEY": os.environ.get("INVESTODAY_API_KEY"),
            "INVESTDATA_API_KEY": os.environ.get("INVESTDATA_API_KEY"),
        }
        try:
            os.environ["INVESTODAY_API_KEY"] = "canonical-test-key"
            os.environ.pop("INVESTDATA_API_KEY", None)
            chain = providers.build_providers("auto", capability="profile")
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        names = [p.name for p in chain]
        self.assertEqual(names[0], "investoday")

    def test_explicit_unknown_provider_raises(self) -> None:
        # An unknown provider id is a hard error, not a
        # silent fallback. The chain builder raises
        # :class:`ProviderError` so a typo in ``--provider``
        # is caught at startup rather than at first call.
        with self.assertRaises(providers.ProviderError):
            providers.build_providers("nonexistent")


class ProviderErrorIsRuntimeErrorTests(unittest.TestCase):
    """The auto-chain distinguishes "this provider cannot
    do this capability" (:class:`ProviderError`) from
    anything else. Pin that it is a :class:`RuntimeError`
    so ``except Exception`` callers keep catching it."""

    def test_subclass_of_runtime_error(self) -> None:
        self.assertTrue(issubclass(providers.ProviderError, RuntimeError))


class ProviderResultShapeTests(unittest.TestCase):
    def test_dataclass_fields(self) -> None:
        # ``ProviderResult`` is the per-call return value
        # of ``run_provider_chain``. Its three fields are
        # the agent contract for "which provider produced
        # these rows" / "which providers failed first".
        import dataclasses
        fields = {f.name for f in dataclasses.fields(providers.ProviderResult)}
        self.assertEqual(fields, {"provider", "rows", "failures"})


if __name__ == "__main__":
    unittest.main()
