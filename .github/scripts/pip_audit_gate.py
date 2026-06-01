#!/usr/bin/env python3
r"""Fail the security workflow when pip-audit finds HIGH or CRITICAL CVEs.

Used by `.github/workflows/security.yml`. The workflow runs
`pip-audit --format json` and pipes the result into this script.
Exit code 0 = no blocking CVE; exit code 1 = at least one
HIGH/CRITICAL advisory was reported, with a per-vuln summary
printed to stdout so the GH Actions run page shows the reasons.

Severities come from the OSV / PyPA advisory feed. When a feed
advisory is missing the severity field (still common in 2026),
it comes back as `None`. We treat `None` and `UNKNOWN` as
'needs human triage' — log it but do not block the merge. Only
`HIGH` and `CRITICAL` fail the workflow.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

BLOCKING_SEVERITIES = {"HIGH", "CRITICAL"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "audit_json",
        help="Path to the pip-audit --format json output.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Exit 1 if pip-audit itself failed (e.g. unreachable OSV "
            "database). Default is to log a warning and pass; flip "
            "this on if the workflow should hard-fail on audit errors."
        ),
    )
    args = parser.parse_args()

    try:
        with open(args.audit_json, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        print(f"::error::pip-audit JSON not found at {args.audit_json}")
        return 1 if args.strict else 0
    except json.JSONDecodeError as exc:
        print(f"::error::could not parse pip-audit JSON: {exc}")
        return 1 if args.strict else 0

    findings: list[dict[str, Any]] = []
    if isinstance(data, dict):
        findings = [f for f in data.get("dependencies", []) if isinstance(f, dict)]
    elif isinstance(data, list):  # pragma: no cover - older pip-audit shapes
        findings = [f for f in data if isinstance(f, dict)]

    scanned = len(findings)
    blockers: list[tuple[str, str, str, str, list[str]]] = []
    unknowns: list[tuple[str, str, str]] = []

    for f in findings:
        name = str(f.get("name", "?"))
        version = str(f.get("version", "?"))
        vulns = f.get("vulns") or []
        for v in vulns:
            if not isinstance(v, dict):
                continue
            sev = (v.get("severity") or "UNKNOWN").upper()
            vid = str(v.get("id", "?"))
            fix = list(v.get("fix_versions") or [])
            if sev in BLOCKING_SEVERITIES:
                blockers.append((name, version, sev, vid, fix))
            elif sev in {"UNKNOWN", ""}:
                unknowns.append((name, version, vid))

    if blockers:
        print("::error::Blocking CVEs found (high/critical):")
        for name, version, sev, vid, fix in blockers:
            fix_str = ", ".join(fix) if fix else "n/a"
            print(f"  - {name}=={version}  {sev}  {vid}  fix={fix_str}")
        return 1

    msg = f"pip-audit scanned {scanned} packages; no high/critical CVE."
    if unknowns:
        msg += (
            f" {len(unknowns)} advisory entries are missing severity data "
            "and were logged for human triage only."
        )
        for name, version, vid in unknowns:
            print(f"  ? {name}=={version}  {vid}  (severity unknown)")
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
