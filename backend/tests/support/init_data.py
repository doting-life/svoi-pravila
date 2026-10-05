"""Build Telegram WebApp initData strings with real HMAC (no network)."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from urllib.parse import urlencode


@dataclass(frozen=True, slots=True)
class InitDataOptions:
    """Optional initData fields and tamper knobs for tests."""

    extra: dict[str, str] | None = None
    include_user: bool = True
    hash_override: str | None = None
    omit_hash: bool = False


def build_webapp_init_data(
    bot_token: str,
    *,
    user_id: int,
    auth_date: int,
    options: InitDataOptions | None = None,
) -> str:
    """Return a query-string initData signed with ``bot_token``."""
    opts = options if options is not None else InitDataOptions()
    fields: dict[str, str] = {"auth_date": str(auth_date)}
    if opts.include_user:
        fields["user"] = json.dumps(
            {"id": user_id, "first_name": "Test", "username": "test_user"},
            separators=(",", ":"),
        )
    if opts.extra:
        fields.update(opts.extra)
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret_key = hmac.new(key=b"WebAppData", msg=bot_token.encode(), digestmod=hashlib.sha256)
    digest = hmac.new(
        key=secret_key.digest(),
        msg=data_check_string.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()
    if not opts.omit_hash:
        fields["hash"] = digest if opts.hash_override is None else opts.hash_override
    return urlencode(fields)
