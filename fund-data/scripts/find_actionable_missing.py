"""Find actionable-missing fund_codes per dataset.

Actionable missing = funds whose DB row count for the dataset
is zero AND whose fund_type does not structurally expect that
dataset to be empty (per the EXPECTED_EMPTY matrix in
:mod:`coverage_report`, which is sourced from
``docs/fund-data-inventory.md`` §9.2).

This is the inverse of :func:`coverage_report._is_structural_empty`:
the same matrix decides what is *not* actionable, and the rest
of the "present=False" rows are.

Typical use on CI::

    python fund-data/scripts/find_actionable_missing.py \
        --dataset stock_holdings --output /tmp/missing-stock.txt
    fund_cli batch-sync --codes-file /tmp/missing-stock.txt \
        --include-holdings --provider akshare --concurrency 8
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# Reuse the single source of truth for fund_type -> structural-empty
# datasets. coverage_report keeps it in lockstep with the inventory
# doc; this script reads it instead of duplicating the matrix.
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from coverage_report import EXPECTED_EMPTY  # type: ignore[import-not-found]


# Map CLI-facing dataset name -> (db_table, include_flag).
# The include_flag is the corresponding `fund_cli sync` /
# `fund_cli batch-sync` flag that fetches this dataset.
DATASETS: dict[str, tuple[str, str]] = {
    "nav": ("nav_history", ""),                # batch-sync fetches nav by default
    "profile": ("fund_profiles", "--include-profile"),
    "holdings": ("stock_holdings", "--include-holdings"),
    "bonds": ("bond_holdings", "--include-bonds"),
    "industries": ("industry_allocations", "--include-industries"),
    "fees": ("fee_structures", "--include-fees"),
    "dividends": ("dividends", "--include-distributions"),
    "splits": ("splits", "--include-distributions"),
}


def _is_structural_empty(fund_type: str | None, dataset: str) -> bool:
    """Mirror :func:`coverage_report._is_structural_empty` for the
    CLI usage here. Unknown / blank fund_type is treated as
    "not expected empty" (everything is actionable).

    The DB sometimes stores "Reits" (capital R) where the
    EXPECTED_EMPTY key is "REITs" (all caps); compare case-
    insensitively to match both spellings.
    """
    if not fund_type:
        return False
    ft_lower = fund_type.lower()
    for prefix, datasets in EXPECTED_EMPTY.items():
        if ft_lower.startswith(prefix.lower()) and dataset in datasets:
            return True
    return False


def find_codes(dataset: str, db_path: str) -> list[str]:
    """Return fund_codes whose ``dataset`` is actionable missing.

    Raises :class:`ValueError` for unknown dataset names.
    """
    if dataset not in DATASETS:
        raise ValueError(
            f"unknown dataset {dataset!r}; choose from {sorted(DATASETS)}"
        )
    table, _flag = DATASETS[dataset]
    con = sqlite3.connect(db_path)
    # Step 1: all funds (code, fund_type).
    funds = con.execute("SELECT fund_code, fund_type FROM funds").fetchall()
    # Step 2: existing rows for this dataset, batched into a set.
    has_it = {
        r[0]
        for r in con.execute(
            f"SELECT DISTINCT fund_code FROM {table}"  # noqa: S608 (table from allow-list)
        ).fetchall()
    }
    out: list[str] = []
    for code, ftype in funds:
        if _is_structural_empty(ftype, dataset):
            continue
        if code in has_it:
            continue
        out.append(code)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dataset",
        required=True,
        choices=sorted(DATASETS),
        help="Dataset to enumerate actionable-missing fund codes for.",
    )
    parser.add_argument(
        "--db",
        default="fund-data/data/fund_data.sqlite",
        help="SQLite path. Defaults to the local full DB.",
    )
    parser.add_argument(
        "--output",
        help="Write the codes to this file (one per line). "
        "If omitted, writes to stdout.",
    )
    args = parser.parse_args(argv)

    codes = find_codes(args.dataset, args.db)
    payload = "\n".join(codes) + ("\n" if codes else "")
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
        print(
            f"dataset={args.dataset} actionable_missing={len(codes)} "
            f"codes -> {args.output}",
            file=sys.stderr,
        )
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
