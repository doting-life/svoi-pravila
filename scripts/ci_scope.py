#!/usr/bin/env python3
"""Map changed paths to CI jobs and whether the image job must Trivy-scan."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_JOBS = (
    "backend",
    "miniapp",
    "secrets",
    "image",
    "stack-smoke",
    "prod-smoke",
    "ownership-guard",
)

DOCS_JOBS = frozenset({"secrets", "ownership-guard"})
BACKEND_JOBS = frozenset({"backend", "secrets", "image", "stack-smoke"})
MINIAPP_JOBS = frozenset({"miniapp", "secrets", "image", "stack-smoke"})

IMAGE_LOCKFILES = frozenset({"backend/uv.lock", "miniapp/pnpm-lock.yaml"})
IMAGE_PINS = frozenset({"scripts/image-pins.env", "ops/grafana/image-pins.env"})


@dataclass(frozen=True)
class ScopeDecision:
    """Jobs to run and whether image-scan is required."""

    jobs: frozenset[str]
    image_scan: bool


def all_jobs() -> tuple[str, ...]:
    """CI_JOBS from the environment, or the Makefile default list."""
    raw = os.environ.get("CI_JOBS", "").strip()
    if not raw:
        return DEFAULT_JOBS
    return tuple(raw.split())


def is_docs_path(path: str) -> bool:
    """True when the path is documentation / CTO-owned docs only."""
    if path == "AGENTS.md":
        return True
    if path.startswith("docs/"):
        return True
    if path.startswith(".cursor/"):
        return True
    return path.endswith(".md")


def is_image_input(path: str) -> bool:
    """True when changing the path should force a Trivy image scan."""
    name = Path(path).name
    if name == "Dockerfile" or name.startswith("Dockerfile."):
        return True
    if path in IMAGE_LOCKFILES or path in IMAGE_PINS:
        return True
    return False


def force_full_suite(
    *,
    github_event_name: str | None = None,
    github_ref: str | None = None,
    push_ref: str | None = None,
) -> bool:
    """Master pushes and the schedule always run every CI job (and scan)."""
    event = github_event_name if github_event_name is not None else os.environ.get("GITHUB_EVENT_NAME")
    if event == "schedule":
        return True
    ref = github_ref if github_ref is not None else os.environ.get("GITHUB_REF")
    if ref == "refs/heads/master":
        return True
    pushed = push_ref if push_ref is not None else os.environ.get("PUSH_REF")
    if pushed in {"refs/heads/master", "master"}:
        return True
    return False


def jobs_for_paths(paths: list[str]) -> frozenset[str]:
    """Return the union of jobs required by the given changed paths."""
    if not paths:
        return frozenset(all_jobs())
    jobs: set[str] = set()
    saw_docs = False
    saw_backend = False
    saw_miniapp = False
    for path in paths:
        if path.startswith("backend/"):
            saw_backend = True
            jobs |= BACKEND_JOBS
            continue
        if path.startswith("miniapp/"):
            saw_miniapp = True
            jobs |= MINIAPP_JOBS
            continue
        if is_docs_path(path):
            saw_docs = True
            jobs |= DOCS_JOBS
            continue
        return frozenset(all_jobs())
    if saw_docs and not saw_backend and not saw_miniapp:
        # Docs-only when every path is a docs path (already enforced by loop).
        return frozenset(DOCS_JOBS)
    if not jobs:
        return frozenset(all_jobs())
    return frozenset(jobs)


def decide(
    paths: list[str],
    *,
    force_all: bool | None = None,
) -> ScopeDecision:
    """Compute scope for the given paths (or force the full suite)."""
    if force_all is None:
        force_all = force_full_suite()
    if force_all:
        return ScopeDecision(jobs=frozenset(all_jobs()), image_scan=True)
    jobs = jobs_for_paths(paths)
    scan = any(is_image_input(path) for path in paths)
    return ScopeDecision(jobs=jobs, image_scan=scan)


def _run_git(args: list[str], *, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip() or "git failed"
        raise RuntimeError(msg)
    return result.stdout


def ensure_origin_master(repo: Path) -> None:
    """Fetch origin/master when the ref is missing (shallow GitHub clones)."""
    probe = subprocess.run(
        ["git", "rev-parse", "--verify", "origin/master"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if probe.returncode == 0:
        return
    fetch = subprocess.run(
        ["git", "fetch", "--no-tags", "origin", "master:refs/remotes/origin/master"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if fetch.returncode != 0:
        msg = fetch.stderr.strip() or "git fetch origin master failed"
        raise RuntimeError(msg)


def changed_paths(repo: Path | None = None) -> list[str]:
    """Paths changed between origin/master merge-base and HEAD."""
    root = repo if repo is not None else ROOT
    ensure_origin_master(root)
    base = _run_git(["merge-base", "HEAD", "origin/master"], cwd=root).strip()
    if not base:
        raise RuntimeError("empty merge-base with origin/master")
    diff = _run_git(["diff", "--name-only", f"{base}...HEAD"], cwd=root)
    return [line for line in diff.splitlines() if line]


def main(argv: list[str] | None = None) -> int:
    """CLI for ci.sh: --check-job, --image-scan, or print jobs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-job",
        metavar="JOB",
        help="exit 0 if JOB is in scope, 3 if skipped",
    )
    parser.add_argument(
        "--image-scan",
        action="store_true",
        help="exit 0 if image-scan is required, 3 if build-only",
    )
    parser.add_argument(
        "--print-jobs",
        action="store_true",
        help="print space-separated jobs in scope",
    )
    args = parser.parse_args(argv)

    try:
        decision = decide(changed_paths())
    except RuntimeError as exc:
        print(f"ci_scope: {exc}", file=sys.stderr)
        return 1

    if args.check_job is not None:
        if args.check_job in decision.jobs:
            return 0
        print(f"ci: {args.check_job} skipped (scope)")
        return 3

    if args.image_scan:
        if decision.image_scan:
            return 0
        print("ci: image scan skipped (scope)")
        return 3

    if args.print_jobs:
        print(" ".join(sorted(decision.jobs, key=list(all_jobs()).index)))
        return 0

    parser.error("pass --check-job, --image-scan, or --print-jobs")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
