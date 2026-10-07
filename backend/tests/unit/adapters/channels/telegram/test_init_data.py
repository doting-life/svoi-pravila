"""Unit tests for AiogramInitDataVerifier (real HMAC vectors, no network)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr
from tests.fakes.clock import FakeClock
from tests.support.init_data import InitDataOptions, build_webapp_init_data

from svoi_pravila.adapters.channels.telegram.init_data import AiogramInitDataVerifier
from svoi_pravila.application.ports.init_data import InitDataExpired, InitDataInvalid

_TOKEN = "123456:ABC-DEF"
_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


def _verifier(clock: FakeClock, *, max_age: int = 3600) -> AiogramInitDataVerifier:
    return AiogramInitDataVerifier(SecretStr(_TOKEN), clock, max_age_seconds=max_age)


@pytest.mark.unit
def test_verify_valid_init_data() -> None:
    clock = FakeClock(start=_NOW)
    auth_ts = int(_NOW.timestamp())
    raw = build_webapp_init_data(
        _TOKEN,
        user_id=42,
        auth_date=auth_ts,
        options=InitDataOptions(extra={"query_id": "QQ"}),
    )
    result = _verifier(clock).verify(raw)
    assert result.telegram_user_id.value == 42
    assert result.auth_date == _NOW
    assert set(result.__dataclass_fields__) == {"telegram_user_id", "auth_date", "start_param"}
    assert result.start_param is None


@pytest.mark.unit
def test_verify_wrong_hash() -> None:
    clock = FakeClock(start=_NOW)
    raw = build_webapp_init_data(
        _TOKEN,
        user_id=1,
        auth_date=int(_NOW.timestamp()),
        options=InitDataOptions(hash_override="0" * 64),
    )
    with pytest.raises(InitDataInvalid):
        _verifier(clock).verify(raw)


@pytest.mark.unit
def test_verify_tampered_user_id() -> None:
    clock = FakeClock(start=_NOW)
    raw = build_webapp_init_data(_TOKEN, user_id=1, auth_date=int(_NOW.timestamp()))
    tampered = raw.replace("%7B%22id%22%3A1", "%7B%22id%22%3A9")
    with pytest.raises(InitDataInvalid):
        _verifier(clock).verify(tampered)


@pytest.mark.unit
def test_verify_missing_hash() -> None:
    clock = FakeClock(start=_NOW)
    raw = build_webapp_init_data(
        _TOKEN,
        user_id=1,
        auth_date=int(_NOW.timestamp()),
        options=InitDataOptions(omit_hash=True),
    )
    with pytest.raises(InitDataInvalid):
        _verifier(clock).verify(raw)


@pytest.mark.unit
def test_verify_expired() -> None:
    clock = FakeClock(start=_NOW)
    auth_ts = int((_NOW - timedelta(seconds=3601)).timestamp())
    raw = build_webapp_init_data(_TOKEN, user_id=1, auth_date=auth_ts)
    with pytest.raises(InitDataExpired):
        _verifier(clock, max_age=3600).verify(raw)


@pytest.mark.unit
def test_verify_future_skew_beyond_60s_invalid() -> None:
    clock = FakeClock(start=_NOW)
    auth_ts = int((_NOW + timedelta(seconds=61)).timestamp())
    raw = build_webapp_init_data(_TOKEN, user_id=1, auth_date=auth_ts)
    with pytest.raises(InitDataInvalid):
        _verifier(clock).verify(raw)


@pytest.mark.unit
def test_verify_future_skew_within_60s_ok() -> None:
    clock = FakeClock(start=_NOW)
    auth_ts = int((_NOW + timedelta(seconds=30)).timestamp())
    raw = build_webapp_init_data(_TOKEN, user_id=7, auth_date=auth_ts)
    result = _verifier(clock).verify(raw)
    assert result.telegram_user_id.value == 7


@pytest.mark.unit
def test_verify_bot_token_mismatch() -> None:
    clock = FakeClock(start=_NOW)
    raw = build_webapp_init_data("other:TOKEN", user_id=1, auth_date=int(_NOW.timestamp()))
    with pytest.raises(InitDataInvalid):
        _verifier(clock).verify(raw)


@pytest.mark.unit
def test_verify_empty_raw_invalid() -> None:
    clock = FakeClock(start=_NOW)
    with pytest.raises(InitDataInvalid):
        _verifier(clock).verify("   ")


@pytest.mark.unit
def test_verify_missing_user_invalid() -> None:
    clock = FakeClock(start=_NOW)
    raw = build_webapp_init_data(
        _TOKEN,
        user_id=1,
        auth_date=int(_NOW.timestamp()),
        options=InitDataOptions(include_user=False),
    )
    with pytest.raises(InitDataInvalid):
        _verifier(clock).verify(raw)


@pytest.mark.unit
def test_verify_naive_auth_date_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock(start=_NOW)
    parsed = SimpleNamespace(
        user=SimpleNamespace(id=99),
        auth_date=datetime(2026, 6, 1, 12, 0, 0),
        start_param=None,
    )
    monkeypatch.setattr(
        "svoi_pravila.adapters.channels.telegram.init_data.safe_parse_webapp_init_data",
        lambda _token, _raw: parsed,
    )
    result = _verifier(clock).verify("unused")
    assert result.telegram_user_id.value == 99
    assert result.auth_date == _NOW
    assert result.auth_date.tzinfo is UTC


@pytest.mark.unit
def test_verify_aware_non_utc_auth_date(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock(start=_NOW)
    parsed = SimpleNamespace(
        user=SimpleNamespace(id=11),
        auth_date=_NOW.astimezone(ZoneInfo("Europe/Moscow")),
        start_param="inv_abc",
    )
    monkeypatch.setattr(
        "svoi_pravila.adapters.channels.telegram.init_data.safe_parse_webapp_init_data",
        lambda _token, _raw: parsed,
    )
    result = _verifier(clock).verify("unused")
    assert result.auth_date == _NOW
    assert result.start_param == "inv_abc"


@pytest.mark.unit
def test_verify_start_param_passed_through() -> None:
    clock = FakeClock(start=_NOW)
    auth_ts = int(_NOW.timestamp())
    raw = build_webapp_init_data(
        _TOKEN,
        user_id=42,
        auth_date=auth_ts,
        options=InitDataOptions(extra={"start_param": "inv_token123"}),
    )
    result = _verifier(clock).verify(raw)
    assert result.start_param == "inv_token123"
