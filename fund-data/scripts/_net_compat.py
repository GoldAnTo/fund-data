"""Network + SQLite compatibility shim for the long-running fund-data scripts.

Two foot-guns show up on macOS that the rest of the project code
does not handle on its own:

1. **Three layers of proxy.**  macOS injects ``http(s)_proxy`` /
   ``all_proxy`` env vars via launchd and the system proxy settings,
   and third-party apps (Clash Verge, Surge, Charles) listen on
   7897.  AkShare uses ``requests`` (not ``urllib``) so the
   ``urllib.request.getproxies = lambda: {}`` patch in
   ``refresh_fund_type.py`` does not propagate, but clearing the env
   vars *does* take effect for every HTTP client.  ``scutil --proxy``
   is left alone -- changing it would affect the user's daily
   network.

2. **macOS happy-eyeballs + IPv4-only servers.**  Eastmoney's
   ``fund.eastmoney.com`` only returns A records (no AAAA) for some
   CDN edges, and macOS ``getaddrinfo`` (RFC 6724) prefers IPv6.
   The IPv6 SYN never completes and the IPv4 SYN never gets sent,
   so the process looks like it is hung at 0 % CPU.  Filtering to
   ``AF_INET`` in ``socket.getaddrinfo`` forces the lookup to
   return only the IPv4 answer.

Both patches are no-ops on Linux / Windows runners, and idempotent
across re-imports.  ``apply()`` runs them once and stores a flag so
subsequent calls are cheap.

Typical use (at the top of a long-running script)::

    from _net_compat import apply as apply_net_compat
    apply_net_compat()
    # ... rest of imports

Why not put this in :mod:`fund_data`?  Importing ``fund_data``
should be cheap and side-effect-free -- the unit tests construct
``FundDataStore`` many times per run and do not need a network or
DNS round-trip.  Keeping the patch in a separate module means the
scripts that *do* make network calls (doctor / coverage / backfill)
opt in explicitly.
"""

from __future__ import annotations

import os
import socket

_APPLIED = False

_PROXY_ENV_VARS = (
    "http_proxy",
    "https_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "all_proxy",
    "ALL_PROXY",
)

# Hold the original ``getaddrinfo`` so we can filter the result
# without re-defining the function.  Storing on a module attribute
# is enough -- ``socket.getaddrinfo`` is a CPython builtin attribute
# that is rebound on assignment, not patched in place.
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_only_getaddrinfo(host, port, *args, **kwargs):  # noqa: ANN001
    """Filter ``getaddrinfo`` results to ``AF_INET`` only.

    Falls back to the original (empty) result if every answer is
    IPv6 -- this preserves the "could not resolve" error path so
    callers see a clear exception instead of a silent empty
    answer.
    """
    results = _orig_getaddrinfo(host, port, *args, **kwargs)
    ipv4 = [r for r in results if r[0] == socket.AF_INET]
    return ipv4 if ipv4 else results


def apply() -> None:
    """Apply the macOS proxy + IPv4 + sqlite-timeout patches in-place.  Idempotent."""
    global _APPLIED
    if _APPLIED:
        return

    # 1. Strip the env-var layer of the proxy stack.  We do *not*
    # touch ``scutil --proxy`` or third-party app launchd injection
    # -- both would change the user's daily network.
    for var in _PROXY_ENV_VARS:
        os.environ.pop(var, None)

    # 2. Force ``socket.getaddrinfo`` to skip IPv6 answers.  This
    # patches the CPython attribute, not the underlying C
    # function, so it is trivially reversible on interpreter
    # shutdown.
    socket.getaddrinfo = _ipv4_only_getaddrinfo

    # 3. Default the ``sqlite3.connect`` ``timeout`` to 30 s.  The
    # long-running bulk passes (backfill, fund_profile_backfill,
    # akshare_capability_backfill) hold the writer lock for a few
    # hundred milliseconds per batch.  Without this, the diagnostic
    # scripts (doctor / coverage / backfill_list_missing) that
    # share the same db file would see ``OperationalError:
    # database is locked`` after the default 5 s timeout.  We
    # monkey-patch the builtin so call-sites do not need to opt
    # in explicitly -- this is the same project convention as
    # :meth:`fund_data.FundDataStore.connect`.
    import sqlite3 as _sqlite3

    _orig_sqlite_connect = _sqlite3.connect

    def _patched_connect(*args, **kwargs):  # noqa: ANN001
        kwargs.setdefault("timeout", 30.0)
        return _orig_sqlite_connect(*args, **kwargs)

    _sqlite3.connect = _patched_connect

    _APPLIED = True


def sqlite_connect(*args, timeout: float = 30.0, **kwargs):  # noqa: ANN001
    """Convenience wrapper around :func:`sqlite3.connect` with a
    default 30 s ``timeout``.

    Long-running bulk passes (backfill, fund_profile_backfill,
    akshare_capability_backfill) hold the writer lock for a few
    hundred milliseconds per batch.  Doctor / coverage /
    backfill_list_missing can run concurrently and would otherwise
    see ``OperationalError: database is locked`` after the default
    5 s timeout.  Bumping to 30 s is what
    :meth:`fund_data.FundDataStore.connect` already does for the
    store-managed connections, so this matches the project
    convention.
    """
    import sqlite3  # local import -- this module is imported before sqlite3 may be ready in some runners

    return sqlite3.connect(*args, timeout=timeout, **kwargs)


__all__ = ("apply", "sqlite_connect")
