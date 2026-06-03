"""Provider facade: provider id constants, the auto-chain builder,
and re-exports of the four provider classes.

Lifted from ``fund_data.py`` in the 0.3.0 split (RFC
``docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md``).
A single import surface so the rest of the project
(``fund_data.search_funds`` / ``fund_data.batch_sync_funds``
/ the CLI) does not need to know which provider it is
calling.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("fund_data")

# Provider id strings (also re-exported from fund_data.paths
# because the resolver layer needs them too). We re-import
# here so ``from fund_data.providers import PROVIDER_AUTO``
# works as a standalone access path.
from ..paths import (
    PROVIDER_AKSHARE,
    PROVIDER_AUTO,
    PROVIDER_EASTMONEY,
    PROVIDER_INVESTODAY,
    PROVIDER_TUSHARE,
)

from .akshare import AkshareProvider
from .base import ProviderError, ProviderResult, run_provider_chain
from .eastmoney import EastmoneyProvider
from .investoday import InvestodayProvider
from .tushare import TushareProvider, _tushare_period

__all__ = [
    # provider ids
    "PROVIDER_AUTO",
    "PROVIDER_EASTMONEY",
    "PROVIDER_AKSHARE",
    "PROVIDER_INVESTODAY",
    "PROVIDER_TUSHARE",
    # base
    "ProviderError",
    "ProviderResult",
    "run_provider_chain",
    # providers
    "AkshareProvider",
    "EastmoneyProvider",
    "InvestodayProvider",
    "TushareProvider",
    # helpers
    "_tushare_period",
    "build_providers",
    "build_providers_full",
]


def build_providers(provider: str, *, capability: str | None = None) -> list[Any]:
    """Build the provider chain for a capability."""
    providers, init_warnings = build_providers_full(provider, capability=capability)
    for message in init_warnings:
        logger.warning(message)
    return providers


def build_providers_full(
    provider: str, *, capability: str | None = None
) -> tuple[list[Any], list[str]]:
    """Build the provider chain and return the init warnings as well.

    Returns a tuple of (providers, init_warnings). ``init_warnings`` is a
    list of human-readable strings, one per skipped provider, in the order
    they were tried. It is empty in non-auto mode or when every provider
    initialized successfully.
    """
    names: list[str]
    if provider == PROVIDER_AUTO:
        # Provider chain order is *capability-aware*: AkShare is
        # the biggest dataset for stock/bond/industry/fee/etc.
        # holdings, so it has to be the PRIMARY (not the
        # fallback) -- otherwise an empty Investoday response
        # would short-circuit the chain and we'd never see the
        # AkShare rows that the bulk backfill actually wants.
        # For NAV / snapshot / search / fund_list the order
        # stays Eastmoney-first (those are Eastmoney's domain).
        names = []
        if capability in {
            "stock_holdings",
            "profile",
            "bond_holdings",
            "industry_allocations",
            "fee_structures",
            "dividends",
            "splits",
            "fund_managers",
        }:
            names.append(PROVIDER_AKSHARE)
        else:
            names.append(PROVIDER_EASTMONEY)
        if os.environ.get("INVESTODAY_API_KEY") or os.environ.get("INVESTDATA_API_KEY"):
            names.append(PROVIDER_INVESTODAY)
        if os.environ.get("TUSHARE_TOKEN"):
            names.append(PROVIDER_TUSHARE)
        if capability in {
            "stock_holdings",
            "profile",
            "bond_holdings",
            "industry_allocations",
            "fee_structures",
            "dividends",
            "splits",
            "fund_managers",
        }:
            # Eastmoney is the last-resort fallback; it has
            # methods for stock/snapshot/search/fund_list but
            # not for the AkShare-only domains. The chain's
            # ``getattr`` AttributeError on the missing
            # ``bond_holdings`` / ``industry_allocations``
            # methods gets logged as a per-call failure and the
            # caller sees "all providers failed" -- not a
            # crash, just an empty result.
            names.append(PROVIDER_EASTMONEY)
        else:
            names.append(PROVIDER_AKSHARE)
    else:
        names = [provider]

    providers: list[Any] = []
    init_warnings: list[str] = []
    for name in names:
        try:
            if name == PROVIDER_EASTMONEY:
                providers.append(EastmoneyProvider())
            elif name == PROVIDER_AKSHARE:
                providers.append(AkshareProvider())
            elif name == PROVIDER_INVESTODAY:
                providers.append(InvestodayProvider())
            elif name == PROVIDER_TUSHARE:
                providers.append(TushareProvider())
            else:
                raise ProviderError(f"unknown provider: {name}")
        except ProviderError as exc:
            if provider != PROVIDER_AUTO:
                raise
            message = f"{name} unavailable for {capability or 'request'}: {exc}"
            init_warnings.append(message)
    if not providers:
        raise ProviderError(f"no providers available for {capability or 'request'}")
    return providers, init_warnings

