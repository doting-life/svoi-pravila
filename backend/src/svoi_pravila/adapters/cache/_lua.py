"""Load packaged Lua scripts for Valkey adapters."""

from __future__ import annotations

from importlib import resources


def load_lua(name: str) -> str:
    """Return the UTF-8 body of ``adapters/cache/lua/<name>``."""
    return resources.files(__package__).joinpath("lua", name).read_text(encoding="utf-8")
