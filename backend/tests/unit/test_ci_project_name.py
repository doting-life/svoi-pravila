"""Shell check: the CI compose project name carries the run's unique suffix."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "scripts" / "ci_project.sh"
COMPOSE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _project_name(ci_tmpdir: str, explicit: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key != "COMPOSE_PROJECT_NAME"}
    env["HELPER"] = str(HELPER)
    env["RUN_TMPDIR"] = ci_tmpdir
    if explicit is not None:
        env["COMPOSE_PROJECT_NAME"] = explicit
    return subprocess.run(
        ["/bin/bash", "-c", 'source "$HELPER" && ci_project_name "$RUN_TMPDIR"'],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _image_tag(ci_tmpdir: str) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key != "COMPOSE_PROJECT_NAME"}
    env["HELPER"] = str(HELPER)
    env["RUN_TMPDIR"] = ci_tmpdir
    return subprocess.run(
        ["/bin/bash", "-c", 'source "$HELPER" && ci_image_tag "$RUN_TMPDIR"'],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.unit
def test_project_name_contains_lower_cased_run_suffix() -> None:
    result = _project_name("/work/svoi-pravila-ci.AbC123")

    assert result.returncode == 0
    assert result.stdout.strip() == "svoi-pravila-ci-abc123"
    assert COMPOSE_NAME.fullmatch(result.stdout.strip())


@pytest.mark.unit
def test_project_names_differ_between_runs() -> None:
    first = _project_name("/work/svoi-pravila-ci.aaaaaa").stdout
    second = _project_name("/work/svoi-pravila-ci.bbbbbb").stdout

    assert first != second


@pytest.mark.unit
def test_explicit_compose_project_name_wins() -> None:
    result = _project_name("/work/svoi-pravila-ci.AbC123", explicit="custom-project")

    assert result.returncode == 0
    assert result.stdout.strip() == "custom-project"


@pytest.mark.unit
def test_directory_without_suffix_is_rejected() -> None:
    result = _project_name("/work/svoi-pravila-ci")

    assert result.returncode != 0
    assert result.stdout == ""


@pytest.mark.unit
def test_image_tag_is_derived_from_the_run_suffix() -> None:
    result = _image_tag("/work/svoi-pravila-ci.AbC123")

    assert result.returncode == 0
    assert result.stdout.strip() == "ci-abc123"


@pytest.mark.unit
def test_image_tags_differ_between_runs() -> None:
    first = _image_tag("/work/svoi-pravila-ci.aaaaaa").stdout
    second = _image_tag("/work/svoi-pravila-ci.bbbbbb").stdout

    assert first != second


@pytest.mark.unit
def test_image_tag_ignores_explicit_compose_project_name() -> None:
    env = {**os.environ, "COMPOSE_PROJECT_NAME": "custom-project", "HELPER": str(HELPER)}
    env["RUN_TMPDIR"] = "/work/svoi-pravila-ci.AbC123"
    result = subprocess.run(
        ["/bin/bash", "-c", 'source "$HELPER" && ci_image_tag "$RUN_TMPDIR"'],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.stdout.strip() == "ci-abc123"


@pytest.mark.unit
def test_image_tag_without_suffix_is_rejected() -> None:
    result = _image_tag("/work/svoi-pravila-ci")

    assert result.returncode != 0
    assert result.stdout == ""
