"""Test-suite bootstrap.

Pin ``FUND_DATA_DB`` to a process-unique tmp file before any test
imports :mod:`fund_data`. This is belt-and-suspenders: every test
already passes an explicit ``db_path`` to
:meth:`fund_data.FundDataStore`, but a regression in a future test
that forgets the argument would otherwise fall through to
:func:`fund_data.default_db_path`, which resolves to the production
data base or the cloud bundle cache (``~/.cache/fund-data/...``).

The unittest discovery path (``python3 -m unittest discover
scripts/tests``) imports every module it finds in this directory,
so a module named ``conftest.py`` is just a side-effect module —
not a pytest hook file. Placing the env var assignment at the top
of this package ``__init__`` would also work, but a dedicated file
makes the intent explicit and keeps the test runner portable.
"""
from __future__ import annotations

import os
import tempfile

_TEST_DB = os.path.join(
    tempfile.gettempdir(),
    f"fund_data_test_{os.getpid()}.sqlite",
)
os.environ.setdefault("FUND_DATA_DB", _TEST_DB)
