"""Guard: UsageSurface.DM must not be produced outside historical row deserialization."""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_SRC = Path(__file__).resolve().parents[3] / "src" / "svoi_pravila"
_ALLOWED_ADAPTER_FILES = frozenset(
    {
        "adapters/persistence/repositories.py",
        "adapters/persistence/analytics_store.py",
    }
)


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


@pytest.mark.unit
def test_usage_surface_dm_absent_from_application() -> None:
    application = _REPO_SRC / "application"
    offenders: list[str] = []
    for path in _python_files(application):
        text = path.read_text(encoding="utf-8")
        if "UsageSurface.DM" in text:
            offenders.append(str(path.relative_to(_REPO_SRC)))
    assert offenders == []


@pytest.mark.unit
def test_usage_surface_dm_only_in_allowed_adapter_deserializers() -> None:
    adapters = _REPO_SRC / "adapters"
    offenders: list[str] = []
    for path in _python_files(adapters):
        text = path.read_text(encoding="utf-8")
        if "UsageSurface.DM" not in text:
            continue
        rel = str(path.relative_to(_REPO_SRC))
        if rel not in _ALLOWED_ADAPTER_FILES:
            offenders.append(rel)
    assert offenders == []
