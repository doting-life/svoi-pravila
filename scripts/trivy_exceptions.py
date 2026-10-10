#!/usr/bin/env python3
"""Load and apply timed Trivy exceptions for internal third-party images."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXCEPTIONS = ROOT / "ops" / "trivy-exceptions.yaml"

ALLOWED_IMAGES = frozenset({"grafana", "prometheus"})
FORBIDDEN_IMAGES = frozenset({"api", "miniapp", "backup"})
MAX_EXCEPTION_DAYS = 30


@dataclass(frozen=True)
class ExceptionEntry:
    """One CVE exception for a third-party internal image."""

    cve: str
    image: str
    statement: str
    expires: date


class ExceptionError(ValueError):
    """Invalid or expired exception file content."""


def _parse_date(raw: object) -> date:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        return date.fromisoformat(raw)
    msg = f"expires must be an ISO date, got {raw!r}"
    raise ExceptionError(msg)


def load_exceptions(
    path: Path,
    *,
    today: date | None = None,
) -> tuple[ExceptionEntry, ...]:
    """Load exceptions; fail on forbidden images, bad dates, or expired rows."""
    day = today if today is not None else date.today()
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if data is None:
        return ()
    if not isinstance(data, dict):
        raise ExceptionError("exceptions root must be a mapping")
    rows = data.get("exceptions")
    if rows is None:
        return ()
    if not isinstance(rows, list):
        raise ExceptionError("exceptions must be a list")
    out: list[ExceptionEntry] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ExceptionError(f"exceptions[{index}] must be a mapping")
        cve = row.get("cve")
        image = row.get("image")
        statement = row.get("statement")
        expires_raw = row.get("expires")
        if not isinstance(cve, str) or not cve.strip():
            raise ExceptionError(f"exceptions[{index}].cve is required")
        if not isinstance(image, str) or not image.strip():
            raise ExceptionError(f"exceptions[{index}].image is required")
        if not isinstance(statement, str) or not statement.strip():
            raise ExceptionError(f"exceptions[{index}].statement is required")
        if image in FORBIDDEN_IMAGES:
            raise ExceptionError(
                f"exceptions[{index}]: image {image!r} cannot have exceptions "
                f"(only {sorted(ALLOWED_IMAGES)})"
            )
        if image not in ALLOWED_IMAGES:
            raise ExceptionError(
                f"exceptions[{index}]: image {image!r} is not allowed "
                f"(only {sorted(ALLOWED_IMAGES)})"
            )
        expires = _parse_date(expires_raw)
        if expires < day:
            raise ExceptionError(
                f"exceptions[{index}]: {cve} for {image} expired on {expires.isoformat()}"
            )
        if expires > day + timedelta(days=MAX_EXCEPTION_DAYS):
            raise ExceptionError(
                f"exceptions[{index}]: {cve} expires more than {MAX_EXCEPTION_DAYS} days out"
            )
        out.append(
            ExceptionEntry(
                cve=cve.strip(),
                image=image.strip(),
                statement=statement.strip(),
                expires=expires,
            )
        )
    return tuple(out)


def ignored_cves(entries: tuple[ExceptionEntry, ...], image: str) -> frozenset[str]:
    """CVE ids ignored for the logical image name."""
    return frozenset(entry.cve for entry in entries if entry.image == image)


def finding_ids(report: dict[str, Any]) -> list[str]:
    """Collect VulnerabilityID values from a Trivy JSON image report."""
    ids: list[str] = []
    results = report.get("Results")
    if not isinstance(results, list):
        return ids
    for result in results:
        if not isinstance(result, dict):
            continue
        vulns = result.get("Vulnerabilities")
        if not isinstance(vulns, list):
            continue
        for vuln in vulns:
            if not isinstance(vuln, dict):
                continue
            vid = vuln.get("VulnerabilityID")
            if isinstance(vid, str) and vid:
                ids.append(vid)
    return ids


def remaining_findings(
    report: dict[str, Any],
    *,
    image: str,
    entries: tuple[ExceptionEntry, ...],
) -> list[str]:
    """Return CVE ids that are not covered by an exception for this image."""
    ignored = ignored_cves(entries, image)
    return [cve for cve in finding_ids(report) if cve not in ignored]


def main(argv: list[str] | None = None) -> int:
    """Validate exceptions and/or filter a Trivy JSON report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--exceptions",
        type=Path,
        default=DEFAULT_EXCEPTIONS,
        help="path to ops/trivy-exceptions.yaml",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="only validate the exceptions file",
    )
    parser.add_argument(
        "--image",
        choices=sorted(ALLOWED_IMAGES | FORBIDDEN_IMAGES),
        help="logical image for filtering a Trivy JSON report on stdin",
    )
    args = parser.parse_args(argv)

    try:
        entries = load_exceptions(args.exceptions)
    except ExceptionError as exc:
        print(f"trivy-exceptions: {exc}", file=sys.stderr)
        return 1

    if args.validate:
        print(f"trivy-exceptions: ok ({len(entries)} entries)")
        return 0

    if args.image is None:
        parser.error("--image is required unless --validate")
        return 2

    if args.image in FORBIDDEN_IMAGES:
        # Still load to catch forbidden rows; then reject any ignore attempt.
        report = json.load(sys.stdin)
        leftover = finding_ids(report if isinstance(report, dict) else {})
        if leftover:
            print(
                f"trivy-exceptions: {args.image} has findings and cannot use exceptions: "
                f"{', '.join(leftover)}",
                file=sys.stderr,
            )
            return 1
        return 0

    raw = json.load(sys.stdin)
    if not isinstance(raw, dict):
        print("trivy-exceptions: stdin must be a Trivy JSON object", file=sys.stderr)
        return 1
    leftover = remaining_findings(raw, image=args.image, entries=entries)
    if leftover:
        print(
            f"trivy-exceptions: unexcepted findings for {args.image}: {', '.join(leftover)}",
            file=sys.stderr,
        )
        return 1
    print(f"trivy-exceptions: {args.image} clean after exceptions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
