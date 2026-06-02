"""Provider base: ``ProviderError``, ``ProviderResult``, and the
``run_provider_chain`` helper.

Lifted from ``fund_data.py`` in the 0.3.0 split (RFC
``docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md``).
Every concrete provider (EastmoneyProvider, AkshareProvider,
InvestodayProvider, TushareProvider) and the
``build_providers`` facade raise ``ProviderError`` to signal
"this provider cannot satisfy the request" and the chain
builder walks the providers in order, recording every
failure in the per-call result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["ProviderError", "ProviderResult", "run_provider_chain"]


class ProviderError(RuntimeError):
    pass


@dataclass
class ProviderResult:
    provider: str
    rows: Any
    failures: list[dict[str, str]]


def run_provider_chain(
    providers: list[Any], method_name: str, *args: Any, allow_empty: bool = False, **kwargs: Any
) -> ProviderResult:
    failures: list[dict[str, str]] = []
    for provider in providers:
        try:
            method = getattr(provider, method_name)
            rows = method(*args, **kwargs)
            if rows is None or (rows == [] and not allow_empty):
                raise ProviderError("provider returned no rows")
            return ProviderResult(provider=provider.name, rows=rows, failures=failures)
        except Exception as exc:
            failures.append({"provider": provider.name, "error": str(exc)})
    failure_text = "; ".join(f"{item['provider']}: {item['error']}" for item in failures)
    raise ProviderError(f"all providers failed for {method_name}: {failure_text}")


