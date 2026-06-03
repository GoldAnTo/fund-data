"""Unit tests for ``scripts/fund_data/_env.py``.

The loader is the contract between the project-root ``.env``
and the entry-point scripts. Its job is small but the
edges matter:

- A missing ``.env`` is a no-op, not a crash (CI / fresh
  clones do not have one).
- ``os.environ.setdefault`` semantics: shell exports and
  pre-set env vars always win over the file.
- Quoted values, ``export`` prefixes, comments, blank
  lines, and inline comments are handled.
- The search order is documented; pinning it stops a future
  refactor from silently changing which file is read when
  the project is embedded as a library.

The tests do NOT call ``load_env()`` against the real
project-root ``.env`` — every test passes an explicit
``path=`` to keep the test environment hermetic and to
avoid leaking the developer's paid-provider key into
assertions.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys_path_insert = SCRIPT_DIR  # for the import below
import sys

if str(sys_path_insert) not in sys.path:
    sys.path.insert(0, str(sys_path_insert))

from fund_data import _env  # noqa: E402


class ParseLineTests(unittest.TestCase):
    """Pin the line grammar. This is the surface the
    ``.env.example`` template documents; if it ever changes
    the doc needs to change with it, and a test failure is
    the cheapest way to make the link visible."""

    def test_simple_key_value(self) -> None:
        self.assertEqual(_env.parse_line("FOO=bar"), ("FOO", "bar"))

    def test_value_with_surrounding_spaces(self) -> None:
        self.assertEqual(_env.parse_line("FOO = bar baz"), ("FOO", "bar baz"))

    def test_double_quoted_value(self) -> None:
        self.assertEqual(_env.parse_line('FOO="bar baz"'), ("FOO", "bar baz"))

    def test_single_quoted_value(self) -> None:
        self.assertEqual(_env.parse_line("FOO='bar baz'"), ("FOO", "bar baz"))

    def test_export_prefix(self) -> None:
        # Allows the same file to be both `source`'d and
        # consumed by Python. Note the lack of quotes — the
        # value is the literal `bar baz`, not the string
        # `"bar baz"`.
        self.assertEqual(_env.parse_line("export FOO=bar baz"), ("FOO", "bar baz"))

    def test_export_prefix_with_quotes(self) -> None:
        self.assertEqual(
            _env.parse_line('export FOO="bar baz"'),
            ("FOO", "bar baz"),
        )

    def test_blank_line_returns_none(self) -> None:
        self.assertIsNone(_env.parse_line(""))
        self.assertIsNone(_env.parse_line("   "))

    def test_comment_returns_none(self) -> None:
        self.assertIsNone(_env.parse_line("# this is a comment"))
        self.assertIsNone(_env.parse_line("  # indented comment"))

    def test_no_equals_returns_none(self) -> None:
        # `FOO` without `=` is not a valid key=value line.
        self.assertIsNone(_env.parse_line("FOO"))

    def test_empty_key_returns_none(self) -> None:
        # `=value` would be ambiguous; reject.
        self.assertIsNone(_env.parse_line("=value"))

    def test_empty_value_is_empty_string(self) -> None:
        # `FOO=` is a valid (and useful) declaration.
        self.assertEqual(_env.parse_line("FOO="), ("FOO", ""))

    def test_unmatched_quote_kept_verbatim(self) -> None:
        # We only strip *matching* quote pairs. An unmatched
        # `"` is part of the value.
        self.assertEqual(
            _env.parse_line('FOO="unterminated'),
            ("FOO", '"unterminated'),
        )


class LoadEnvTests(unittest.TestCase):
    """End-to-end tests against a temp ``.env`` file. The
    test never reads the real project-root ``.env`` — it
    always passes an explicit ``path=`` so the developer's
    paid key never leaks into the test process."""

    def setUp(self) -> None:
        # Save and clear every env var the loader might
        # write. Tests start from a known-clean slate.
        self._watched = ("FOO", "BAR", "BAZ", "INVESTODAY_API_KEY", "TUSHARE_TOKEN")
        self._saved: dict[str, str | None] = {
            k: os.environ.get(k) for k in self._watched
        }
        for k in self._watched:
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        for k in self._watched:
            os.environ.pop(k, None)
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v

    def test_loads_simple_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("FOO=alpha\nBAR=beta\n", encoding="utf-8")
            loaded = _env.load_env(path=path)
        self.assertEqual(loaded, {"FOO": "alpha", "BAR": "beta"})
        self.assertEqual(os.environ["FOO"], "alpha")
        self.assertEqual(os.environ["BAR"], "beta")

    def test_skips_comments_and_blanks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "# top-level comment\n"
                "\n"
                "FOO=alpha\n"
                "  # indented comment\n"
                "BAR=beta\n"
                "\n",
                encoding="utf-8",
            )
            loaded = _env.load_env(path=path)
        self.assertEqual(loaded, {"FOO": "alpha", "BAR": "beta"})

    def test_existing_env_var_wins(self) -> None:
        # ``os.environ.setdefault`` semantics: the loader
        # never overwrites a value the shell or the parent
        # process already set. This is what makes
        # ``INVESTODAY_API_KEY=... python3 fund_cli.py ...``
        # keep working.
        os.environ["FOO"] = "from-shell"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("FOO=from-file\n", encoding="utf-8")
            loaded = _env.load_env(path=path)
        self.assertEqual(loaded, {})
        self.assertEqual(os.environ["FOO"], "from-shell")

    def test_missing_file_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loaded = _env.load_env(path=Path(tmp) / "does-not-exist")
        self.assertEqual(loaded, {})

    def test_invalid_lines_are_silently_skipped(self) -> None:
        # ``FOO`` (no `=`) is malformed; the loader should
        # skip it and keep going rather than aborting.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("FOO=alpha\nGARBAGE_LINE\nBAR=beta\n", encoding="utf-8")
            loaded = _env.load_env(path=path)
        self.assertEqual(loaded, {"FOO": "alpha", "BAR": "beta"})

    def test_strips_matching_quote_pair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                'FOO="alpha beta"\n'
                "BAR='gamma delta'\n",
                encoding="utf-8",
            )
            loaded = _env.load_env(path=path)
        self.assertEqual(
            loaded,
            {"FOO": "alpha beta", "BAR": "gamma delta"},
        )

    def test_handles_export_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("export FOO=alpha\nexport BAR=beta\n", encoding="utf-8")
            loaded = _env.load_env(path=path)
        self.assertEqual(loaded, {"FOO": "alpha", "BAR": "beta"})

    def test_empty_value(self) -> None:
        # `FOO=` is a valid declaration; the value is the
        # empty string. Useful for optional toggles like
        # `FUND_DATA_AUTO_PULL=`.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("FOO=\n", encoding="utf-8")
            loaded = _env.load_env(path=path)
        self.assertEqual(loaded, {"FOO": ""})
        self.assertEqual(os.environ["FOO"], "")


class LoadEnvSearchOrderTests(unittest.TestCase):
    """Pin the search order documented in ``fund_data/_env.py``.

    The order is the contract that lets an operator
    override the file (via ``$FUND_DATA_ENV_FILE``),
    shadow it with a per-directory ``.env`` (cwd), or
    rely on the package's own parent walk (editable
    install case). A future refactor must not silently
    change which file wins."""

    def test_explicit_path_wins_over_cwd(self) -> None:
        # Operator sets ``$FUND_DATA_ENV_FILE`` to point at a
        # shared / mounted / CI-workspace secrets file. The
        # cwd ``.env`` (if any) must be ignored.
        with tempfile.TemporaryDirectory() as explicit_dir, tempfile.TemporaryDirectory() as cwd_dir:
            explicit = Path(explicit_dir) / "shared.env"
            explicit.write_text("FOO=from-explicit\n", encoding="utf-8")
            cwd_env = Path(cwd_dir) / ".env"
            cwd_env.write_text("FOO=from-cwd\n", encoding="utf-8")
            os.environ["FUND_DATA_ENV_FILE"] = str(explicit)
            try:
                loaded = _env.load_env()
            finally:
                os.environ.pop("FUND_DATA_ENV_FILE", None)
        self.assertEqual(loaded["FOO"], "from-explicit")

    def test_cwd_dotenv_used_when_no_override(self) -> None:
        # No ``$FUND_DATA_ENV_FILE`` — the cwd ``.env`` is
        # the operator's "I am running the script from
        # *this* project" signal. Pin that it gets picked
        # up via the cwd scan, not via the parent walk.
        with tempfile.TemporaryDirectory() as tmp:
            cwd_env = Path(tmp) / ".env"
            cwd_env.write_text("FOO=from-cwd\n", encoding="utf-8")
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                loaded = _env.load_env()
            finally:
                os.chdir(old_cwd)
                os.environ.pop("FOO", None)
        self.assertEqual(loaded["FOO"], "from-cwd")


if __name__ == "__main__":
    unittest.main()
