"""Assert GitHub CI YAML matches the single make-ci job list."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci.yml"
ACTION_PATH = ROOT / ".github" / "actions" / "toolchain" / "action.yml"
MAKEFILE_PATH = ROOT / "Makefile"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CI_JOBS_RE = re.compile(r"^CI_JOBS\s*:?=\s*(.+)$", re.MULTILINE)


def job_list_from_makefile(text: str) -> list[str]:
    """Return the single CI job list declared in the Makefile."""
    match = CI_JOBS_RE.search(text)
    if match is None:
        raise ValueError("CI_JOBS is missing from the Makefile")
    return match.group(1).split()


def _uses_parts(value: str) -> tuple[str, str]:
    if "@" not in value:
        return value, ""
    name, pin = value.rsplit("@", 1)
    return name, pin


def check_workflow(
    workflow: dict[str, Any],
    action: dict[str, Any],
    jobs: list[str],
) -> list[str]:
    """Return human-readable violations of the CI parity contract."""
    errors: list[str] = []
    env = workflow.get("env")
    if isinstance(env, dict):
        sp_keys = [key for key in env if str(key).startswith("SP_")]
        if sp_keys:
            errors.append(f"workflow env must not set SP_* keys: {sp_keys}")

    declared = workflow.get("jobs")
    if not isinstance(declared, dict):
        return ["workflow has no jobs"]
    names = list(declared.keys())
    if set(names) != set(jobs):
        errors.append(f"job set {names} does not equal {jobs}")

    for name, job in declared.items():
        if not isinstance(job, dict):
            errors.append(f"job {name} is not a mapping")
            continue
        if "services" in job:
            errors.append(f"job {name} must not declare services")
        job_env = job.get("env")
        if isinstance(job_env, dict):
            sp_keys = [key for key in job_env if str(key).startswith("SP_")]
            if sp_keys:
                errors.append(f"job {name} env must not set SP_* keys: {sp_keys}")
        steps = job.get("steps")
        if not isinstance(steps, list):
            errors.append(f"job {name} has no steps")
            continue
        for step in steps:
            if not isinstance(step, dict):
                errors.append(f"job {name} has a non-mapping step")
                continue
            step_env = step.get("env")
            if isinstance(step_env, dict):
                sp_keys = [key for key in step_env if str(key).startswith("SP_")]
                if sp_keys:
                    errors.append(f"job {name} step env must not set SP_* keys: {sp_keys}")
            if "run" in step:
                run = str(step["run"]).strip()
                expected = f"make ci JOB={name}"
                if run != expected:
                    errors.append(f"job {name} run {run!r} is not {expected!r}")
            if "uses" in step:
                uses = str(step["uses"])
                action_name, pin = _uses_parts(uses)
                if uses.startswith("./"):
                    if uses != "./.github/actions/toolchain":
                        errors.append(f"job {name} local uses {uses!r} is not the toolchain action")
                    continue
                if not SHA_RE.fullmatch(pin):
                    errors.append(f"job {name} uses {uses!r} is not SHA-pinned")
                if action_name != "actions/checkout":
                    errors.append(
                        f"job {name} uses {uses!r}; only checkout or the toolchain action are allowed"
                    )

    errors.extend(_check_toolchain_action(action))
    return errors


def _check_toolchain_action(action: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    runs = action.get("runs")
    if not isinstance(runs, dict):
        return ["toolchain action has no runs"]
    steps = runs.get("steps")
    if not isinstance(steps, list):
        return ["toolchain action has no steps"]
    saw_uv = False
    saw_node = False
    for step in steps:
        if not isinstance(step, dict) or "uses" not in step:
            errors.append("toolchain action steps must be SHA-pinned uses")
            continue
        uses = str(step["uses"])
        action_name, pin = _uses_parts(uses)
        if not SHA_RE.fullmatch(pin):
            errors.append(f"toolchain action uses {uses!r} is not SHA-pinned")
        with_ = step.get("with") if isinstance(step.get("with"), dict) else {}
        if action_name == "astral-sh/setup-uv":
            saw_uv = True
            version_file = str(with_.get("version-file", ""))
            if version_file != "backend/pyproject.toml":
                errors.append(
                    f"setup-uv version-file must be backend/pyproject.toml, found {version_file!r}"
                )
            if "version" in with_ and str(with_["version"]).strip():
                errors.append("setup-uv must not set a version literal")
        elif action_name == "actions/setup-node":
            saw_node = True
            node_file = str(with_.get("node-version-file", ""))
            if node_file != "miniapp/.nvmrc":
                errors.append(
                    f"setup-node node-version-file must be miniapp/.nvmrc, found {node_file!r}"
                )
            if "node-version" in with_ and str(with_["node-version"]).strip():
                errors.append("setup-node must not set a node-version literal")
        else:
            errors.append(f"toolchain action uses unexpected action {uses!r}")
    if not saw_uv:
        errors.append("toolchain action must install uv from backend/pyproject.toml")
    if not saw_node:
        errors.append("toolchain action must install Node from miniapp/.nvmrc")
    return errors


def main() -> int:
    jobs = job_list_from_makefile(MAKEFILE_PATH.read_text())
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text())
    action = yaml.safe_load(ACTION_PATH.read_text())
    if not isinstance(workflow, dict) or not isinstance(action, dict):
        print("ci.yml and toolchain action.yml must be mappings", file=sys.stderr)
        return 1
    errors = check_workflow(workflow, action, jobs)
    if errors:
        for item in errors:
            print(item, file=sys.stderr)
        return 1
    print("ci-parity-check: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
