"""Tests for timed Trivy exceptions (ADR-0012)."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "trivy_exceptions.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("trivy_exceptions", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod() -> ModuleType:
    return _load()


def _write_exceptions(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(yaml.safe_dump({"exceptions": rows}), encoding="utf-8")


@pytest.mark.unit
def test_forbidden_images_cannot_have_exceptions(mod: ModuleType, tmp_path: Path) -> None:
    today = date(2026, 10, 10)
    path = tmp_path / "ex.yaml"
    for image in ("api", "miniapp", "backup"):
        _write_exceptions(
            path,
            [
                {
                    "cve": "CVE-2026-0001",
                    "image": image,
                    "statement": "no",
                    "expires": (today + timedelta(days=7)).isoformat(),
                }
            ],
        )
        with pytest.raises(mod.ExceptionError, match="cannot have exceptions"):
            mod.load_exceptions(path, today=today)


@pytest.mark.unit
def test_expired_exception_fails(mod: ModuleType, tmp_path: Path) -> None:
    today = date(2026, 10, 10)
    path = tmp_path / "ex.yaml"
    _write_exceptions(
        path,
        [
            {
                "cve": "CVE-2026-0002",
                "image": "grafana",
                "statement": "loopback only",
                "expires": (today - timedelta(days=1)).isoformat(),
            }
        ],
    )
    with pytest.raises(mod.ExceptionError, match="expired"):
        mod.load_exceptions(path, today=today)


@pytest.mark.unit
def test_expiry_beyond_30_days_rejected(mod: ModuleType, tmp_path: Path) -> None:
    today = date(2026, 10, 10)
    path = tmp_path / "ex.yaml"
    _write_exceptions(
        path,
        [
            {
                "cve": "CVE-2026-0003",
                "image": "prometheus",
                "statement": "internal",
                "expires": (today + timedelta(days=31)).isoformat(),
            }
        ],
    )
    with pytest.raises(mod.ExceptionError, match="more than 30 days"):
        mod.load_exceptions(path, today=today)


@pytest.mark.unit
def test_remaining_findings_honours_exceptions(mod: ModuleType, tmp_path: Path) -> None:
    today = date(2026, 10, 10)
    path = tmp_path / "ex.yaml"
    _write_exceptions(
        path,
        [
            {
                "cve": "CVE-2026-1111",
                "image": "grafana",
                "statement": (
                    "Grafana listens on 127.0.0.1 only; Prometheus is on internal networks only"
                ),
                "expires": (today + timedelta(days=30)).isoformat(),
            }
        ],
    )
    entries = mod.load_exceptions(path, today=today)
    report = {
        "Results": [
            {
                "Vulnerabilities": [
                    {"VulnerabilityID": "CVE-2026-1111"},
                    {"VulnerabilityID": "CVE-2026-2222"},
                ]
            }
        ]
    }
    assert mod.remaining_findings(report, image="grafana", entries=entries) == ["CVE-2026-2222"]
    assert mod.remaining_findings(report, image="prometheus", entries=entries) == [
        "CVE-2026-1111",
        "CVE-2026-2222",
    ]
