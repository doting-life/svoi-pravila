"""Negative tests for the GitHub workflow parity checker."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ci_parity_check.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ci_parity_check", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


JOBS = [
    "backend",
    "miniapp",
    "secrets",
    "image",
    "stack-smoke",
    "prod-smoke",
    "ownership-guard",
]

VALID_ACTION = """
runs:
  using: composite
  steps:
    - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7
      with:
        version-file: backend/pyproject.toml
        enable-cache: true
    - uses: actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020
      with:
        node-version-file: miniapp/.nvmrc
    - uses: pnpm/action-setup@ea17c68df8912ef543352723c149a84f56e3d413
      with:
        package_json_file: miniapp/package.json
"""

VALID_JOB = """
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
      - uses: ./.github/actions/toolchain
      - run: make ci JOB={name}
"""

VALID_PUBLISH_JOB = """
    if: github.event_name == 'push' && github.ref == 'refs/heads/master'
    needs: [backend, miniapp, secrets, image, stack-smoke, prod-smoke]
    runs-on: ubuntu-latest
    permissions:
      packages: write
      contents: read
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
      - uses: ./.github/actions/toolchain
      - run: make ci JOB=publish
"""

VALID_CI_SH = """
job_workflow() { :; }
job_toolchain() { :; }
job_backend() { :; }
job_miniapp() { :; }
job_secrets() { :; }
job_image() { :; }
job_stack_smoke() { :; }
job_prod_smoke() { :; }
job_publish() {
  run_stage publish scan make -C "$ROOT" image-scan
  run_stage publish push make -C "$ROOT" publish
}
job_ownership_guard() { :; }
"""

INVALID_CI_SH_PUBLISH_NO_SCAN = """
job_workflow() { :; }
job_toolchain() { :; }
job_backend() { :; }
job_miniapp() { :; }
job_secrets() { :; }
job_image() { :; }
job_stack_smoke() { :; }
job_prod_smoke() { :; }
job_publish() {
  run_stage publish push make -C "$ROOT" publish
}
job_ownership_guard() { :; }
"""


def _workflow(jobs: dict[str, str]) -> str:
    body = "\n".join(f"  {name}:{spec.format(name=name)}" for name, spec in jobs.items())
    return "jobs:\n" + body


def _canonical_jobs() -> dict[str, str]:
    jobs = dict.fromkeys(JOBS, VALID_JOB)
    jobs["publish"] = VALID_PUBLISH_JOB
    return jobs


def _publish_errors(publish_job: str) -> list[str]:
    module = _load()
    jobs = _canonical_jobs()
    jobs["publish"] = publish_job
    errors: list[str] = module.check_workflow(
        yaml.safe_load(_workflow(jobs)),
        yaml.safe_load(VALID_ACTION),
        JOBS,
    )
    return errors


@pytest.mark.unit
def test_parity_accepts_canonical_workflow() -> None:
    module = _load()
    jobs = _canonical_jobs()
    errors = module.check_workflow(
        yaml.safe_load(_workflow(jobs)),
        yaml.safe_load(VALID_ACTION),
        JOBS,
    )
    assert errors == []
    assert module.check_job_functions(VALID_CI_SH, JOBS) == []
    assert module.check_banned_pm_bootstrap({"Makefile": "PNPM := pnpm"}) == []


@pytest.mark.unit
def test_parity_rejects_inline_run_logic() -> None:
    module = _load()
    jobs = _canonical_jobs()
    jobs["backend"] = """
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
      - uses: ./.github/actions/toolchain
      - run: echo hello && make ci JOB=backend
"""
    errors = module.check_workflow(
        yaml.safe_load(_workflow(jobs)),
        yaml.safe_load(VALID_ACTION),
        JOBS,
    )
    assert any("run" in item for item in errors)


@pytest.mark.unit
def test_parity_rejects_version_literal() -> None:
    module = _load()
    jobs = _canonical_jobs()
    action = """
runs:
  using: composite
  steps:
    - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7
      with:
        version-file: backend/pyproject.toml
        version: 0.12.22
    - uses: actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020
      with:
        node-version-file: miniapp/.nvmrc
    - uses: pnpm/action-setup@ea17c68df8912ef543352723c149a84f56e3d413
      with:
        package_json_file: miniapp/package.json
        version: 12.9.1
"""
    errors = module.check_workflow(
        yaml.safe_load(_workflow(jobs)),
        yaml.safe_load(action),
        JOBS,
    )
    assert any("version literal" in item for item in errors)


@pytest.mark.unit
def test_parity_rejects_pnpm_version_literal() -> None:
    module = _load()
    jobs = _canonical_jobs()
    action = """
runs:
  using: composite
  steps:
    - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7
      with:
        version-file: backend/pyproject.toml
    - uses: actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020
      with:
        node-version-file: miniapp/.nvmrc
    - uses: pnpm/action-setup@ea17c68df8912ef543352723c149a84f56e3d413
      with:
        package_json_file: miniapp/package.json
        version: 12.9.1
"""
    errors = module.check_workflow(
        yaml.safe_load(_workflow(jobs)),
        yaml.safe_load(action),
        JOBS,
    )
    assert any("pnpm/action-setup must not set a version literal" in item for item in errors)


@pytest.mark.unit
def test_parity_rejects_missing_pnpm_step() -> None:
    module = _load()
    jobs = _canonical_jobs()
    action = """
runs:
  using: composite
  steps:
    - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7
      with:
        version-file: backend/pyproject.toml
    - uses: actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020
      with:
        node-version-file: miniapp/.nvmrc
"""
    errors = module.check_workflow(
        yaml.safe_load(_workflow(jobs)),
        yaml.safe_load(action),
        JOBS,
    )
    assert any("must install pnpm" in item for item in errors)


@pytest.mark.unit
def test_parity_rejects_missing_job() -> None:
    module = _load()
    jobs = {name: spec for name, spec in _canonical_jobs().items() if name != "miniapp"}
    errors = module.check_workflow(
        yaml.safe_load(_workflow(jobs)),
        yaml.safe_load(VALID_ACTION),
        JOBS,
    )
    assert any("does not equal" in item for item in errors)


@pytest.mark.unit
def test_parity_rejects_corepack() -> None:
    module = _load()
    errors = module.check_banned_pm_bootstrap({"Makefile": "PNPM := cd miniapp && corepack pnpm"})
    assert any("corepack" in item for item in errors)


@pytest.mark.unit
def test_parity_rejects_missing_job_function() -> None:
    module = _load()
    errors = module.check_job_functions(
        """
job_workflow() { :; }
job_toolchain() { :; }
job_backend() { :; }
""",
        JOBS,
    )
    assert any("missing job_miniapp" in item for item in errors)


@pytest.mark.unit
def test_parity_rejects_extra_job_function() -> None:
    module = _load()
    errors = module.check_job_functions(VALID_CI_SH + "\njob_extra() { :; }\n", JOBS)
    assert any("unexpected job functions: job_extra" in item for item in errors)


@pytest.mark.unit
def test_parity_rejects_missing_publish_job() -> None:
    module = _load()
    jobs = {name: spec for name, spec in _canonical_jobs().items() if name != "publish"}
    errors = module.check_workflow(
        yaml.safe_load(_workflow(jobs)),
        yaml.safe_load(VALID_ACTION),
        JOBS,
    )
    assert any("does not equal" in item for item in errors)


@pytest.mark.unit
def test_parity_rejects_publish_in_ci_jobs() -> None:
    module = _load()
    errors = module.check_workflow(
        yaml.safe_load(_workflow(_canonical_jobs())),
        yaml.safe_load(VALID_ACTION),
        [*JOBS, "publish"],
    )
    assert any("must not be listed in CI_JOBS" in item for item in errors)


@pytest.mark.unit
def test_parity_rejects_publish_on_pull_requests() -> None:
    errors = _publish_errors(VALID_PUBLISH_JOB.replace("github.event_name == 'push' && ", ""))
    assert any("job publish if" in item for item in errors)


@pytest.mark.unit
def test_parity_rejects_publish_missing_prod_smoke_need() -> None:
    errors = _publish_errors(VALID_PUBLISH_JOB.replace(", prod-smoke]", "]"))
    assert any("job publish needs" in item for item in errors)


@pytest.mark.unit
def test_parity_rejects_publish_without_packages_write() -> None:
    errors = _publish_errors(VALID_PUBLISH_JOB.replace("packages: write", "packages: read"))
    assert any("job publish permissions" in item for item in errors)


@pytest.mark.unit
def test_parity_rejects_packages_write_outside_publish() -> None:
    module = _load()
    jobs = _canonical_jobs()
    jobs["image"] = VALID_JOB + "    permissions:\n      packages: write\n"
    errors = module.check_workflow(
        yaml.safe_load(_workflow(jobs)),
        yaml.safe_load(VALID_ACTION),
        JOBS,
    )
    assert any("job image must not have packages: write" in item for item in errors)


@pytest.mark.unit
def test_parity_rejects_workflow_level_packages_write() -> None:
    module = _load()
    workflow = yaml.safe_load(_workflow(_canonical_jobs()))
    workflow["permissions"] = {"packages": "write"}
    errors = module.check_workflow(workflow, yaml.safe_load(VALID_ACTION), JOBS)
    assert any("workflow-level permissions" in item for item in errors)


@pytest.mark.unit
def test_parity_rejects_missing_publish_function() -> None:
    module = _load()
    errors = module.check_job_functions(
        VALID_CI_SH.replace(
            'job_publish() {\n  run_stage publish scan make -C "$ROOT" image-scan\n'
            '  run_stage publish push make -C "$ROOT" publish\n}\n',
            "",
        ),
        JOBS,
    )
    assert any("missing job_publish" in item for item in errors)


@pytest.mark.unit
def test_parity_accepts_publish_with_image_scan_before_push() -> None:
    module = _load()
    assert module.check_job_functions(VALID_CI_SH, JOBS) == []


@pytest.mark.unit
def test_parity_rejects_publish_without_image_scan() -> None:
    module = _load()
    errors = module.check_job_functions(INVALID_CI_SH_PUBLISH_NO_SCAN, JOBS)
    assert any("image-scan" in item for item in errors)
