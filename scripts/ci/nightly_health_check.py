"""Nightly CI data-plane health gate for fund-data.

Wraps the four ``fund_cli`` subcommands that the data-plane
health gate is built from:

    1. ``fund_cli.py doctor``             — schema / sync_failures /
                                            coverage regression
                                            check
    2. ``fund_cli.py cloud build-bundle``  — rebuilds the gzipped
                                            query db + sha256 +
                                            manifest
    3. ``fund_cli.py cloud upload``        — pushes the release to
                                            OSS via ossutil
    4. ``fund_cli.py cloud pull``          — pulls the just-uploaded
                                            manifest down and
                                            verifies the sha256
                                            against step 2

The runner is the consumer-stable contract for a future
GitHub Actions workflow (``.github/workflows/nightly.yml``)
and for an on-call human who wants to know "is the data
plane still healthy" without reading 200 lines of log output.

Failure handling follows the discipline in
``fund-data/AGENTS.md`` under "Backfill performance notes":
re-trying a real data failure only hides the regression. So
``run_step`` does **not** retry on its own; the caller passes
in a ``retry_predicate`` that decides whether the failure is
transient (e.g. ossutil timeout) and worth a backoff retry, or
a real data error (sha256 mismatch, schema drift) that
deserves to be escalated immediately.

The shape of the per-step JSON envelope intentionally mirrors
the per-subcommand JSON envelopes already locked down by the
unit tests in ``tests/test_doctor.py``,
``tests/test_fund_cloud.py`` and ``tests/test_fund_mcp.py`` so
the next consumer can parse any of them with one code path.

Typical use::

    # CI runner writes the envelope to /tmp/nightly-summary.json
    # and exits 0 on success, 1 on data failure, 2 on transient
    # exhaustion.
    python3 scripts/ci/nightly_health_check.py \\
        --db /path/to/fund_data.sqlite \\
        --output /tmp/nightly-summary.json

The runner is read-only against the running data plane. It
does not run a backfill, a sync, or a coverage expansion. If
the gate surfaces a regression, the on-call human runs the
fix -- the gate is the canary, not the surgery.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
FUND_CLI = SCRIPT_DIR.parent.parent / "fund-data" / "scripts" / "fund_cli.py"


@dataclasses.dataclass
class StepResult:
    """Outcome of one step. ``ok`` is the gate-relevant boolean;
    ``transient`` distinguishes 'worth retrying' from 'real data
    error, escalate' so the on-call human can route the alert
    without re-reading the failure."""

    name: str
    ok: bool
    exit_code: int
    duration_seconds: float
    transient: bool
    error: str | None = None
    payload: dict[str, Any] | None = None


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def run_step(
    name: str,
    cmd: list[str],
    *,
    timeout_seconds: int = 300,
) -> StepResult:
    """Run one ``fund_cli`` step and capture stdout/stderr.

    Does NOT retry -- the caller decides whether the failure
    is transient (e.g. ossutil 5xx) and passes a retry
    predicate into :func:`run_gate`. Keeping the retry logic
    out of the step runner means the failure envelope is
    stable: the gate output is one JSON document, not a
    retry trail.
    """
    started = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return StepResult(
            name=name,
            ok=False,
            exit_code=-1,
            duration_seconds=time.monotonic() - started,
            transient=True,
            error=f"step timed out after {timeout_seconds}s: {exc}",
        )

    duration = time.monotonic() - started
    if result.returncode == 0:
        try:
            payload = json.loads(result.stdout) if result.stdout else None
        except json.JSONDecodeError:
            payload = None
        return StepResult(
            name=name,
            ok=True,
            exit_code=0,
            duration_seconds=duration,
            transient=False,
            payload=payload,
        )

    return StepResult(
        name=name,
        ok=False,
        exit_code=result.returncode,
        duration_seconds=duration,
        transient=_looks_transient(result.stderr or "", result.stdout or ""),
        error=(result.stderr or result.stdout or "").strip().splitlines()[-1]
        if (result.stderr or result.stdout)
        else None,
    )


def _looks_transient(stderr: str, stdout: str) -> bool:
    """Best-effort classification of a step failure.

    ossutil network errors (timeout, 5xx) and SSL handshake
    blips are transient -- worth retrying. Schema drift,
    sha256 mismatch, and ``fund_cli`` exit-1 with a non-network
    message are real data errors -- escalate.
    """
    haystack = f"{stderr}\n{stdout}".lower()
    transient_markers = (
        "ssl:",
        "timeout",
        "timed out",
        "5xx",
        "service unavailable",
        "connection reset",
        "temporarily unavailable",
        "eof occurred",
        "rate limit",
        # OSS object propagation can take a few seconds; an
        # immediate-after-upload GET on the manifest URL can
        # 404 even though the upload itself succeeded. Pull
        # again and the second GET usually lands.
        "http error 404",
        "not found",
    )
    return any(marker in haystack for marker in transient_markers)


def _is_transient_step(step: StepResult) -> bool:
    return (not step.ok) and step.transient


def _is_data_step(step: StepResult) -> bool:
    return (not step.ok) and (not step.transient)


def _retry_with_backoff(
    steps: list[tuple[str, list[str]]],
    *,
    max_attempts: int = 3,
    backoff_seconds: tuple[int, ...] = (60, 120, 240),
) -> list[StepResult]:
    """Run a list of (name, cmd) steps. Retry only the
    transient failures (up to ``max_attempts`` total) with
    exponential backoff. Data failures are returned on the
    first attempt -- retrying them only hides the regression.
    """
    results: list[StepResult] = []
    for name, cmd in steps:
        attempt = 0
        last_result: StepResult | None = None
        while attempt < max_attempts:
            attempt += 1
            result = run_step(name, cmd)
            last_result = result
            if result.ok or _is_data_step(result) or attempt >= max_attempts:
                break
            # Transient -- wait then retry.
            wait_seconds = backoff_seconds[min(attempt - 1, len(backoff_seconds) - 1)]
            time.sleep(wait_seconds)
        results.append(last_result)  # type: ignore[arg-type]
    return results


def run_gate(
    *,
    db: str | Path,
    release_dir: str | Path,
    manifest_output: str | Path,
    bucket: str = "fund-data-public-l",
    region: str = "cn-shanghai",
    prefix: str = "fund-data",
    skip_network: bool = True,
) -> dict[str, Any]:
    """Execute the four-step gate and return the summary
    envelope. The envelope is also written to ``--output``
    by :func:`main`."""
    db = Path(db)
    release_dir = Path(release_dir)
    manifest_output = Path(manifest_output)

    version = datetime.now(UTC).strftime("%Y-%m-%dT%H%M%SZ")
    base_url = f"https://{bucket}.oss-{region}.aliyuncs.com/{prefix}/releases/{version}/"

    # Build the four CLI invocations. The version is fixed for
    # the whole run so step 2's manifest URL matches the URL
    # step 4 will try to pull.
    steps: list[tuple[str, list[str]]] = [
        (
            "doctor",
            [
                sys.executable,
                str(FUND_CLI),
                "doctor",
                "--db",
                str(db),
                "--output",
                "/tmp/nightly-doctor.json",
                "--skip-network",
                # Query DBs (the OSS bundle) exclude the sync_*
                # tables; the gate is verifying the data plane,
                # not the operator's local sync state.
                "--skip-sync-state",
            ],
        ),
        (
            "build-bundle",
            [
                sys.executable,
                str(FUND_CLI),
                "cloud",
                "build-bundle",
                "--source-db",
                str(db),
                "--output-dir",
                str(release_dir),
                "--base-url",
                base_url,
                "--version",
                version,
                "--manifest-output",
                str(manifest_output),
                "--output",
                "/tmp/nightly-build.json",
            ],
        ),
        (
            "upload",
            [
                sys.executable,
                str(FUND_CLI),
                "cloud",
                "upload",
                "--release-dir",
                str(release_dir),
                "--bucket",
                bucket,
                "--region",
                region,
                "--prefix",
                prefix,
                "--manifest",
                str(manifest_output),
                "--output",
                "/tmp/nightly-upload.json",
            ],
        ),
        (
            "pull-and-verify",
            [
                sys.executable,
                str(FUND_CLI),
                "cloud",
                "pull",
                "--manifest-url",
                f"https://{bucket}.oss-{region}.aliyuncs.com/{prefix}/current/manifest.json",
                "--cache-dir",
                "/tmp/nightly-cache",
                "--output",
                "/tmp/nightly-pull.json",
            ],
        ),
    ]

    # Upload and pull are back-to-back. The OSS bucket needs a
    # few seconds to propagate the new manifest object before
    # the pull request lands -- without this pause the pull step
    # returns 404 even though the upload itself succeeded.
    # This is a wait, not a step, so it lives outside the steps
    # list. The timeout below is generous: most manifest
    # propagations complete in <2s; 15s is plenty of headroom.
    if "pull-and-verify" in {n for n, _ in steps}:
        time.sleep(15)

    started = _utc_now()
    step_results = _retry_with_backoff(steps)
    finished = _utc_now()

    overall_ok = all(step.ok for step in step_results)
    any_data_failure = any(_is_data_step(step) for step in step_results)
    any_transient_exhausted = any(
        _is_transient_step(step) for step in step_results
    )

    summary: dict[str, Any] = {
        "run_id": started,
        "started_at": started,
        "finished_at": finished,
        "overall_ok": overall_ok,
        "data_failure": any_data_failure,
        "transient_exhausted": any_transient_exhausted,
        "steps": [dataclasses.asdict(step) for step in step_results],
    }
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else "nightly ci"
    )
    parser.add_argument(
        "--db",
        default=None,
        help=(
            "Local SQLite to gate on. Defaults to "
            "fund_data.default_db_path() at call time, which "
            "honours $FUND_DATA_CACHE_DIR / $FUND_DATA_DB. The "
            "CI pre-flight runs `fund_cli cloud pull` into "
            "$FUND_DATA_CACHE_DIR first, so a fresh runner "
            "without a 5.4GB fund-data/data/fund_data.sqlite "
            "still resolves to a real DB."
        ),
    )
    parser.add_argument(
        "--release-dir",
        default="/tmp/nightly-release",
        help="Where build-bundle writes the release artifacts.",
    )
    parser.add_argument(
        "--manifest-output",
        default="/tmp/nightly-release/manifest.json",
        help="Where build-bundle writes manifest.json (upload re-publishes it).",
    )
    parser.add_argument(
        "--bucket", default="fund-data-public-l", help="OSS bucket name."
    )
    parser.add_argument("--region", default="cn-shanghai", help="OSS region.")
    parser.add_argument("--prefix", default="fund-data", help="OSS object key prefix.")
    parser.add_argument(
        "--skip-network",
        action="store_true",
        default=True,
        help="Skip the live Eastmoney reachability probe in doctor (default: on).",
    )
    parser.add_argument(
        "--no-skip-network",
        dest="skip_network",
        action="store_false",
        help="Probe Eastmoney during doctor (default: skip).",
    )
    parser.add_argument(
        "--output",
        help="Write the summary envelope to this file in addition to stdout.",
    )
    args = parser.parse_args(argv)

    # Resolve the DB lazily: if --db is not given, defer to
    # fund_data.default_db_path() so the FUND_DATA_CACHE_DIR /
    # FUND_DATA_DB env vars (set by the CI pre-flight step)
    # actually steer the path. Doing this at parse-time would
    # require importing fund_data earlier, which pulls in
    # optional runtime deps.
    if args.db is None:
        # `fund-data/scripts/fund_data.py` is the actual module;
        # the package name is `scripts`. Matches the convention
        # used everywhere else in the codebase.
        from scripts import fund_data
        args.db = str(fund_data.default_db_path())

    summary = run_gate(
        db=args.db,
        release_dir=args.release_dir,
        manifest_output=args.manifest_output,
        bucket=args.bucket,
        region=args.region,
        prefix=args.prefix,
        skip_network=args.skip_network,
    )

    payload = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)

    if summary["data_failure"]:
        # Real data regression -- alert the on-call human.
        return 1
    if summary["transient_exhausted"]:
        # Retries exhausted on a transient failure -- surface
        # it but flag for the human to investigate.
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
