"""Ensure local .env has generated secrets without overwriting existing ones."""

from __future__ import annotations

import base64
import re
import secrets
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
EXAMPLE_PATH = ROOT / ".env.example"
_ENV_MODE = 0o600
_URL_SAFE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"


def ensure_env_permissions(path: Path) -> None:
    """Restrict ``path`` to owner read/write only (0600)."""
    path.chmod(_ENV_MODE)


def _ensure_key_line(text: str, example: str, key: str) -> str:
    if re.search(rf"^{key}=", text, flags=re.MULTILINE) is not None:
        return text
    match = re.search(rf"^{key}=(.*)$", example, flags=re.MULTILINE)
    if match is None:
        return text
    return text.rstrip() + "\n" + match.group(0) + "\n"


def _set_if_empty(text: str, key: str, value: str) -> str:
    match = re.search(rf"^{key}=(.*)$", text, flags=re.MULTILINE)
    if match is None:
        return text
    current = match.group(1).strip()
    if current:
        return text
    return re.sub(
        rf"^{key}=.*$",
        f"{key}={value}",
        text,
        count=1,
        flags=re.MULTILINE,
    )


def _updates_mode(text: str) -> str:
    match = re.search(r"^SP_TELEGRAM_UPDATES_MODE=(.*)$", text, flags=re.MULTILINE)
    if match is None:
        return ""
    return match.group(1).strip()


def main(env_path: Path = ENV_PATH, example_path: Path = EXAMPLE_PATH) -> None:
    """Fill missing KEK and pepper; fill webhook secrets only in webhook mode.

    Never generates or overwrites ``SP_TELEGRAM_BOT_TOKEN``.
    """
    text = env_path.read_text() if env_path.exists() else example_path.read_text()
    example = example_path.read_text()

    for key in (
        "SP_DATA_KEK",
        "SP_DATA_KEK_ID",
        "SP_PSEUDONYM_PEPPER",
        "SP_TELEGRAM_UPDATES_MODE",
        "SP_TELEGRAM_BOT_TOKEN",
        "SP_TELEGRAM_RATE_LIMIT_PER_MINUTE",
        "SP_TELEGRAM_DEDUP_TTL_SECONDS",
        "SP_TELEGRAM_SHUTDOWN_GRACE_SECONDS",
    ):
        text = _ensure_key_line(text, example, key)

    text = _set_if_empty(
        text,
        "SP_DATA_KEK",
        base64.b64encode(secrets.token_bytes(32)).decode(),
    )
    text = _set_if_empty(
        text,
        "SP_PSEUDONYM_PEPPER",
        base64.b64encode(secrets.token_bytes(32)).decode(),
    )

    if _updates_mode(text) == "webhook":
        for key in (
            "SP_TELEGRAM_WEBHOOK_PATH_SECRET",
            "SP_TELEGRAM_WEBHOOK_SECRET_TOKEN",
        ):
            text = _ensure_key_line(text, example, key)
        text = _set_if_empty(
            text,
            "SP_TELEGRAM_WEBHOOK_PATH_SECRET",
            "".join(secrets.choice(_URL_SAFE) for _ in range(48)),
        )
        text = _set_if_empty(
            text,
            "SP_TELEGRAM_WEBHOOK_SECRET_TOKEN",
            "".join(secrets.choice(_URL_SAFE) for _ in range(48)),
        )

    env_path.write_text(text)
    ensure_env_permissions(env_path)
    mode = stat.S_IMODE(env_path.stat().st_mode)
    if mode != _ENV_MODE:
        ensure_env_permissions(env_path)


if __name__ == "__main__":
    main()
