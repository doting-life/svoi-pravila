"""Fill a stack-smoke env file outside the repository."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from ensure_dev_env import main as ensure_dev_env


def _set_key(text: str, key: str, value: str) -> str:
    if re.search(rf"^{key}=", text, flags=re.MULTILINE) is not None:
        return re.sub(rf"^{key}=.*$", f"{key}={value}", text, count=1, flags=re.MULTILINE)
    return text.rstrip() + f"\n{key}={value}\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    args = parser.parse_args()
    ensure_dev_env(env_path=args.env_file)
    text = args.env_file.read_text()
    for key, value in (
        ("SP_ENVIRONMENT", "test"),
        ("SP_TELEGRAM_UPDATES_MODE", "disabled"),
        ("SP_TELEGRAM_BOT_TOKEN", "1:CI-STACK-SMOKE-PLACEHOLDER"),
        ("SP_GIGACHAT_CREDENTIALS", "ci-placeholder"),
        ("SP_GIGACHAT_SCOPE", "PERS"),
    ):
        text = _set_key(text, key, value)
    args.env_file.write_text(text)


if __name__ == "__main__":
    main()
