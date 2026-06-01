"""Test-suite bootstrap.

Pin ``FUND_DATA_DB`` to a process-unique tmp file before any test
imports :mod:`fund_data`. This is belt-and-suspenders: every test
already passes an explicit ``db_path`` to
:meth:`fund_data.FundDataStore`, but a regression in a future test
that forgets the argument would otherwise fall through to
:func:`fund_data.default_db_path`, which resolves to the production
data base or the cloud bundle cache (``~/.cache/fund-data/...``).

``unittest discover`` only imports modules matching its ``test*.py``
pattern by default, so a sibling ``conftest.py`` would never be
executed. We pin the env var here, in the package ``__init__``,
which unittest discover does load.

``setdefault`` is not enough: a shell that exports
``FUND_DATA_DB=`` (empty string) still has the key set, so
``setdefault`` skips it and the empty string would then propagate
through ``default_db_path``. Use a manual ``if not ...`` check so
the pin always wins.
"""

from __future__ import annotations

import os
import tempfile

_TEST_DB = os.path.join(
    tempfile.gettempdir(),
    f"fund_data_test_{os.getpid()}.sqlite",
)
if not os.environ.get("FUND_DATA_DB"):
    os.environ["FUND_DATA_DB"] = _TEST_DB
