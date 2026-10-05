"""Russian message catalog for the mini-app API."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources


@lru_cache(maxsize=1)
def load_ru_messages() -> dict[str, str]:
    """Load C0 error messages keyed by MiniappErrorCode value."""
    raw = resources.files(__package__).joinpath("ru.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        msg = "mini-app localization must be a JSON object"
        raise TypeError(msg)
    messages: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, str) or not value:
            msg = "mini-app localization entries must be non-empty strings"
            raise TypeError(msg)
        messages[key] = value
    return messages
