"""Unit tests for ``scripts/_net_compat.py``.

The patch is small but the regression surface is wide -- a wrong
``setdefault`` in the sqlite wrapper would silently drop the
``timeout`` argument on real db files, and a missed re-import could
let macOS happy-eyeballs sneak an IPv6 SYN through and re-hang the
whole diagnostic pipeline.  These tests lock down:

1. ``apply()`` is idempotent.
2. ``socket.getaddrinfo`` is replaced with a filter that drops
   IPv6 answers (or returns the original list if every answer is
   IPv6, so the caller still sees a real "could not resolve" error
   path).
3. ``sqlite3.connect`` is monkey-patched so the default
   ``timeout`` is 30 s.  The patch must also still pass through
   any explicit ``timeout=`` (or any other keyword) the caller
   set.
4. The proxy env vars are stripped after ``apply()`` returns --
   *not* before, so a test that checks them at module import time
   still works.
"""

from __future__ import annotations

import importlib
import os
import socket
import sqlite3
import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import _net_compat  # noqa: E402


class _IdempotencyTests(unittest.TestCase):
    """``apply()`` must be a no-op on the second call so module
    re-imports do not re-bind ``socket.getaddrinfo`` (which would
    wrap the previously-wrapped function)."""

    def setUp(self) -> None:
        # Force a clean state -- other tests in the same process
        # may have already called ``apply()`` at import time.
        _net_compat._APPLIED = False
        self._saved_getaddrinfo = socket.getaddrinfo
        self._saved_sqlite_connect = sqlite3.connect

    def tearDown(self) -> None:
        # Restore the unpatched state regardless of what the test
        # did, so the rest of the suite sees a clean slate.
        socket.getaddrinfo = self._saved_getaddrinfo
        sqlite3.connect = self._saved_sqlite_connect
        _net_compat._APPLIED = False

    def test_apply_is_idempotent(self) -> None:
        _net_compat.apply()
        first_getaddrinfo = socket.getaddrinfo
        first_sqlite = sqlite3.connect
        _net_compat.apply()
        # Same callable object -- not a wrapper around a wrapper.
        self.assertIs(socket.getaddrinfo, first_getaddrinfo)
        self.assertIs(sqlite3.connect, first_sqlite)


class _GetaddrinfoFilterTests(unittest.TestCase):
    """The IPv4-only filter is the whole point of this module on
    macOS happy-eyeballs + IPv4-only servers."""

    def setUp(self) -> None:
        self._saved = socket.getaddrinfo

    def tearDown(self) -> None:
        socket.getaddrinfo = self._saved

    def test_filter_drops_ipv6_results(self) -> None:
        # Build a fake result set with both AF_INET6 and AF_INET
        # answers (the shape ``getaddrinfo`` returns is a tuple of
        # ``(family, type, proto, canonname, sockaddr)``).
        AF_INET6 = socket.AF_INET6
        AF_INET = socket.AF_INET
        SOCK_STREAM = socket.SOCK_STREAM
        fake = [
            (AF_INET6, SOCK_STREAM, 6, "", ("::1", 80)),
            (AF_INET, SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
            (AF_INET6, SOCK_STREAM, 6, "", ("fe80::1", 80)),
        ]

        def fake_orig(host, port, *args, **kwargs):  # noqa: ANN001
            return list(fake)

        socket.getaddrinfo = _net_compat._orig_getaddrinfo  # restore for safety
        _net_compat._orig_getaddrinfo = fake_orig
        try:
            results = _net_compat._ipv4_only_getaddrinfo("anywhere", 80)
        finally:
            _net_compat._orig_getaddrinfo = socket.getaddrinfo
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], AF_INET)
        self.assertEqual(results[0][4], ("127.0.0.1", 80))

    def test_filter_falls_back_when_only_ipv6_available(self) -> None:
        # If every answer is IPv6 we still want the caller to see
        # the original list -- an empty list would hide the
        # ``getaddrinfo`` failure with a confusing "no results"
        # error.
        AF_INET6 = socket.AF_INET6
        SOCK_STREAM = socket.SOCK_STREAM
        fake = [
            (AF_INET6, SOCK_STREAM, 6, "", ("::1", 80)),
        ]

        def fake_orig(host, port, *args, **kwargs):  # noqa: ANN001
            return list(fake)

        saved = _net_compat._orig_getaddrinfo
        _net_compat._orig_getaddrinfo = fake_orig
        try:
            results = _net_compat._ipv4_only_getaddrinfo("anywhere", 80)
        finally:
            _net_compat._orig_getaddrinfo = saved
        self.assertEqual(results, fake)


class _SqliteConnectTests(unittest.TestCase):
    """The sqlite3 monkey-patch. Locks down the default-timeout
    behaviour and the explicit-override behaviour."""

    def setUp(self) -> None:
        self._saved = sqlite3.connect
        _net_compat._APPLIED = False

    def tearDown(self) -> None:
        sqlite3.connect = self._saved
        _net_compat._APPLIED = False

    def test_apply_sets_default_timeout_to_30_seconds(self) -> None:
        _net_compat.apply()
        # Use :memory: so we do not touch a real db file.  We
        # cannot directly read the patched function's default, but
        # we can verify it via the call: the connection object
        # exposes its timeout via ``conn.getattr(...)`` -- actually
        # no, the timeout is a *connection-time* parameter.  So
        # we round-trip it through a query that takes a write
        # lock to force the timeout to kick in.
        conn = sqlite3.connect(":memory:")
        try:
            cur = conn.execute("PRAGMA busy_timeout")
            self.assertEqual(cur.fetchone()[0], 30000)
        finally:
            conn.close()

    def test_apply_passes_through_explicit_timeout(self) -> None:
        _net_compat.apply()
        conn = sqlite3.connect(":memory:", timeout=5.0)
        try:
            cur = conn.execute("PRAGMA busy_timeout")
            # The caller explicitly set 5 s -- our setdefault(30)
            # must not override it.
            self.assertEqual(cur.fetchone()[0], 5000)
        finally:
            conn.close()

    def test_sqlite_connect_helper_wraps_with_30s_default(self) -> None:
        # The explicit helper is a no-op once ``apply()`` has
        # monkey-patched the builtin, but it must still work in
        # isolation (e.g. for unit tests that import
        # ``_net_compat`` without calling ``apply()``).
        _net_compat._APPLIED = False
        sqlite3.connect = self._saved  # un-patch
        conn = _net_compat.sqlite_connect(":memory:")
        try:
            cur = conn.execute("PRAGMA busy_timeout")
            self.assertEqual(cur.fetchone()[0], 30000)
        finally:
            conn.close()


class _ProxyEnvStripTests(unittest.TestCase):
    """``apply()`` strips the env-var layer of the proxy stack.  We
    do not test ``scutil`` or third-party app launchd injection --
    those are OS-level and out of scope for a unit test."""

    def setUp(self) -> None:
        self._saved = {var: os.environ.get(var) for var in _net_compat._PROXY_ENV_VARS}
        for var in _net_compat._PROXY_ENV_VARS:
            os.environ[var] = "http://127.0.0.1:7897"
        _net_compat._APPLIED = False

    def tearDown(self) -> None:
        for var, value in self._saved.items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value
        _net_compat._APPLIED = False

    def test_apply_strips_all_proxy_env_vars(self) -> None:
        _net_compat.apply()
        for var in _net_compat._PROXY_ENV_VARS:
            self.assertNotIn(var, os.environ, f"{var} should be stripped after apply()")


if __name__ == "__main__":
    unittest.main()
