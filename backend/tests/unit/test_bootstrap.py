"""Composition root wiring tests."""

from __future__ import annotations

import pytest
from aiogram import Bot
from aiogram.methods import DeleteMyCommands, DeleteWebhook, SetWebhook
from httpx import ASGITransport, AsyncClient

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.factory import build_telegram_lifecycle
from svoi_pravila.adapters.channels.telegram.lifecycle import ExtraTasks
from svoi_pravila.bootstrap import create_application
from svoi_pravila.config import Environment, Settings, TelegramUpdatesMode
from tests.factories import make_settings
from tests.fakes.concurrency import FakeConcurrencyGuard
from tests.fakes.probes import FailingProbe, OkProbe
from tests.fakes.rate_limit import FakeRateLimiter, FakeUpdateDeduplicator
from tests.fakes.telegram_session import FakeTelegramSession


class _FakeEngine:
    """Stand-in engine for bootstrap wiring tests."""


class _FakeValkey:
    """Stand-in Valkey client for bootstrap wiring tests."""

    def register_script(self, script: str) -> object:
        async def _run(*, keys: list[str], args: list[str]) -> int:
            _ = keys, args
            return 0

        _ = script
        return _run


class _FakePrepared:
    """Stand-in prepared-result store for bootstrap wiring tests."""

    async def store(self, user_pseudonym: str, variant: object) -> str:
        _ = user_pseudonym, variant
        return "p_" + ("A" * 64)

    async def redeem(self, user_pseudonym: str, token: str) -> object:
        _ = user_pseudonym, token
        msg = "not used"
        raise LookupError(msg)

    async def delete(self, user_pseudonym: str, token: str) -> None:
        _ = user_pseudonym, token


def _patch_infrastructure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "svoi_pravila.bootstrap.configure_logging",
        lambda _settings, _stream: None,
    )
    monkeypatch.setattr("svoi_pravila.bootstrap.create_engine", lambda _settings: _FakeEngine())
    monkeypatch.setattr("svoi_pravila.bootstrap.create_client", lambda _settings: _FakeValkey())
    monkeypatch.setattr(
        "svoi_pravila.bootstrap.create_gigachat_client",
        lambda _settings: object(),
    )
    monkeypatch.setattr(
        "svoi_pravila.bootstrap.DatabaseProbe",
        lambda _engine: OkProbe("database"),
    )
    monkeypatch.setattr(
        "svoi_pravila.bootstrap.ValkeyProbe",
        lambda _client: OkProbe("valkey"),
    )

    async def _noop(_obj: object) -> None:
        return None

    monkeypatch.setattr("svoi_pravila.bootstrap.close_client", _noop)
    monkeypatch.setattr("svoi_pravila.bootstrap.dispose_engine", _noop)
    monkeypatch.setattr("svoi_pravila.bootstrap.close_gigachat_client", _noop)
    monkeypatch.setattr(
        "svoi_pravila.bootstrap.ValkeyRateLimiter",
        lambda _client, *, limit, window_seconds, key_prefix="tg:rl": FakeRateLimiter(limit=limit),
    )
    monkeypatch.setattr(
        "svoi_pravila.bootstrap.ValkeyConcurrencyGuard",
        lambda _client: FakeConcurrencyGuard(),
    )
    monkeypatch.setattr(
        "svoi_pravila.bootstrap.ValkeyUpdateDeduplicator",
        lambda _client, *, ttl_seconds: FakeUpdateDeduplicator(),
    )
    monkeypatch.setattr(
        "svoi_pravila.bootstrap.ValkeyPreparedResults",
        lambda _client, *, ttl_seconds, key_prefix="tg:prepared": _FakePrepared(),
    )


def _patch_lifecycle_with_session(
    monkeypatch: pytest.MonkeyPatch,
    session: FakeTelegramSession,
) -> None:
    def _build(
        settings: Settings,
        deps: TelegramDeps,
        *,
        bot: Bot | None = None,
        extra_tasks: ExtraTasks | None = None,
    ) -> object:
        _ = bot
        return build_telegram_lifecycle(
            settings,
            deps,
            bot=Bot(token="1:TEST", session=session),
            extra_tasks=extra_tasks,
        )

    monkeypatch.setattr("svoi_pravila.bootstrap.build_telegram_lifecycle", _build)


@pytest.mark.unit
async def test_create_application_wires_probes_into_readyz(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings()
    engine = _FakeEngine()
    valkey = _FakeValkey()
    closed: list[str] = []

    monkeypatch.setattr(
        "svoi_pravila.bootstrap.configure_logging",
        lambda _settings, _stream: None,
    )
    monkeypatch.setattr("svoi_pravila.bootstrap.create_engine", lambda _settings: engine)
    monkeypatch.setattr("svoi_pravila.bootstrap.create_client", lambda _settings: valkey)
    monkeypatch.setattr(
        "svoi_pravila.bootstrap.create_gigachat_client",
        lambda _settings: object(),
    )
    monkeypatch.setattr(
        "svoi_pravila.bootstrap.DatabaseProbe",
        lambda _engine: OkProbe("database"),
    )
    monkeypatch.setattr(
        "svoi_pravila.bootstrap.ValkeyProbe",
        lambda _client: OkProbe("valkey"),
    )

    async def close_client(_client: object) -> None:
        closed.append("valkey")

    async def dispose_engine(_engine: object) -> None:
        closed.append("engine")

    async def close_gigachat(_client: object) -> None:
        closed.append("gigachat")

    monkeypatch.setattr("svoi_pravila.bootstrap.close_client", close_client)
    monkeypatch.setattr("svoi_pravila.bootstrap.dispose_engine", dispose_engine)
    monkeypatch.setattr("svoi_pravila.bootstrap.close_gigachat_client", close_gigachat)

    app = create_application(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/readyz")
            assert response.status_code == 200
            body = response.json()
            assert body["ready"] is True
            assert {p["name"] for p in body["probes"]} == {"database", "valkey"}
    assert closed == ["gigachat", "valkey", "engine"]


@pytest.mark.unit
async def test_create_application_readyz_failed_when_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings()
    _patch_infrastructure(monkeypatch)
    monkeypatch.setattr(
        "svoi_pravila.bootstrap.DatabaseProbe",
        lambda _engine: FailingProbe("database"),
    )

    app = create_application(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["probes"][0]["status"] == "failed"


@pytest.mark.unit
async def test_create_application_disabled_skips_telegram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(telegram_updates_mode=TelegramUpdatesMode.DISABLED)
    _patch_infrastructure(monkeypatch)
    built: list[str] = []

    def _fail_build(*_args: object, **_kwargs: object) -> object:
        built.append("lifecycle")
        msg = "must not build telegram when disabled"
        raise AssertionError(msg)

    monkeypatch.setattr("svoi_pravila.bootstrap.build_telegram_lifecycle", _fail_build)
    app = create_application(settings)
    async with app.router.lifespan_context(app):
        assert built == []
    assert not any(
        getattr(route, "path", "").startswith("/telegram/webhook") for route in app.routes
    )
    assert not any(getattr(route, "path", "") == "/api/v1/me" for route in app.routes)


@pytest.mark.unit
async def test_create_application_mounts_miniapp_when_bot_token_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(
        telegram_updates_mode=TelegramUpdatesMode.DISABLED,
        telegram_bot_token="1:TEST",
    )
    _patch_infrastructure(monkeypatch)
    app = create_application(settings)
    assert app.url_path_for("get_me") == "/api/v1/me"


@pytest.mark.unit
async def test_create_application_polling_lifespan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeTelegramSession()
    settings = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token="1:TEST",
    )
    _patch_infrastructure(monkeypatch)
    _patch_lifecycle_with_session(monkeypatch, session)

    app = create_application(settings)
    async with app.router.lifespan_context(app):
        kinds = {type(req) for req in session.requests}
        assert DeleteMyCommands in kinds
        assert DeleteWebhook in kinds
    assert session.closed is True


@pytest.mark.unit
async def test_create_application_webhook_lifespan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeTelegramSession()
    settings = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.WEBHOOK,
        telegram_bot_token="1:TEST",
        telegram_webhook_base_url="https://example.example",
        telegram_webhook_path_secret="p" * 32,
        telegram_webhook_secret_token="s" * 32,
    )
    _patch_infrastructure(monkeypatch)
    _patch_lifecycle_with_session(monkeypatch, session)

    app = create_application(settings)
    async with app.router.lifespan_context(app):
        kinds = {type(req) for req in session.requests}
        assert DeleteMyCommands in kinds
        assert SetWebhook in kinds
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/telegram/webhook/{'p' * 32}",
                headers={"X-Telegram-Bot-Api-Secret-Token": "s" * 32},
                content=b"{}",
            )
        assert response.status_code == 400
    assert session.closed is True
