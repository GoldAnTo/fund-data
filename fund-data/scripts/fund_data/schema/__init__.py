"""Schema subpackage.

Lifted from ``fund_data.py`` in the 0.3.0 split (RFC
``docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md``).
Currently only contains :mod:`.migrations`; later PRs in the
0.3.0 series will add ``DDL`` (the canonical table
definitions consumed by ``FundDataStore.ensure_schema``)
once the store itself moves out of the package root.
"""

from __future__ import annotations

from .migrations import (
    FUND_DATA_SCHEMA_VERSION,
    MIGRATIONS,
    _migration_001_add_industry_allocations_market_value,
    _migration_002_add_fee_structures_fee_text,
    _migration_003_add_fee_structures_discount_fee,
    _migration_004_add_fee_structures_discount_fee_text,
    _migration_005_align_column_order,
)

__all__ = [
    "FUND_DATA_SCHEMA_VERSION",
    "MIGRATIONS",
    "_migration_001_add_industry_allocations_market_value",
    "_migration_002_add_fee_structures_fee_text",
    "_migration_003_add_fee_structures_discount_fee",
    "_migration_004_add_fee_structures_discount_fee_text",
    "_migration_005_align_column_order",
]
