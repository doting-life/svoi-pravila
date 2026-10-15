"""Tests for the per-run CI env file materialisation."""

from __future__ import annotations

import importlib.util
import re
import socket
import stat
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "materialize_ci_env.py"
PORT_KEYS = ("POSTGRES_PORT", "VALKEY_PORT", "API_PORT", "MINIAPP_PORT", "GRAFANA_PORT")
FORMER_FIXED_PORTS = ("15432", "16379", "18000", "18080", "13000")


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("materialize_ci_env", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _values(env_file: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in env_file.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


@pytest.mark.unit
def test_materialize_writes_assigned_ports_and_urls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load()
    monkeypatch.setattr(module, "_free_ports", lambda count: list(range(41001, 41001 + count)))
    env_file = tmp_path / "env"

    module.materialize(env_file=env_file, stack_smoke=False)

    values = _values(env_file)
    assert [values[key] for key in PORT_KEYS] == ["41001", "41002", "41003", "41004", "41005"]
    assert re.fullmatch(
        r"postgresql\+asyncpg://[^@]+@127\.0\.0\.1:41001/\w+", values["SP_DATABASE_URL"]
    )
    assert re.fullmatch(r"redis://:[^@]+@127\.0\.0\.1:41002/0", values["SP_VALKEY_URL"])
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


@pytest.mark.unit
def test_materialize_picks_distinct_ports_per_run(tmp_path: Path) -> None:
    module = _load()
    env_file = tmp_path / "env"

    module.materialize(env_file=env_file, stack_smoke=False)

    values = _values(env_file)
    ports = [int(values[key]) for key in PORT_KEYS]
    assert len(set(ports)) == len(PORT_KEYS)
    assert all(1024 <= port <= 65535 for port in ports)
    assert f":{ports[0]}/" in values["SP_DATABASE_URL"]
    assert f":{ports[1]}/" in values["SP_VALKEY_URL"]


@pytest.mark.unit
def test_free_ports_are_distinct_and_bindable() -> None:
    module = _load()

    ports = module._free_ports(5)

    assert len(set(ports)) == 5
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", port))


@pytest.mark.unit
def test_no_fixed_port_remains_in_the_script() -> None:
    source = SCRIPT.read_text()
    for port in FORMER_FIXED_PORTS:
        assert port not in source
    assert re.search(r"^CI_\w*PORT\s*=", source, flags=re.MULTILINE) is None


@pytest.mark.unit
def test_materialize_refuses_env_file_inside_repository() -> None:
    module = _load()

    with pytest.raises(SystemExit):
        module.materialize(env_file=ROOT / "ci-env-should-not-exist", stack_smoke=False)
    assert not (ROOT / "ci-env-should-not-exist").exists()
