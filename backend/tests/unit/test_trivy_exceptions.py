"""Tests for timed Trivy exceptions (ADR-0012, ADR-0017)."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "trivy_exceptions.py"
PLUGIN_PREFIX = "usr/share/grafana/plugins/"
PLUGIN_TARGET = "usr/share/grafana/plugins/prometheus/gpx_grafana-prometheus-datasource_linux_amd64"
SERVER_TARGET = "usr/share/grafana/bin/grafana"
TODAY = date(2026, 10, 10)


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


def _row(image: str, **extra: object) -> dict[str, object]:
    row: dict[str, object] = {
        "cve": "CVE-2026-0001",
        "image": image,
        "statement": "not reachable in this topology",
        "expires": (TODAY + timedelta(days=7)).isoformat(),
    }
    row.update(extra)
    return row


def _report(*findings: tuple[str, str]) -> dict[str, object]:
    targets: dict[str, list[dict[str, str]]] = {}
    for cve, target in findings:
        targets.setdefault(target, []).append({"VulnerabilityID": cve})
    return {
        "Results": [
            {"Target": target, "Vulnerabilities": vulns} for target, vulns in targets.items()
        ]
    }


@pytest.mark.unit
def test_forbidden_images_cannot_have_exceptions(mod: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "ex.yaml"
    for image in ("api", "miniapp", "backup"):
        _write_exceptions(path, [_row(image)])
        with pytest.raises(mod.ExceptionError, match="cannot have exceptions"):
            mod.load_exceptions(path, today=TODAY)


@pytest.mark.unit
def test_unknown_image_is_rejected(mod: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "ex.yaml"
    _write_exceptions(path, [_row("postgres")])
    with pytest.raises(mod.ExceptionError, match="is not allowed"):
        mod.load_exceptions(path, today=TODAY)


@pytest.mark.unit
def test_grafana_entry_without_target_prefix_is_rejected(mod: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "ex.yaml"
    _write_exceptions(path, [_row("grafana")])
    with pytest.raises(mod.ExceptionError, match="requires target_prefix"):
        mod.load_exceptions(path, today=TODAY)


@pytest.mark.unit
@pytest.mark.parametrize(
    "prefix",
    ["usr/share/grafana/bin/", "usr/share/grafana/", "", "usr/share/grafana/plugins", "/"],
)
def test_grafana_entry_with_another_prefix_is_rejected(
    mod: ModuleType, tmp_path: Path, prefix: str
) -> None:
    path = tmp_path / "ex.yaml"
    _write_exceptions(path, [_row("grafana", target_prefix=prefix)])
    with pytest.raises(mod.ExceptionError, match="requires target_prefix"):
        mod.load_exceptions(path, today=TODAY)


@pytest.mark.unit
@pytest.mark.parametrize("image", ["prometheus", "alertmanager", "node-exporter"])
def test_target_prefix_on_another_image_is_rejected(
    mod: ModuleType, tmp_path: Path, image: str
) -> None:
    path = tmp_path / "ex.yaml"
    _write_exceptions(path, [_row(image, target_prefix=PLUGIN_PREFIX)])
    with pytest.raises(mod.ExceptionError, match="target_prefix is only allowed"):
        mod.load_exceptions(path, today=TODAY)


@pytest.mark.unit
def test_expired_exception_fails(mod: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "ex.yaml"
    _write_exceptions(
        path,
        [
            _row(
                "grafana",
                target_prefix=PLUGIN_PREFIX,
                expires=(TODAY - timedelta(days=1)).isoformat(),
            )
        ],
    )
    with pytest.raises(mod.ExceptionError, match="expired"):
        mod.load_exceptions(path, today=TODAY)


@pytest.mark.unit
def test_expiry_beyond_30_days_rejected(mod: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "ex.yaml"
    _write_exceptions(path, [_row("prometheus", expires=(TODAY + timedelta(days=31)).isoformat())])
    with pytest.raises(mod.ExceptionError, match="more than 30 days"):
        mod.load_exceptions(path, today=TODAY)


@pytest.mark.unit
def test_grafana_finding_in_server_binary_is_not_ignored(mod: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "ex.yaml"
    _write_exceptions(path, [_row("grafana", target_prefix=PLUGIN_PREFIX)])
    entries = mod.load_exceptions(path, today=TODAY)
    report = _report(("CVE-2026-0001", SERVER_TARGET))
    assert mod.remaining_findings(report, image="grafana", entries=entries) == ["CVE-2026-0001"]


@pytest.mark.unit
def test_grafana_finding_in_plugin_binary_is_ignored(mod: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "ex.yaml"
    _write_exceptions(path, [_row("grafana", target_prefix=PLUGIN_PREFIX)])
    entries = mod.load_exceptions(path, today=TODAY)
    report = _report(("CVE-2026-0001", PLUGIN_TARGET))
    assert mod.remaining_findings(report, image="grafana", entries=entries) == []


@pytest.mark.unit
def test_grafana_other_cve_and_os_packages_are_not_ignored(mod: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "ex.yaml"
    _write_exceptions(path, [_row("grafana", target_prefix=PLUGIN_PREFIX)])
    entries = mod.load_exceptions(path, today=TODAY)
    report = _report(
        ("CVE-2026-0001", PLUGIN_TARGET),
        ("CVE-2026-0001", "svoi-pravila-grafana:ci (alpine 3.24.2)"),
        ("CVE-2026-0002", PLUGIN_TARGET),
    )
    leftover = mod.remaining_findings(report, image="grafana", entries=entries)
    assert sorted(leftover) == ["CVE-2026-0001", "CVE-2026-0002"]


@pytest.mark.unit
def test_remaining_findings_honours_exceptions_per_image(mod: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "ex.yaml"
    _write_exceptions(
        path,
        [
            {
                **_row("alertmanager"),
                "cve": "CVE-2026-1111",
                "statement": "Alertmanager is an HTTPS client of Telegram only",
                "expires": (TODAY + timedelta(days=30)).isoformat(),
            }
        ],
    )
    entries = mod.load_exceptions(path, today=TODAY)
    report = _report(("CVE-2026-1111", "bin/alertmanager"), ("CVE-2026-2222", "bin/alertmanager"))
    assert mod.remaining_findings(report, image="alertmanager", entries=entries) == [
        "CVE-2026-2222"
    ]
    assert mod.remaining_findings(report, image="prometheus", entries=entries) == [
        "CVE-2026-1111",
        "CVE-2026-2222",
    ]


@pytest.mark.unit
def test_load_accepts_empty_files(mod: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "ex.yaml"
    path.write_text("", encoding="utf-8")
    assert mod.load_exceptions(path, today=TODAY) == ()
    path.write_text("exceptions:\n", encoding="utf-8")
    assert mod.load_exceptions(path, today=TODAY) == ()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("- a\n", "root must be a mapping"),
        ("exceptions: nope\n", "must be a list"),
        ("exceptions:\n  - nope\n", "must be a mapping"),
        ("exceptions:\n  - {image: prometheus, statement: s, expires: '2026-10-11'}\n", "cve"),
        ("exceptions:\n  - {cve: c, statement: s, expires: '2026-10-11'}\n", "image"),
        ("exceptions:\n  - {cve: c, image: prometheus, expires: '2026-10-11'}\n", "statement"),
        ("exceptions:\n  - {cve: c, image: prometheus, statement: s, expires: 5}\n", "ISO date"),
    ],
)
def test_malformed_files_are_rejected(
    mod: ModuleType, tmp_path: Path, raw: str, message: str
) -> None:
    path = tmp_path / "ex.yaml"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(mod.ExceptionError, match=message):
        mod.load_exceptions(path, today=TODAY)


@pytest.mark.unit
def test_cli_filters_a_grafana_report_by_target(
    mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    today = datetime.now(tz=UTC).date()
    path = tmp_path / "ex.yaml"
    _write_exceptions(
        path,
        [
            _row(
                "grafana",
                target_prefix=PLUGIN_PREFIX,
                expires=(today + timedelta(days=7)).isoformat(),
            )
        ],
    )
    clean = _report(("CVE-2026-0001", PLUGIN_TARGET))
    dirty = _report(("CVE-2026-0001", SERVER_TARGET))
    args = ["--exceptions", str(path), "--image", "grafana"]
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(clean)))
    assert mod.main(args) == 0
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(dirty)))
    assert mod.main(args) == 1


@pytest.mark.unit
def test_cli_validate_and_forbidden_images(
    mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ex.yaml"
    _write_exceptions(
        path,
        [_row("prometheus", expires=(datetime.now(tz=UTC).date() + timedelta(days=3)).isoformat())],
    )
    assert mod.main(["--exceptions", str(path), "--validate"]) == 0
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_report(("CVE-2026-0001", "x")))))
    assert mod.main(["--exceptions", str(path), "--image", "api"]) == 1
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"Results": []})))
    assert mod.main(["--exceptions", str(path), "--image", "api"]) == 0
    bad = tmp_path / "bad.yaml"
    _write_exceptions(bad, [_row("api")])
    assert mod.main(["--exceptions", str(bad), "--validate"]) == 1
