"""Smoke test for the test-suite bootstrap in ``tests/__init__.py``.

The bootstrap pins ``FUND_DATA_DB`` to a process-unique tmp file
so :func:`fund_data.default_db_path` never falls through to the
production data base or the cloud bundle cache. We re-run it in a
subprocess with a known empty ``FUND_DATA_DB=`` and verify the
pin took effect.

A subprocess is required because the bootstrap mutates
``os.environ`` at import time and we cannot undo that mid-process
without polluting sibling tests in the same run.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]


class BootstrapTests(unittest.TestCase):
    def test_bootstrap_pins_fund_data_db_when_empty(self) -> None:
        """With ``FUND_DATA_DB=`` (empty) in the env, importing the
        test package should overwrite the env with a tmp file path
        so :func:`fund_data.default_db_path` never falls through
        to the production data base or the cloud bundle cache."""
        env = {**os.environ, "FUND_DATA_DB": ""}
        # Strip PYTHONPATH so the subprocess uses a clean sys.path
        # matching what ``python3 -m unittest discover`` would see.
        env.pop("PYTHONPATH", None)
        code = (
            "import os, sys; "
            "sys.path.insert(0, 'scripts'); "
            "sys.path.insert(0, 'scripts/tests'); "
            "import scripts.tests; "
            "import fund_data; "
            "print(os.environ.get('FUND_DATA_DB', '')); "
            "print(fund_data.default_db_path())"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(SCRIPT_DIR.parent),
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 2, f"unexpected output: {result.stdout!r}")
        fund_data_db, default_path = lines
        self.assertTrue(
            fund_data_db.startswith(tempfile.gettempdir()),
            f"expected FUND_DATA_DB under {tempfile.gettempdir()!r}, got {fund_data_db!r}",
        )
        self.assertIn(
            "fund_data_test_",
            fund_data_db,
            f"expected process-pid suffix in {fund_data_db!r}",
        )
        self.assertEqual(fund_data_db, str(default_path))

    def test_bootstrap_does_not_overwrite_explicit_fund_data_db(self) -> None:
        """If the operator pins ``FUND_DATA_DB`` to a real value, the
        bootstrap must respect it — not silently rewrite the path."""
        custom = "/tmp/explicit_test_db.sqlite"
        env = {**os.environ, "FUND_DATA_DB": custom}
        env.pop("PYTHONPATH", None)
        code = (
            "import os, sys; "
            "sys.path.insert(0, 'scripts'); "
            "sys.path.insert(0, 'scripts/tests'); "
            "import scripts.tests; "
            "print(os.environ.get('FUND_DATA_DB', ''))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(SCRIPT_DIR.parent),
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), custom)


if __name__ == "__main__":
    unittest.main()
