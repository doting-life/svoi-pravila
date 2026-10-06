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


JOBS = ["backend", "miniapp", "secrets", "image", "stack-smoke", "ownership-guard"]

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
"""

VALID_JOB = """
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
      - uses: ./.github/actions/toolchain
      - run: make ci JOB={name}
"""


def _workflow(jobs: dict[str, str]) -> str:
    body = "\n".join(f"  {name}:{spec.format(name=name)}" for name, spec in jobs.items())
    return "jobs:\n" + body


@pytest.mark.unit
def test_parity_accepts_canonical_workflow() -> None:
    module = _load()
    jobs = dict.fromkeys(JOBS, VALID_JOB)
    errors = module.check_workflow(
        yaml.safe_load(_workflow(jobs)),
        yaml.safe_load(VALID_ACTION),
        JOBS,
    )
    assert errors == []


@pytest.mark.unit
def test_parity_rejects_inline_run_logic() -> None:
    module = _load()
    jobs = dict.fromkeys(JOBS, VALID_JOB)
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
    jobs = dict.fromkeys(JOBS, VALID_JOB)
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
"""
    errors = module.check_workflow(
        yaml.safe_load(_workflow(jobs)),
        yaml.safe_load(action),
        JOBS,
    )
    assert any("version literal" in item for item in errors)


@pytest.mark.unit
def test_parity_rejects_missing_job() -> None:
    module = _load()
    jobs = {name: VALID_JOB for name in JOBS if name != "miniapp"}
    errors = module.check_workflow(
        yaml.safe_load(_workflow(jobs)),
        yaml.safe_load(VALID_ACTION),
        JOBS,
    )
    assert any("does not equal" in item for item in errors)
