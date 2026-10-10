"""Table tests for change-scoped CI job selection."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ci_scope.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ci_scope", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scope() -> ModuleType:
    return _load()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (
            ["docs/maintainers/04-workflow.md", "AGENTS.md", "README.md"],
            frozenset({"secrets", "ownership-guard"}),
        ),
        (
            [".cursor/rules/00-executor-contract.mdc"],
            frozenset({"secrets", "ownership-guard"}),
        ),
        (
            ["backend/src/svoi_pravila/domain/text.py"],
            frozenset({"backend", "secrets", "image", "stack-smoke"}),
        ),
        (
            ["miniapp/src/App.tsx"],
            frozenset({"miniapp", "secrets", "image", "stack-smoke"}),
        ),
        (
            ["backend/src/x.py", "miniapp/src/y.ts"],
            frozenset({"backend", "miniapp", "secrets", "image", "stack-smoke"}),
        ),
        (
            ["backend/src/x.py", "docs/readme.md"],
            frozenset({"backend", "secrets", "image", "stack-smoke", "ownership-guard"}),
        ),
        (
            ["scripts/ci.sh"],
            frozenset(
                {
                    "backend",
                    "miniapp",
                    "secrets",
                    "image",
                    "stack-smoke",
                    "prod-smoke",
                    "ownership-guard",
                }
            ),
        ),
        (
            ["ops/grafana/Dockerfile"],
            frozenset(
                {
                    "backend",
                    "miniapp",
                    "secrets",
                    "image",
                    "stack-smoke",
                    "prod-smoke",
                    "ownership-guard",
                }
            ),
        ),
        (
            ["unknown/path.bin"],
            frozenset(
                {
                    "backend",
                    "miniapp",
                    "secrets",
                    "image",
                    "stack-smoke",
                    "prod-smoke",
                    "ownership-guard",
                }
            ),
        ),
    ],
)
def test_jobs_for_paths(
    scope: ModuleType,
    paths: list[str],
    expected: frozenset[str],
) -> None:
    assert scope.jobs_for_paths(paths) == expected


@pytest.mark.unit
def test_force_full_suite_github_only(scope: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    assert scope.force_full_suite(github_event_name="schedule") is True
    assert scope.force_full_suite(github_ref="refs/heads/master") is True
    # Local pre-push to master must not force the full suite.
    monkeypatch.setenv("PUSH_REF", "refs/heads/master")
    assert (
        scope.force_full_suite(
            github_event_name="pull_request",
            github_ref="refs/pull/1/merge",
        )
        is False
    )
    monkeypatch.delenv("PUSH_REF", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.delenv("GITHUB_REF", raising=False)
    assert scope.force_full_suite() is False


@pytest.mark.unit
def test_decide_hook_docs_vs_github_master(scope: ModuleType) -> None:
    docs = ["docs/maintainers/00-status.md", "AGENTS.md"]
    hook = scope.decide(docs, force_all=False)
    assert hook.jobs == frozenset({"secrets", "ownership-guard"})
    assert hook.image_scan is False
    github = scope.decide(docs, force_all=True)
    assert github.jobs == frozenset(scope.all_jobs())
    assert github.image_scan is True


@pytest.mark.unit
@pytest.mark.parametrize(
    ("paths", "scan"),
    [
        (["backend/src/foo.py"], False),
        (["backend/Dockerfile"], True),
        (["miniapp/Dockerfile"], True),
        (["backend/uv.lock"], True),
        (["miniapp/pnpm-lock.yaml"], True),
        (["scripts/image-pins.env"], True),
        (["ops/grafana/image-pins.env"], True),
        (["docs/x.md"], False),
    ],
)
def test_image_scan_inputs(
    scope: ModuleType,
    paths: list[str],
    *,
    scan: bool,
) -> None:
    decision = scope.decide(paths, force_all=False)
    assert decision.image_scan is scan
