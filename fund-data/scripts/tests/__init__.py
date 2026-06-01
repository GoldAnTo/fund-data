"""Test-suite bootstrap.

Pin ``FUND_DATA_DB`` to a process-unique tmp file before any test
imports :mod:`fund_data`, **but only when no cloud bundle is
configured**. ``fund_data.default_db_path()`` short-circuits on
``FUND_DATA_DB`` before consulting the cloud bundle, so an
unconditional pin here would defeat
``test_fund_cloud.test_pull_bundle_downloads_...``, which
asserts that ``default_db_path()`` honours
``FUND_DATA_CACHE_DIR``.

This is belt-and-suspenders: every test already passes an
explicit ``db_path`` to :meth:`fund_data.FundDataStore`, but a
regression in a future test that forgets the argument would
otherwise fall through to :func:`fund_data.default_db_path`,
which resolves to the production data base or the cloud
bundle cache (``~/.cache/fund-data/...``).

``unittest discover`` only imports modules matching its
``test*.py`` pattern by default, so a sibling ``conftest.py``
would never be executed. We pin the env var here, in the
package ``__init__``, which unittest discover does load.

The ``if not ...`` check (rather than ``setdefault``) handles
the case where a shell exports ``FUND_DATA_DB=`` (empty
string) — an empty string would still have the key set, and
``default_db_path`` would propagate that empty string to
``Path("")``, raising later. We also skip the pin when
``FUND_DATA_CACHE_DIR`` is set, so a developer who has wired
up the cloud bundle keeps getting the bundled query DB.
"""

from __future__ import annotations

import os
import tempfile

_TEST_DB = os.path.join(
    tempfile.gettempdir(),
    f"fund_data_test_{os.getpid()}.sqlite",
)
if (
    not os.environ.get("FUND_DATA_DB")
    and not os.environ.get("FUND_DATA_CACHE_DIR")
):
    os.environ["FUND_DATA_DB"] = _TEST_DB
