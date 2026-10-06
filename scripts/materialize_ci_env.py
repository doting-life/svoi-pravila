"""Write a hermetic CI env file outside the repository."""

from __future__ import annotations

import argparse
import base64
import re
import secrets
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PATH = ROOT / ".env.example"
_ENV_MODE = 0o600

CI_POSTGRES_PORT = "15432"
CI_VALKEY_PORT = "16379"
CI_API_PORT = "18000"
CI_MINIAPP_PORT = "18080"

STACK_SMOKE_OVERRIDES = (
    ("SP_ENVIRONMENT", "test"),
    ("SP_TELEGRAM_UPDATES_MODE", "disabled"),
    ("SP_TELEGRAM_BOT_TOKEN", "1:CI-STACK-SMOKE-PLACEHOLDER"),
    ("SP_GIGACHAT_CREDENTIALS", "ci-placeholder"),
    ("SP_GIGACHAT_SCOPE", "PERS"),
)


def _set_key(text: str, key: str, value: str) -> str:
    if re.search(rf"^{key}=", text, flags=re.MULTILINE) is not None:
        return re.sub(rf"^{key}=.*$", f"{key}={value}", text, count=1, flags=re.MULTILINE)
    return text.rstrip() + f"\n{key}={value}\n"


def _get_key(text: str, key: str) -> str:
    match = re.search(rf"^{key}=(.*)$", text, flags=re.MULTILINE)
    if match is None:
        return ""
    return match.group(1).strip()


def materialize(*, env_file: Path, stack_smoke: bool) -> None:
    """Write an ephemeral env file from ``.env.example``; never read repo ``.env``."""
    if env_file.resolve() == (ROOT / ".env").resolve() or ROOT in env_file.resolve().parents:
        msg = "CI env file must live outside the repository"
        raise SystemExit(msg)
    text = EXAMPLE_PATH.read_text()
    text = _set_key(text, "SP_DATA_KEK", base64.b64encode(secrets.token_bytes(32)).decode())
    text = _set_key(text, "SP_PSEUDONYM_PEPPER", base64.b64encode(secrets.token_bytes(32)).decode())
    text = _set_key(text, "SP_GIGACHAT_CREDENTIALS", "ci-placeholder")
    text = _set_key(text, "POSTGRES_PORT", CI_POSTGRES_PORT)
    text = _set_key(text, "VALKEY_PORT", CI_VALKEY_PORT)
    text = _set_key(text, "API_PORT", CI_API_PORT)
    text = _set_key(text, "MINIAPP_PORT", CI_MINIAPP_PORT)
    text = _set_key(text, "SP_HTTP_PORT", CI_API_PORT)
    user = _get_key(text, "POSTGRES_USER") or "svoi"
    password = _get_key(text, "POSTGRES_PASSWORD") or "svoi_local_dev_only"
    db_name = _get_key(text, "POSTGRES_DB") or "svoi_pravila"
    valkey_password = _get_key(text, "VALKEY_PASSWORD") or password
    text = _set_key(
        text,
        "SP_DATABASE_URL",
        f"postgresql+asyncpg://{user}:{password}@127.0.0.1:{CI_POSTGRES_PORT}/{db_name}",
    )
    text = _set_key(
        text,
        "SP_VALKEY_URL",
        f"redis://:{valkey_password}@127.0.0.1:{CI_VALKEY_PORT}/0",
    )
    if stack_smoke:
        for key, value in STACK_SMOKE_OVERRIDES:
            text = _set_key(text, key, value)
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(text)
    env_file.chmod(_ENV_MODE)
    mode = stat.S_IMODE(env_file.stat().st_mode)
    if mode != _ENV_MODE:
        env_file.chmod(_ENV_MODE)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--stack-smoke", action="store_true")
    args = parser.parse_args()
    materialize(env_file=args.env_file, stack_smoke=args.stack_smoke)


if __name__ == "__main__":
    main()
