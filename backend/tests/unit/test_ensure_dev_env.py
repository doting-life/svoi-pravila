"""Unit tests for scripts/ensure_dev_env.py."""

from __future__ import annotations

import importlib.util
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ensure_dev_env.py"


class _EnsureDevEnvModule(Protocol):
    main: Callable[..., None]


def _load_ensure_dev_env() -> _EnsureDevEnvModule:
    spec = importlib.util.spec_from_file_location("ensure_dev_env", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_EnsureDevEnvModule, module)


@pytest.mark.unit
def test_ensure_dev_env_creates_env_with_0600(tmp_path: Path) -> None:
    mod = _load_ensure_dev_env()
    example = tmp_path / ".env.example"
    example.write_text(
        "SP_DATA_KEK=\nSP_DATA_KEK_ID=local-1\n",
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    mod.main(env_path=env_path, example_path=example)
    assert env_path.exists()
    mode = stat.S_IMODE(env_path.stat().st_mode)
    assert mode == 0o600
    text = env_path.read_text(encoding="utf-8")
    kek_line = next(line for line in text.splitlines() if line.startswith("SP_DATA_KEK="))
    assert kek_line.split("=", 1)[1].strip()


@pytest.mark.unit
def test_ensure_dev_env_tightens_existing_permissions(tmp_path: Path) -> None:
    mod = _load_ensure_dev_env()
    example = tmp_path / ".env.example"
    example.write_text("SP_DATA_KEK=already\nSP_DATA_KEK_ID=local-1\n", encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text("SP_DATA_KEK=already\nSP_DATA_KEK_ID=local-1\n", encoding="utf-8")
    env_path.chmod(0o644)
    mod.main(env_path=env_path, example_path=example)
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert "SP_DATA_KEK=already" in env_path.read_text(encoding="utf-8")
