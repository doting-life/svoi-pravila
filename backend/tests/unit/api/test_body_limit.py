"""ASGI body-limit middleware for `/api/v1`."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from tests.fakes.clock import FakeClock
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.export_delivery import FakeExportDelivery
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.inline_reuse import make_inline_reuse
from tests.fakes.rate_limit import FakePseudonymizer, FakeRateLimiter
from tests.fakes.tokens import FakeTokenGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.support.init_data import build_webapp_init_data
from tests.support.miniapp_decode import build_miniapp_decode_bundle
from tests.unit.application.conftest import AppWorld

from svoi_pravila.adapters.channels.telegram.init_data import AiogramInitDataVerifier
from svoi_pravila.api.app import AppLifecycleHooks, create_app
from svoi_pravila.api.miniapp import MiniappDeps, MiniappRouterBindings, build_miniapp_router
from svoi_pravila.api.miniapp.body_limit import MAX_BODY_BYTES, BodyLimitMiddleware
from svoi_pravila.api.miniapp.errors import MiniappErrorCode
from svoi_pravila.application.errors import AccessNotGranted
from svoi_pravila.application.use_cases.accept_suggestion import AcceptSuggestion
from svoi_pravila.application.use_cases.archive_rule import ArchiveRule
from svoi_pravila.application.use_cases.check_readiness import CheckReadiness
from svoi_pravila.application.use_cases.create_contact import CreateContact
from svoi_pravila.application.use_cases.delete_my_account import DeleteMyAccount
from svoi_pravila.application.use_cases.dismiss_suggestion import DismissSuggestion
from svoi_pravila.application.use_cases.export_my_data import ExportMyData
from svoi_pravila.application.use_cases.get_onboarding_step import GetOnboardingStep
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramId
from svoi_pravila.application.use_cases.list_contacts import ListContacts
from svoi_pravila.application.use_cases.list_rules import ListRules
from svoi_pravila.application.use_cases.list_suggestions import ListSuggestions
from svoi_pravila.application.use_cases.propose_rule import ProposeRule
from svoi_pravila.application.use_cases.rename_contact import RenameContact
from svoi_pravila.application.use_cases.request_my_data_export import RequestMyDataExport
from svoi_pravila.application.use_cases.revoke_all_consents import RevokeAllConsents
from svoi_pravila.application.use_cases.set_active_contact import SetActiveContact
from svoi_pravila.config import Environment
from svoi_pravila.domain.access import AccessStatus
from svoi_pravila.domain.enums import ConsentKind

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_TOKEN = "9:UNIT-BODY"
_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
_TG = 40_014


@dataclass
class _CountingCreateContact:
    """Use-case stand-in that records calls and denies access."""

    calls: list[str] = field(default_factory=list)

    async def execute(self, _command: object) -> Any:
        self.calls.append("create_contact")
        raise AccessNotGranted(
            AccessStatus(
                age_confirmed=True,
                missing_consents=frozenset({ConsentKind.PERSONAL_DATA}),
                granted=False,
            )
        )


def _auth() -> dict[str, str]:
    raw = build_webapp_init_data(_TOKEN, user_id=_TG, auth_date=int(_NOW.timestamp()))
    return {"Authorization": f"tma {raw}"}


def _world() -> AppWorld:
    return AppWorld(
        uow_factory=InMemoryUnitOfWorkFactory(),
        clock=FakeClock(start=_NOW),
        ids=FakeIdGenerator(),
        tokens=FakeTokenGenerator(),
        catalog=FakeConsentCatalog(),
    )


def _app(world: AppWorld, *, create_contact: CreateContact | _CountingCreateContact) -> Any:
    auth = MiniappDeps(
        init_data_verifier=AiogramInitDataVerifier(
            SecretStr(_TOKEN), world.clock, max_age_seconds=3600
        ),
        rate_limiter=FakeRateLimiter(limit=120),
        pseudonymizer=FakePseudonymizer(),
        get_user_by_telegram_id=GetUserByTelegramId(world.uow_factory),
        get_onboarding_step=GetOnboardingStep(world.uow_factory, world.catalog),
    )
    reuse = make_inline_reuse(world.clock)
    decode_bundle = build_miniapp_decode_bundle(world)
    bindings = MiniappRouterBindings(
        auth=auth,
        list_contacts=ListContacts(world.uow_factory, world.catalog),
        create_contact=cast(CreateContact, create_contact),
        rename_contact=RenameContact(world.uow_factory, world.catalog),
        set_active_contact=SetActiveContact(world.uow_factory, world.catalog),
        list_rules=ListRules(world.uow_factory, world.catalog),
        propose_rule=ProposeRule(world.uow_factory, world.catalog, world.ids, world.clock),
        archive_rule=ArchiveRule(world.uow_factory, world.catalog, world.clock),
        list_suggestions=ListSuggestions(world.uow_factory, world.catalog),
        accept_suggestion=AcceptSuggestion(
            world.uow_factory, world.catalog, world.ids, world.clock
        ),
        dismiss_suggestion=DismissSuggestion(world.uow_factory, world.catalog, world.clock),
        request_my_data_export=RequestMyDataExport(
            ExportMyData(world.uow_factory, world.clock),
            FakeExportDelivery(),
        ),
        revoke_all_consents=RevokeAllConsents(world.uow_factory, world.clock, reuse),
        delete_my_account=DeleteMyAccount(
            world.uow_factory,
            world.ids,
            FakePseudonymizer(),
            world.clock,
            reuse,
        ),
        export_rate_limiter=FakeRateLimiter(limit=3),
        display_timezone="Europe/Moscow",
        decode_incoming=decode_bundle.decode_incoming,
        suggest_rule_from_decode=decode_bundle.suggest_rule_from_decode,
        prepared_results=decode_bundle.prepared_results,
        rule_sources=decode_bundle.rule_sources,
        pseudonymizer=decode_bundle.pseudonymizer,
        enable_test_routes=True,
    )
    return create_app(
        CheckReadiness(probes=(), timeout_seconds=1.0),
        Environment.TEST,
        AppLifecycleHooks(extra_routers=(build_miniapp_router(bindings),)),
    )


@pytest.mark.unit
async def test_content_length_over_limit_is_413_without_use_case() -> None:
    world = _world()
    await world.ensure_granted_user(_TG)
    counter = _CountingCreateContact()
    app = _app(world, create_contact=counter)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/contacts",
            headers={**_auth(), "Content-Length": str(MAX_BODY_BYTES + 1)},
            content=b"x" * (MAX_BODY_BYTES + 1),
        )
    assert response.status_code == 413
    assert response.json()["code"] == MiniappErrorCode.BODY_TOO_LARGE
    assert response.headers["cache-control"] == "no-store"
    assert counter.calls == []


@pytest.mark.unit
async def test_exact_limit_is_accepted_by_middleware() -> None:
    world = _world()
    app = _app(
        world,
        create_contact=CreateContact(world.uow_factory, world.catalog, world.ids, world.clock),
    )
    transport = ASGITransport(app=app)
    body = b"x" * MAX_BODY_BYTES
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/contacts",
            headers={"Content-Length": str(MAX_BODY_BYTES)},
            content=body,
        )
    assert response.status_code != 413
    assert response.status_code == 401


@pytest.mark.unit
async def test_malformed_content_length_is_400() -> None:
    messages: list[dict[str, Any]] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        _ = receive
        messages.append({"ran": True})
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = BodyLimitMiddleware(cast(ASGIApp, app))
    sent: list[MutableMapping[str, Any]] = []

    async def receive() -> MutableMapping[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/contacts",
        "raw_path": b"/api/v1/contacts",
        "query_string": b"",
        "headers": [(b"content-length", b"abc")],
        "client": ("127.0.0.1", 123),
        "server": ("test", 80),
    }
    await middleware(scope, receive, send)
    assert messages == []
    assert sent[0]["status"] == 400
    body = json.loads(cast(bytes, sent[1]["body"]))
    assert body["code"] == MiniappErrorCode.VALIDATION_ERROR


@pytest.mark.unit
async def test_chunked_oversized_body_never_calls_app() -> None:
    ran: list[bool] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await receive()
        ran.append(True)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = BodyLimitMiddleware(cast(ASGIApp, app))
    chunks: list[MutableMapping[str, Any]] = [
        {"type": "http.request", "body": b"a" * (MAX_BODY_BYTES // 2), "more_body": True},
        {"type": "http.request", "body": b"b" * (MAX_BODY_BYTES // 2 + 1), "more_body": True},
        {"type": "http.request", "body": b"tail", "more_body": False},
    ]
    index = 0

    async def receive() -> MutableMapping[str, Any]:
        nonlocal index
        if index >= len(chunks):
            return {"type": "http.disconnect"}
        message = chunks[index]
        index += 1
        return message

    sent: list[MutableMapping[str, Any]] = []

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/me",
        "raw_path": b"/api/v1/me",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 123),
        "server": ("test", 80),
    }
    await middleware(scope, receive, send)
    assert ran == []
    assert sent[0]["status"] == 413
    body = json.loads(cast(bytes, sent[1]["body"]))
    assert body["code"] == MiniappErrorCode.BODY_TOO_LARGE


@pytest.mark.unit
async def test_body_limit_passthrough_non_http_and_disconnect() -> None:
    seen: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(str(scope["type"]))
        if scope["type"] != "http":
            return
        message = await receive()
        seen.append(str(message["type"]))
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = BodyLimitMiddleware(cast(ASGIApp, app))

    async def empty_receive() -> MutableMapping[str, Any]:
        return {"type": "http.disconnect"}

    async def send(_message: MutableMapping[str, Any]) -> None:
        return None

    await middleware({"type": "lifespan"}, empty_receive, send)
    assert seen == ["lifespan"]

    http_scope: Scope = {
        "type": "http",
        "path": "/api/v1/me",
        "headers": [],
        "method": "GET",
        "query_string": b"",
    }
    await middleware(http_scope, empty_receive, send)
    # Disconnect while reading yields an empty replayed body for the app.
    assert seen[-1] == "http.request"


@pytest.mark.unit
async def test_chunked_oversized_stops_without_draining() -> None:
    ran: list[bool] = []
    receive_calls = 0

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        ran.append(True)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = BodyLimitMiddleware(cast(ASGIApp, app))

    async def receive() -> MutableMapping[str, Any]:
        nonlocal receive_calls
        receive_calls += 1
        if receive_calls == 1:
            return {
                "type": "http.request",
                "body": b"x" * (MAX_BODY_BYTES + 1),
                "more_body": True,
            }
        # Would yield forever if the middleware kept draining.
        return {"type": "http.request", "body": b"y", "more_body": True}

    sent: list[MutableMapping[str, Any]] = []

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    scope: Scope = {
        "type": "http",
        "path": "/api/v1/me",
        "headers": [],
        "method": "POST",
        "query_string": b"",
    }
    await middleware(scope, receive, send)
    assert ran == []
    assert receive_calls == 1
    assert sent[0]["status"] == 413
    assert json.loads(cast(bytes, sent[1]["body"]))["code"] == MiniappErrorCode.BODY_TOO_LARGE


@pytest.mark.unit
async def test_body_limit_skips_non_request_messages() -> None:
    seen: list[int] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        message = await receive()
        body = message.get("body", b"")
        seen.append(len(body) if isinstance(body, (bytes, bytearray)) else 0)
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = BodyLimitMiddleware(cast(ASGIApp, app))
    messages: list[MutableMapping[str, Any]] = [
        {"type": "http.request", "body": b"", "more_body": True},
        {"type": "something.else"},
        {"type": "http.request", "body": b"ok", "more_body": False},
    ]
    index = 0

    async def receive() -> MutableMapping[str, Any]:
        nonlocal index
        message = messages[index]
        index += 1
        return message

    async def send(_message: MutableMapping[str, Any]) -> None:
        return None

    await middleware(
        {
            "type": "http",
            "path": "/api/v1/me",
            "headers": [],
            "method": "POST",
            "query_string": b"",
        },
        receive,
        send,
    )
    assert seen == [2]


@pytest.mark.unit
async def test_non_miniapp_path_is_not_limited() -> None:
    world = _world()
    app = _app(
        world,
        create_contact=CreateContact(world.uow_factory, world.catalog, world.ids, world.clock),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
