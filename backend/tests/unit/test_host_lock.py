"""Tests for the host-wide lock helper (fcntl.flock, no retry loop)."""

from __future__ import annotations

import fcntl
import importlib.util
import os
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "host_lock.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("host_lock", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _cache_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def _lock_file(tmp_path: Path) -> Path:
    return tmp_path / "cache" / "svoi-pravila" / "locks" / "prod-smoke.lock"


@contextmanager
def _held(tmp_path: Path) -> Iterator[int]:
    path = _lock_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        yield fd
    finally:
        os.close(fd)


def _args(timeout: int, *command: str) -> list[str]:
    return ["--name", "prod-smoke", "--timeout", str(timeout), "--", *command]


def _can_lock(tmp_path: Path) -> bool:
    fd = os.open(_lock_file(tmp_path), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    finally:
        os.close(fd)
    return True


@pytest.mark.unit
def test_exit_code_of_the_command_passes_through(tmp_path: Path) -> None:
    module = _load()

    assert module.main(_args(5, "/bin/bash", "-c", "exit 7")) == 7
    assert module.main(_args(5, "/bin/bash", "-c", "exit 0")) == 0
    assert _lock_file(tmp_path).parent.is_dir()


@pytest.mark.unit
def test_lock_is_held_while_the_command_runs_and_released_on_exit(tmp_path: Path) -> None:
    module = _load()
    probe = (
        "import fcntl, os, sys\n"
        "fd = os.open(sys.argv[1], os.O_RDWR)\n"
        "try:\n"
        "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "except BlockingIOError:\n"
        "    sys.exit(0)\n"
        "sys.exit(1)\n"
    )
    path = _lock_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.touch()

    code = module.main(_args(5, sys.executable, "-c", probe, str(path)))

    assert code == 0
    assert _can_lock(tmp_path)


@pytest.mark.unit
def test_lock_is_released_when_the_command_fails(tmp_path: Path) -> None:
    module = _load()

    assert module.main(_args(5, "/bin/bash", "-c", "exit 3")) == 3
    assert _can_lock(tmp_path)


@pytest.mark.unit
def test_second_holder_waits_until_the_first_releases(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    marker = tmp_path / "released"
    command = ("/bin/bash", "-c", f'test -f "{marker}"')

    with _held(tmp_path) as fd:

        def release() -> None:
            marker.write_text("done")
            fcntl.flock(fd, fcntl.LOCK_UN)

        threading.Timer(0.2, release).start()
        code = module.main(_args(10, *command))

    assert code == 0
    assert capsys.readouterr().out.splitlines() == ["ci: prod-smoke waiting for host lock"]


@pytest.mark.unit
def test_timeout_fails_clearly_without_running_the_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    ran = tmp_path / "ran"

    with _held(tmp_path):
        code = module.main(_args(1, "/bin/bash", "-c", f'touch "{ran}"'))

    captured = capsys.readouterr()
    assert code == module.TIMEOUT_EXIT
    assert not ran.exists()
    assert "prod-smoke host lock not acquired within 1s" in captured.err


@pytest.mark.unit
@pytest.mark.parametrize(
    "argv",
    [
        ["--name", "prod-smoke", "--timeout", "5"],
        ["--name", "prod-smoke", "--timeout", "5", "--"],
        ["--name", "../x", "--timeout", "5", "--", "true"],
        ["--name", "prod-smoke", "--timeout", "0", "--", "true"],
    ],
)
def test_invalid_usage_is_rejected(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    module = _load()

    assert module.main(argv) == module.USAGE_EXIT
    assert "usage:" in capsys.readouterr().err
