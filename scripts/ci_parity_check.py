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
CI_SH_PATH = ROOT / "scripts" / "ci.sh"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CI_JOBS_RE = re.compile(r"^CI_JOBS\s*:?=\s*(.+)$", re.MULTILINE)
JOB_FN_RE = re.compile(r"^job_([a-z0-9_]+)\s*\(\)", re.MULTILINE)
INTERNAL_JOB_FNS = frozenset({"workflow", "toolchain"})
PUBLISH_JOB = "publish"
PUBLISH_IF = "github.event_name == 'push' && github.ref == 'refs/heads/master'"
PUBLISH_NEEDS = frozenset(
    {"backend", "miniapp", "secrets", "image", "stack-smoke", "prod-smoke"},
)
PUBLISH_PERMISSIONS = {"packages": "write", "contents": "read"}


def job_list_from_makefile(text: str) -> list[str]:
    """Return the single CI job list declared in the Makefile."""
    match = CI_JOBS_RE.search(text)
    if match is None:
        raise ValueError("CI_JOBS is missing from the Makefile")
    return match.group(1).split()


def job_functions_from_ci_sh(text: str) -> set[str]:
    """Return `job_*` function names from ci.sh without the `job_` prefix."""
    return set(JOB_FN_RE.findall(text))


def check_job_functions(ci_sh: str, jobs: list[str]) -> list[str]:
    """Return mismatches between CI_JOBS names and job_* functions in ci.sh."""
    errors: list[str] = []
    found = job_functions_from_ci_sh(ci_sh)
    expected = {name.replace("-", "_") for name in jobs}
    for name in jobs:
        fn = name.replace("-", "_")
        if fn not in found:
            errors.append(f"ci.sh missing job_{fn} for {name}")
    if PUBLISH_JOB not in found:
        errors.append(f"ci.sh missing job_{PUBLISH_JOB}")
    extras = found - expected - INTERNAL_JOB_FNS - {PUBLISH_JOB}
    if extras:
        extra_names = ", ".join(sorted(f"job_{item}" for item in extras))
        errors.append(f"ci.sh has unexpected job functions: {extra_names}")
    return errors


_BANNED_PM = "core" + "pack"


def check_banned_pm_bootstrap(named_texts: dict[str, str]) -> list[str]:
    """Fail when any scanned file still invokes the Node package-manager shim."""
    errors: list[str] = []
    for label, text in named_texts.items():
        if _BANNED_PM in text:
            errors.append(f"{label} must not invoke {_BANNED_PM}")
    return errors


def banned_pm_texts_from_tree(root: Path) -> dict[str, str]:
    """Load Makefile, scripts, hooks, and GitHub YAML for the bootstrap ban."""
    texts: dict[str, str] = {}
    makefile = root / "Makefile"
    if makefile.is_file():
        texts[str(makefile.relative_to(root))] = makefile.read_text()
    for directory in (root / "scripts", root / ".githooks", root / ".github"):
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            texts[str(path.relative_to(root))] = path.read_text()
    return texts


def _uses_parts(value: str) -> tuple[str, str]:
    if "@" not in value:
        return value, ""
    name, pin = value.rsplit("@", 1)
    return name, pin


def _has_packages_write(permissions: object) -> bool:
    return isinstance(permissions, dict) and permissions.get("packages") == "write"


def _check_publish_job(job: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    condition = " ".join(str(job.get("if", "")).split())
    if condition != PUBLISH_IF:
        errors.append(f"job {PUBLISH_JOB} if {condition!r} is not {PUBLISH_IF!r}")
    needs = job.get("needs")
    needs_set = {str(item) for item in needs} if isinstance(needs, list) else set()
    if needs_set != PUBLISH_NEEDS:
        errors.append(
            f"job {PUBLISH_JOB} needs {sorted(needs_set)} must equal {sorted(PUBLISH_NEEDS)}"
        )
    permissions = job.get("permissions")
    if permissions != PUBLISH_PERMISSIONS:
        errors.append(
            f"job {PUBLISH_JOB} permissions {permissions!r} must equal {PUBLISH_PERMISSIONS!r}"
        )
    return errors


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
    if PUBLISH_JOB in jobs:
        errors.append(f"{PUBLISH_JOB} must not be listed in CI_JOBS (make ci never publishes)")
    expected_names = {*jobs, PUBLISH_JOB}
    if set(names) != expected_names:
        errors.append(f"job set {names} does not equal {sorted(expected_names)}")
    if _has_packages_write(workflow.get("permissions")):
        errors.append("workflow-level permissions must not grant packages: write")

    for name, job in declared.items():
        if not isinstance(job, dict):
            errors.append(f"job {name} is not a mapping")
            continue
        if "services" in job:
            errors.append(f"job {name} must not declare services")
        if name == PUBLISH_JOB:
            errors.extend(_check_publish_job(job))
        elif _has_packages_write(job.get("permissions")):
            errors.append(f"job {name} must not have packages: write; only {PUBLISH_JOB} may")
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
    saw_pnpm = False
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
        elif action_name == "pnpm/action-setup":
            saw_pnpm = True
            package_json = str(with_.get("package_json_file", ""))
            if package_json != "miniapp/package.json":
                errors.append(
                    "pnpm/action-setup package_json_file must be miniapp/package.json, "
                    f"found {package_json!r}"
                )
            if "version" in with_ and str(with_["version"]).strip():
                errors.append("pnpm/action-setup must not set a version literal")
        else:
            errors.append(f"toolchain action uses unexpected action {uses!r}")
    if not saw_uv:
        errors.append("toolchain action must install uv from backend/pyproject.toml")
    if not saw_node:
        errors.append("toolchain action must install Node from miniapp/.nvmrc")
    if not saw_pnpm:
        errors.append("toolchain action must install pnpm from miniapp/package.json")
    return errors


def main() -> int:
    makefile = MAKEFILE_PATH.read_text()
    jobs = job_list_from_makefile(makefile)
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text())
    action = yaml.safe_load(ACTION_PATH.read_text())
    if not isinstance(workflow, dict) or not isinstance(action, dict):
        print("ci.yml and toolchain action.yml must be mappings", file=sys.stderr)
        return 1
    errors = check_workflow(workflow, action, jobs)
    errors.extend(check_job_functions(CI_SH_PATH.read_text(), jobs))
    errors.extend(check_banned_pm_bootstrap(banned_pm_texts_from_tree(ROOT)))
    if errors:
        for item in errors:
            print(item, file=sys.stderr)
        return 1
    print("ci-parity-check: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
