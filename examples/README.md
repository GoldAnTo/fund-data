"""fund-data — runnable examples

Each example is a standalone Python script that assumes only:

* `python3 -m pip install -e ".[dev]"` has been run from the repo root,
* the SQLite data base is reachable (defaults to
  `fund-data/data/fund_data.sqlite`).

The examples are intentionally short. They are not a tutorial — read
`fund-data/SKILL.md` for the design story. The point of this folder
is to give a new contributor (or an agent) a one-screen reference
for the three most common flows.

| Script | Flow |
|---|---|
| `coverage_report.py` | Run the coverage report and print the Markdown to stdout. |
| `watchlist_sync.py`  | Read a watchlist file, sync a small batch through `batch_sync_funds`. |
| `json_export.py`     | Export the `funds` table to JSON Lines for downstream tooling. |
"""
