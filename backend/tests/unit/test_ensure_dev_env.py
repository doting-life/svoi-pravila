"""Unit tests for scripts/ensure_dev_env.py."""

from __future__ import annotations

import base64
import importlib.util
import re
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


def _value(text: str, key: str) -> str:
    match = re.search(rf"^{key}=(.*)$", text, flags=re.MULTILINE)
    assert match is not None
    return match.group(1).strip()


@pytest.mark.unit
def test_ensure_dev_env_creates_env_with_0600(tmp_path: Path) -> None:
    mod = _load_ensure_dev_env()
    example = tmp_path / ".env.example"
    example.write_text(
        "SP_DATA_KEK=\nSP_DATA_KEK_ID=local-1\nSP_PSEUDONYM_PEPPER=\n"
        "SP_TELEGRAM_UPDATES_MODE=polling\nSP_TELEGRAM_BOT_TOKEN=\n",
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    mod.main(env_path=env_path, example_path=example)
    assert env_path.exists()
    mode = stat.S_IMODE(env_path.stat().st_mode)
    assert mode == 0o600
    text = env_path.read_text(encoding="utf-8")
    kek = _value(text, "SP_DATA_KEK")
    pepper = _value(text, "SP_PSEUDONYM_PEPPER")
    assert len(base64.b64decode(kek, validate=True)) == 32
    assert len(base64.b64decode(pepper, validate=True)) >= 32
    assert _value(text, "SP_TELEGRAM_BOT_TOKEN") == ""
    assert "SP_TELEGRAM_WEBHOOK_PATH_SECRET=" not in text


@pytest.mark.unit
def test_ensure_dev_env_tightens_existing_permissions(tmp_path: Path) -> None:
    mod = _load_ensure_dev_env()
    example = tmp_path / ".env.example"
    example.write_text(
        "SP_DATA_KEK=already\nSP_DATA_KEK_ID=local-1\nSP_PSEUDONYM_PEPPER=pepper\n",
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    env_path.write_text(
        "SP_DATA_KEK=already\nSP_DATA_KEK_ID=local-1\nSP_PSEUDONYM_PEPPER=pepper\n",
        encoding="utf-8",
    )
    env_path.chmod(0o644)
    mod.main(env_path=env_path, example_path=example)
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    text = env_path.read_text(encoding="utf-8")
    assert "SP_DATA_KEK=already" in text
    assert "SP_PSEUDONYM_PEPPER=pepper" in text


@pytest.mark.unit
def test_ensure_dev_env_never_overwrites_bot_token(tmp_path: Path) -> None:
    mod = _load_ensure_dev_env()
    example = tmp_path / ".env.example"
    example.write_text(
        "SP_DATA_KEK=\nSP_PSEUDONYM_PEPPER=\nSP_TELEGRAM_BOT_TOKEN=\n"
        "SP_TELEGRAM_UPDATES_MODE=polling\n",
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    env_path.write_text(
        "SP_DATA_KEK=\nSP_PSEUDONYM_PEPPER=\nSP_TELEGRAM_BOT_TOKEN=keep-me\n"
        "SP_TELEGRAM_UPDATES_MODE=polling\n",
        encoding="utf-8",
    )
    mod.main(env_path=env_path, example_path=example)
    text = env_path.read_text(encoding="utf-8")
    assert _value(text, "SP_TELEGRAM_BOT_TOKEN") == "keep-me"


@pytest.mark.unit
def test_ensure_dev_env_writes_only_requested_path(tmp_path: Path) -> None:
    mod = _load_ensure_dev_env()
    example = tmp_path / ".env.example"
    example.write_text(
        "SP_DATA_KEK=\nSP_DATA_KEK_ID=local-1\nSP_PSEUDONYM_PEPPER=\n"
        "SP_TELEGRAM_UPDATES_MODE=polling\nSP_TELEGRAM_BOT_TOKEN=\n",
        encoding="utf-8",
    )
    sibling = tmp_path / ".env"
    target = tmp_path / "fresh.env"
    mod.main(env_path=target, example_path=example)
    assert target.exists()
    assert not sibling.exists()


@pytest.mark.unit
def test_ensure_dev_env_generates_webhook_secrets_only_in_webhook_mode(tmp_path: Path) -> None:
    mod = _load_ensure_dev_env()
    example = tmp_path / ".env.example"
    example.write_text(
        "SP_DATA_KEK=\nSP_PSEUDONYM_PEPPER=\nSP_TELEGRAM_UPDATES_MODE=webhook\n"
        "SP_TELEGRAM_WEBHOOK_PATH_SECRET=\nSP_TELEGRAM_WEBHOOK_SECRET_TOKEN=\n",
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    mod.main(env_path=env_path, example_path=example)
    text = env_path.read_text(encoding="utf-8")
    path_secret = _value(text, "SP_TELEGRAM_WEBHOOK_PATH_SECRET")
    secret_token = _value(text, "SP_TELEGRAM_WEBHOOK_SECRET_TOKEN")
    assert len(path_secret) >= 32
    assert len(secret_token) >= 32
