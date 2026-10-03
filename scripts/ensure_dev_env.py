"""Ensure local .env has a field-encryption KEK without overwriting an existing one."""

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


def ensure_env_permissions(path: Path) -> None:
    """Restrict ``path`` to owner read/write only (0600)."""
    path.chmod(_ENV_MODE)


def main(env_path: Path = ENV_PATH, example_path: Path = EXAMPLE_PATH) -> None:
    """Fill missing SP_DATA_KEK / SP_DATA_KEK_ID in .env from example defaults."""
    text = env_path.read_text() if env_path.exists() else example_path.read_text()
    example = example_path.read_text()

    for key in ("SP_DATA_KEK", "SP_DATA_KEK_ID"):
        if re.search(rf"^{key}=", text, flags=re.MULTILINE) is None:
            match = re.search(rf"^{key}=(.*)$", example, flags=re.MULTILINE)
            line = match.group(0) if match is not None else f"{key}="
            text = text.rstrip() + "\n" + line + "\n"

    match = re.search(r"^SP_DATA_KEK=(.*)$", text, flags=re.MULTILINE)
    current = match.group(1).strip() if match is not None else ""
    if not current:
        key = base64.b64encode(secrets.token_bytes(32)).decode()
        text = re.sub(
            r"^SP_DATA_KEK=.*$",
            f"SP_DATA_KEK={key}",
            text,
            count=1,
            flags=re.MULTILINE,
        )

    env_path.write_text(text)
    ensure_env_permissions(env_path)
    # Drop execute/group/other bits if the platform preserved them somehow.
    mode = stat.S_IMODE(env_path.stat().st_mode)
    if mode != _ENV_MODE:
        ensure_env_permissions(env_path)


if __name__ == "__main__":
    main()
