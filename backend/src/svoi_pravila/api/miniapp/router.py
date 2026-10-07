"""FastAPI router for `/api/v1` mini-app endpoints."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse

from svoi_pravila.api.miniapp.decode_sse import DecodeStreamPorts, format_sse, iter_decode_sse
from svoi_pravila.api.miniapp.deps import (
    MiniappAuthContext,
    MiniappAuthenticator,
    MiniappDeps,
    invite_raw_token_from_start_param,
    no_store,
    parse_path_uuid,
)
from svoi_pravila.api.miniapp.errors import ErrorBody, LimitErrorBody, MiniappErrorCode
from svoi_pravila.api.miniapp.http import MiniappHttpError
from svoi_pravila.api.miniapp.schemas import (
    AcceptInviteRequest,
    AcceptSuggestionResponse,
    ConfirmTrueRequest,
    ConsentDocumentResponse,
    ContactItem,
    ContactListResponse,
    CreateContactRequest,
    CreateRuleRequest,
    DecodeRequest,
    DismissSuggestionResponse,
    ExportDownloadResponse,
    GrantConsentRequest,
    InviteResolveResponse,
    InviteResponse,
    MeResponse,
    PrivacyTextsResponse,
    RenameContactRequest,
    RuleItem,
    RuleListResponse,
    SuggestFromDecodeRequest,
    SuggestFromDecodeResponse,
    SuggestionItem,
    SuggestionListResponse,
)
from svoi_pravila.application.errors import NotFound
from svoi_pravila.application.ports.bot_username import BotUsername
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.prepared_results import PreparedResults
from svoi_pravila.application.ports.pseudonymizer import Pseudonymizer
from svoi_pravila.application.ports.quota_gate import QuotaGate
from svoi_pravila.application.ports.rate_limiter import RateLimiter
from svoi_pravila.application.ports.rule_sources import RuleSources
from svoi_pravila.application.rule_view import RuleListItemView, project_rules_for_list
from svoi_pravila.application.support_resources import load_crisis_lead, load_support_resources
from svoi_pravila.application.use_cases.accept_age_confirmation import (
    AcceptAgeConfirmation,
    AcceptAgeConfirmationCommand,
)
from svoi_pravila.application.use_cases.accept_invite import AcceptInvite, AcceptInviteCommand
from svoi_pravila.application.use_cases.accept_suggestion import (
    AcceptSuggestion,
    AcceptSuggestionCommand,
)
from svoi_pravila.application.use_cases.approve_rule import ApproveRule, ApproveRuleCommand
from svoi_pravila.application.use_cases.archive_rule import ArchiveRule, ArchiveRuleCommand
from svoi_pravila.application.use_cases.create_contact import (
    CreateContact,
    CreateContactCommand,
)
from svoi_pravila.application.use_cases.create_invite import CreateInvite, CreateInviteCommand
from svoi_pravila.application.use_cases.decode_incoming import DecodeIncoming
from svoi_pravila.application.use_cases.delete_my_account import (
    DeleteMyAccount,
    DeleteMyAccountCommand,
)
from svoi_pravila.application.use_cases.dismiss_suggestion import (
    DismissSuggestion,
    DismissSuggestionCommand,
)
from svoi_pravila.application.use_cases.get_consent_document import (
    GetConsentDocument,
    GetConsentDocumentQuery,
)
from svoi_pravila.application.use_cases.get_onboarding_step import GetOnboardingStepQuery
from svoi_pravila.application.use_cases.grant_consent import (
    GrantConsent,
    GrantConsentCommand,
    GrantConsentOutcome,
)
from svoi_pravila.application.use_cases.issue_export_download import (
    IssueExportDownload,
    IssueExportDownloadCommand,
)
from svoi_pravila.application.use_cases.leave_pair import LeavePair, LeavePairCommand
from svoi_pravila.application.use_cases.list_contacts import ListContacts, ListContactsCommand
from svoi_pravila.application.use_cases.list_rules import ListRules, ListRulesCommand
from svoi_pravila.application.use_cases.list_suggestions import (
    ListSuggestions,
    ListSuggestionsCommand,
)
from svoi_pravila.application.use_cases.propose_rule import ProposeRule, ProposeRuleCommand
from svoi_pravila.application.use_cases.reject_pending_rule import (
    RejectPendingRule,
    RejectPendingRuleCommand,
)
from svoi_pravila.application.use_cases.rename_contact import (
    RenameContact,
    RenameContactCommand,
)
from svoi_pravila.application.use_cases.resolve_invite import ResolveInvite, ResolveInviteCommand
from svoi_pravila.application.use_cases.revoke_all_consents import (
    RevokeAllConsents,
    RevokeAllConsentsCommand,
)
from svoi_pravila.application.use_cases.serve_export_download import (
    ServeExportDownload,
    ServeExportDownloadCommand,
)
from svoi_pravila.application.use_cases.set_active_contact import (
    SetActiveContact,
    SetActiveContactCommand,
)
from svoi_pravila.application.use_cases.suggest_rule_from_decode import (
    SuggestRuleFromDecode,
    SuggestRuleFromDecodeCommand,
    SuggestRuleFromDecodeOutcome,
)
from svoi_pravila.domain.contact import MAX_CONTACTS_PER_USER, Contact
from svoi_pravila.domain.enums import (
    ConsentKind,
    QuotaClass,
    RelationshipKind,
    RuleCategory,
    RuleStatus,
    UsageSurface,
)
from svoi_pravila.domain.ids import ContactId, RuleId, RuleSuggestionId, UserId
from svoi_pravila.domain.product_day import product_day
from svoi_pravila.domain.rules import MAX_OPEN_RULES_PER_SCOPE, PairScope, Rule
from svoi_pravila.domain.text import ContactLabel, RuleText
from svoi_pravila.domain.user import User
from svoi_pravila.privacy import load_privacy_catalog

_SSE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Type": "text/event-stream",
    "X-Accel-Buffering": "no",
}

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorBody},
    403: {"model": ErrorBody},
    404: {"model": ErrorBody},
    409: {"model": ErrorBody},
    413: {"model": ErrorBody},
    422: {"model": ErrorBody},
    429: {"model": ErrorBody},
    503: {"model": LimitErrorBody},
}

_LIMIT_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    **_ERROR_RESPONSES,
    429: {"model": LimitErrorBody},
    503: {"model": LimitErrorBody},
}


_EXPORT_RATE_PURPOSE = "export"


@dataclass(frozen=True, slots=True)
class MiniappRouterBindings:
    """Use cases and auth deps for the mini-app router."""

    auth: MiniappDeps
    list_contacts: ListContacts
    create_contact: CreateContact
    rename_contact: RenameContact
    set_active_contact: SetActiveContact
    create_invite: CreateInvite
    leave_pair: LeavePair
    list_rules: ListRules
    propose_rule: ProposeRule
    archive_rule: ArchiveRule
    approve_rule: ApproveRule
    reject_pending_rule: RejectPendingRule
    list_suggestions: ListSuggestions
    accept_suggestion: AcceptSuggestion
    dismiss_suggestion: DismissSuggestion
    accept_age: AcceptAgeConfirmation
    grant_consent: GrantConsent
    get_consent_document: GetConsentDocument
    resolve_invite: ResolveInvite
    accept_invite: AcceptInvite
    issue_export_download: IssueExportDownload
    serve_export_download: ServeExportDownload
    revoke_all_consents: RevokeAllConsents
    delete_my_account: DeleteMyAccount
    export_rate_limiter: RateLimiter
    display_timezone: str
    analytics_timezone: str
    miniapp_url: str | None
    decode_incoming: DecodeIncoming
    suggest_rule_from_decode: SuggestRuleFromDecode
    prepared_results: PreparedResults
    rule_sources: RuleSources
    pseudonymizer: Pseudonymizer
    quota_gate: QuotaGate
    clock: Clock
    bot_username: BotUsername
    enable_test_routes: bool = False


def _contact_item(contact: Contact) -> ContactItem:
    return ContactItem.model_validate(
        {
            "id": str(contact.id),
            "label": contact.label.value,
            "relationship": contact.relationship.value,
            "pair_id": str(contact.pair_id) if contact.pair_id is not None else None,
            "paired": contact.pair_id is not None,
            "created_at": contact.created_at,
        }
    )


def _needs_my_approval(rule: Rule, actor_id: UserId) -> bool:
    if not isinstance(rule.scope, PairScope):
        return False
    if rule.status is not RuleStatus.PROPOSED:
        return False
    pending = rule.pending_revision
    return pending is not None and actor_id in rule.approvers and actor_id != pending.author_id


def _rule_item_from_view(view: RuleListItemView, *, needs_my_approval: bool) -> RuleItem:
    return RuleItem.model_validate(
        {
            "id": str(view.rule_id),
            "category": view.category.value,
            "status": view.status.value,
            "text": view.text.value,
            "shared": view.shared,
            "needs_my_approval": needs_my_approval,
            "created_at": view.created_at,
            "effective_since": view.effective_since,
            "has_pending_edit": view.has_pending_edit,
        }
    )


def _rule_item(rule: Rule, actor_id: UserId) -> RuleItem:
    """Map a rule for mutation responses (list projection, or closed-status fallback)."""
    views = project_rules_for_list((rule,))
    if views:
        return _rule_item_from_view(views[0], needs_my_approval=_needs_my_approval(rule, actor_id))
    shared = isinstance(rule.scope, PairScope)
    effective = rule.effective_revision
    if effective is not None:
        text = effective.text
        effective_since = effective.effective_since
    else:
        latest = rule.revisions[-1]
        text = latest.text
        effective_since = latest.effective_since
    return RuleItem.model_validate(
        {
            "id": str(rule.id),
            "category": rule.category.value,
            "status": rule.status.value,
            "text": text.value,
            "shared": shared,
            "needs_my_approval": _needs_my_approval(rule, actor_id),
            "created_at": rule.created_at,
            "effective_since": effective_since,
            "has_pending_edit": False,
        }
    )


def build_miniapp_router(bindings: MiniappRouterBindings) -> APIRouter:
    """Create `/api/v1` routes bound to composition-root use cases."""
    authenticator = MiniappAuthenticator(bindings.auth)
    auth_dep = Annotated[MiniappAuthContext, Depends(authenticator.authenticate)]
    actor_dep = Annotated[User, Depends(authenticator.require_actor)]
    router = APIRouter(prefix="/api/v1", tags=["miniapp"])
    _register_me(router, bindings, auth_dep, actor_dep)
    _register_invites(router, bindings, auth_dep, actor_dep)
    _register_downloads(router, bindings)
    _register_contacts(router, bindings, actor_dep)
    _register_rules(router, bindings, actor_dep)
    _register_suggestions(router, bindings, actor_dep)
    _register_decode(router, bindings, actor_dep)
    if bindings.enable_test_routes:
        _register_test_routes(router)
    return router


def _register_test_routes(router: APIRouter) -> None:
    """TEST-only SSE probe for Caddy flush_interval smoke (no auth, no LLM)."""

    @router.get(
        "/_test/sse-flush",
        operation_id="testSseFlush",
        include_in_schema=False,
    )
    async def sse_flush_probe() -> StreamingResponse:
        async def frames() -> AsyncIterator[str]:
            yield format_sse("probe", {"phase": "first"})
            await asyncio.sleep(2.0)
            yield format_sse("probe", {"phase": "done"})

        return StreamingResponse(frames(), media_type="text/event-stream", headers=_SSE_HEADERS)


def _register_decode(
    router: APIRouter,
    bindings: MiniappRouterBindings,
    actor_dep: Any,
) -> None:
    """Register decode SSE and suggest-from-decode routes."""

    @router.post(
        "/decode",
        operation_id="decodeIncoming",
        responses={
            **_LIMIT_ERROR_RESPONSES,
            200: {
                "description": "Server-Sent Events stream (analysis, then one terminal event).",
                "content": {"text/event-stream": {}},
            },
        },
    )
    async def decode_incoming(
        body: DecodeRequest,
        request: Request,
        actor: actor_dep,
    ) -> StreamingResponse:
        stream_ports = DecodeStreamPorts(
            decode_incoming=bindings.decode_incoming,
            prepared_results=bindings.prepared_results,
            rule_sources=bindings.rule_sources,
            pseudonymizer=bindings.pseudonymizer,
        )
        _ = request  # ASGI cancels ``frames`` on client disconnect (no orphan tasks).
        agen = iter_decode_sse(
            stream_ports,
            actor=actor,
            telegram_user_id=actor.telegram_user_id,
            text=body.text,
        )
        # Peek the first frame so quota/budget errors become HTTP JSON before SSE opens.
        try:
            first = await agen.__anext__()
        except StopAsyncIteration:
            await agen.aclose()
            return StreamingResponse(iter(()), media_type="text/event-stream", headers=_SSE_HEADERS)
        except BaseException:
            await agen.aclose()
            raise

        async def frames() -> AsyncIterator[str]:
            # Do not poll ``is_disconnected`` between frames: after the terminal
            # event the client may already look disconnected, and breaking would
            # cancel DecodeIncoming before usage accounting runs.
            try:
                yield first
                async for frame in agen:
                    yield frame
            finally:
                await agen.aclose()

        return StreamingResponse(frames(), media_type="text/event-stream", headers=_SSE_HEADERS)

    @router.post(
        "/suggestions/from-decode",
        operation_id="suggestFromDecode",
        response_model=SuggestFromDecodeResponse,
        responses=_LIMIT_ERROR_RESPONSES,
    )
    async def suggest_from_decode(
        body: SuggestFromDecodeRequest,
        response: Response,
        actor: actor_dep,
    ) -> SuggestFromDecodeResponse:
        no_store(response)
        result = await bindings.suggest_rule_from_decode.execute(
            SuggestRuleFromDecodeCommand(
                telegram_user_id=actor.telegram_user_id,
                token=body.token,
                surface=UsageSurface.MINIAPP,
            )
        )
        suggestion = None
        if result.suggestion is not None:
            s = result.suggestion
            suggestion = SuggestionItem.model_validate(
                {
                    "id": str(s.id),
                    "category": s.category.value,
                    "text": s.text.value,
                    "source": s.source.value,
                    "firmness": s.firmness.value if s.firmness is not None else None,
                    "created_at": s.created_at,
                }
            )
        payload: dict[str, object] = {
            "outcome": result.outcome.value,
            "suggestion": suggestion,
        }
        if result.outcome is SuggestRuleFromDecodeOutcome.CRISIS:
            payload["lead"] = load_crisis_lead()
            payload["resources"] = list(load_support_resources())
        return SuggestFromDecodeResponse.model_validate(payload)


async def _build_me_response(
    bindings: MiniappRouterBindings, auth: MiniappAuthContext
) -> MeResponse:
    """Assemble GET /me payload (onboarding, limits, bot username, decode remaining)."""
    step_result = await bindings.auth.get_onboarding_step.execute(
        GetOnboardingStepQuery(telegram_user_id=auth.telegram_user_id)
    )
    step = step_result.step
    active = (
        str(auth.user.active_contact_id)
        if auth.user is not None and auth.user.active_contact_id is not None
        else None
    )
    username = bindings.bot_username.username
    if username is None or not username:
        raise MiniappHttpError(MiniappErrorCode.SERVICE_UNAVAILABLE, 503)
    day = product_day(bindings.clock.now(), bindings.analytics_timezone)
    quota_pseudonym = bindings.pseudonymizer.pseudonymize(
        "decode_quota", str(auth.telegram_user_id.value)
    )
    decode_remaining = await bindings.quota_gate.remaining(quota_pseudonym, QuotaClass.DECODE, day)
    return MeResponse.model_validate(
        {
            "onboarding_step": step.kind.value,
            "consent_kind": (step.consent_kind.value if step.consent_kind is not None else None),
            "consent_version": step.consent_version,
            "active_contact_id": active,
            "account_exists": auth.user is not None,
            "max_contacts": MAX_CONTACTS_PER_USER,
            "max_open_rules": MAX_OPEN_RULES_PER_SCOPE,
            "display_timezone": bindings.display_timezone,
            "bot_username": username,
            "decode_remaining": decode_remaining,
        }
    )


def _register_me(
    router: APIRouter,
    bindings: MiniappRouterBindings,
    auth_dep: Any,
    actor_dep: Any,
) -> None:
    """Register me routes."""

    @router.get(
        "/me",
        operation_id="getMe",
        response_model=MeResponse,
        responses=_ERROR_RESPONSES,
    )
    async def get_me(
        response: Response,
        auth: auth_dep,
    ) -> MeResponse:
        no_store(response)
        return await _build_me_response(bindings, auth)

    @router.post(
        "/me/age-confirmation",
        operation_id="acceptAgeConfirmation",
        status_code=status.HTTP_204_NO_CONTENT,
        response_model=None,
        responses=_ERROR_RESPONSES,
    )
    async def accept_age_confirmation(
        response: Response,
        auth: auth_dep,
    ) -> Response:
        no_store(response)
        await bindings.accept_age.execute(
            AcceptAgeConfirmationCommand(telegram_user_id=auth.telegram_user_id)
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get(
        "/consents/{kind}/document",
        operation_id="getConsentDocument",
        response_model=ConsentDocumentResponse,
        responses=_ERROR_RESPONSES,
    )
    async def get_consent_document(
        kind: str,
        response: Response,
        auth: auth_dep,
    ) -> ConsentDocumentResponse:
        _ = auth
        no_store(response)
        try:
            consent_kind = ConsentKind(kind)
        except ValueError as exc:
            raise MiniappHttpError(MiniappErrorCode.NOT_FOUND, 404) from exc
        result = await bindings.get_consent_document.execute(
            GetConsentDocumentQuery(kind=consent_kind)
        )
        document = result.document
        return ConsentDocumentResponse.model_validate(
            {
                "kind": document.kind.value,
                "version": document.version,
                "text": document.text,
            }
        )

    @router.post(
        "/me/consents",
        operation_id="grantConsent",
        status_code=status.HTTP_204_NO_CONTENT,
        response_model=None,
        responses=_ERROR_RESPONSES,
    )
    async def grant_consent(
        body: GrantConsentRequest,
        response: Response,
        auth: auth_dep,
    ) -> Response:
        no_store(response)
        if auth.user is None:
            raise MiniappHttpError(MiniappErrorCode.ONBOARDING_REQUIRED, 403)
        result = await bindings.grant_consent.execute(
            GrantConsentCommand(
                user_id=auth.user.id,
                kind=ConsentKind(body.kind),
                text_version=body.text_version,
            )
        )
        if result.outcome is GrantConsentOutcome.STALE_VERSION:
            raise MiniappHttpError(MiniappErrorCode.CONSENT_STALE, 409)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post(
        "/me/export",
        operation_id="exportMyData",
        status_code=status.HTTP_200_OK,
        response_model=ExportDownloadResponse,
        responses=_ERROR_RESPONSES,
    )
    async def export_my_data(
        response: Response,
        auth: auth_dep,
    ) -> ExportDownloadResponse:
        no_store(response)
        pseudonym = bindings.auth.pseudonymizer.pseudonymize(
            _EXPORT_RATE_PURPOSE,
            str(auth.telegram_user_id.value),
        )
        decision = await bindings.export_rate_limiter.check(pseudonym)
        if not decision.allowed:
            raise MiniappHttpError(MiniappErrorCode.RATE_LIMITED, 429)
        if bindings.miniapp_url is None:
            raise MiniappHttpError(MiniappErrorCode.SERVICE_UNAVAILABLE, 503)
        result = await bindings.issue_export_download.execute(
            IssueExportDownloadCommand(telegram_user_id=auth.telegram_user_id)
        )
        download_url = f"{bindings.miniapp_url}/api/v1/downloads/{result.grant.raw_token}"
        return ExportDownloadResponse(
            download_url=download_url,
            expires_at=result.grant.expires_at,
        )

    @router.get(
        "/privacy/texts",
        operation_id="getPrivacyTexts",
        response_model=PrivacyTextsResponse,
        responses=_ERROR_RESPONSES,
    )
    async def get_privacy_texts(
        response: Response,
        auth: auth_dep,
    ) -> PrivacyTextsResponse:
        _ = auth
        no_store(response)
        catalog = load_privacy_catalog()
        return PrivacyTextsResponse.model_validate(
            {
                "export": {
                    "description": catalog.export.description,
                    "sections": catalog.export.sections,
                },
                "revoke": {
                    "description": catalog.revoke.description,
                    "confirm": catalog.revoke.confirm,
                },
                "delete": {
                    "description": catalog.delete.description,
                    "confirm": catalog.delete.confirm,
                },
                "leave_pair": {
                    "description": catalog.leave_pair.description,
                    "confirm": catalog.leave_pair.confirm,
                },
            }
        )

    @router.post(
        "/me/consents/revoke",
        operation_id="revokeAllConsents",
        status_code=status.HTTP_204_NO_CONTENT,
        response_model=None,
        responses=_ERROR_RESPONSES,
    )
    async def revoke_all_consents(
        response: Response,
        actor: actor_dep,
        body: ConfirmTrueRequest,
    ) -> Response:
        _ = body
        no_store(response)
        await bindings.revoke_all_consents.execute(
            RevokeAllConsentsCommand(telegram_user_id=actor.telegram_user_id)
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post(
        "/me/delete",
        operation_id="deleteMyAccount",
        status_code=status.HTTP_204_NO_CONTENT,
        response_model=None,
        responses=_ERROR_RESPONSES,
    )
    async def delete_my_account(
        response: Response,
        auth: auth_dep,
        body: ConfirmTrueRequest,
    ) -> Response:
        _ = body
        no_store(response)
        if auth.user is None:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        await bindings.delete_my_account.execute(
            DeleteMyAccountCommand(telegram_user_id=auth.telegram_user_id)
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)


def _register_contacts(
    router: APIRouter,
    bindings: MiniappRouterBindings,
    actor_dep: Any,
) -> None:
    """Register contacts routes."""

    @router.get(
        "/contacts",
        operation_id="listContacts",
        response_model=ContactListResponse,
        responses=_ERROR_RESPONSES,
    )
    async def list_contacts(
        response: Response,
        actor: actor_dep,
    ) -> ContactListResponse:
        no_store(response)
        result = await bindings.list_contacts.execute(ListContactsCommand(actor_id=actor.id))
        return ContactListResponse(contacts=[_contact_item(c) for c in result.contacts])

    @router.post(
        "/contacts",
        operation_id="createContact",
        response_model=ContactItem,
        status_code=status.HTTP_201_CREATED,
        responses=_ERROR_RESPONSES,
    )
    async def create_contact(
        body: CreateContactRequest,
        response: Response,
        actor: actor_dep,
    ) -> ContactItem:
        no_store(response)
        result = await bindings.create_contact.execute(
            CreateContactCommand(
                actor_id=actor.id,
                label=ContactLabel(body.label),
                relationship=RelationshipKind(body.relationship),
            )
        )
        return _contact_item(result.contact)

    @router.patch(
        "/contacts/{contact_id}",
        operation_id="renameContact",
        response_model=ContactItem,
        responses=_ERROR_RESPONSES,
    )
    async def rename_contact(
        contact_id: str,
        body: RenameContactRequest,
        response: Response,
        actor: actor_dep,
    ) -> ContactItem:
        no_store(response)
        cid = ContactId(parse_path_uuid(contact_id))
        result = await bindings.rename_contact.execute(
            RenameContactCommand(
                actor_id=actor.id,
                contact_id=cid,
                label=ContactLabel(body.label),
            )
        )
        return _contact_item(result.contact)

    @router.post(
        "/contacts/{contact_id}/activate",
        operation_id="activateContact",
        status_code=status.HTTP_204_NO_CONTENT,
        responses=_ERROR_RESPONSES,
    )
    async def activate_contact(
        contact_id: str,
        response: Response,
        actor: actor_dep,
    ) -> Response:
        no_store(response)
        cid = ContactId(parse_path_uuid(contact_id))
        await bindings.set_active_contact.execute(
            SetActiveContactCommand(actor_id=actor.id, contact_id=cid)
        )
        return Response(
            status_code=status.HTTP_204_NO_CONTENT,
            headers={"Cache-Control": "no-store"},
        )

    @router.post(
        "/contacts/{contact_id}/invite",
        operation_id="createInvite",
        response_model=InviteResponse,
        status_code=status.HTTP_201_CREATED,
        responses=_ERROR_RESPONSES,
    )
    async def create_invite(
        contact_id: str,
        response: Response,
        actor: actor_dep,
    ) -> InviteResponse:
        no_store(response)
        cid = ContactId(parse_path_uuid(contact_id))
        created = await bindings.create_invite.execute(
            CreateInviteCommand(actor_id=actor.id, contact_id=cid)
        )
        username = bindings.bot_username.username or "test_bot"
        link = f"https://t.me/{username}?startapp=inv_{created.raw_token}"
        return InviteResponse(link=link, expires_at=created.invite.expires_at)

    @router.post(
        "/contacts/{contact_id}/leave",
        operation_id="leavePair",
        status_code=status.HTTP_204_NO_CONTENT,
        responses=_ERROR_RESPONSES,
    )
    async def leave_pair(
        contact_id: str,
        body: ConfirmTrueRequest,
        response: Response,
        actor: actor_dep,
    ) -> Response:
        _ = body
        no_store(response)
        cid = ContactId(parse_path_uuid(contact_id))
        listed = await bindings.list_contacts.execute(ListContactsCommand(actor_id=actor.id))
        contact = next((item for item in listed.contacts if item.id == cid), None)
        if contact is None:
            raise NotFound()
        if contact.pair_id is None:
            raise MiniappHttpError(MiniappErrorCode.CONTACT_NOT_PAIRED, 409)
        await bindings.leave_pair.execute(
            LeavePairCommand(actor_id=actor.id, pair_id=contact.pair_id)
        )
        return Response(
            status_code=status.HTTP_204_NO_CONTENT,
            headers={"Cache-Control": "no-store"},
        )


def _register_invites(
    router: APIRouter,
    bindings: MiniappRouterBindings,
    auth_dep: Any,
    actor_dep: Any,
) -> None:
    """Register invite resolve/accept routes (token from verified start_param)."""

    @router.post(
        "/invites/resolve",
        operation_id="resolveInvite",
        response_model=InviteResolveResponse,
        responses=_ERROR_RESPONSES,
    )
    async def resolve_invite(
        response: Response,
        auth: auth_dep,
    ) -> InviteResolveResponse:
        no_store(response)
        if auth.user is None:
            raise MiniappHttpError(MiniappErrorCode.ONBOARDING_REQUIRED, 403)
        raw_token = invite_raw_token_from_start_param(auth.start_param)
        result = await bindings.resolve_invite.execute(
            ResolveInviteCommand(actor_id=auth.user.id, raw_token=raw_token)
        )
        return InviteResolveResponse(expires_at=result.expires_at)

    @router.post(
        "/invites/accept",
        operation_id="acceptInvite",
        status_code=status.HTTP_204_NO_CONTENT,
        response_model=None,
        responses=_ERROR_RESPONSES,
    )
    async def accept_invite(
        body: AcceptInviteRequest,
        response: Response,
        actor: actor_dep,
        auth: auth_dep,
    ) -> Response:
        no_store(response)
        raw_token = invite_raw_token_from_start_param(auth.start_param)
        resolved = await bindings.resolve_invite.execute(
            ResolveInviteCommand(actor_id=actor.id, raw_token=raw_token)
        )
        await bindings.accept_invite.execute(
            AcceptInviteCommand(
                actor_id=actor.id,
                invite_id=resolved.invite_id,
                label_for_inviter=ContactLabel(body.label),
                relationship=RelationshipKind(body.relationship),
            )
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)


def _register_downloads(
    router: APIRouter,
    bindings: MiniappRouterBindings,
) -> None:
    """Register unauthenticated one-time export download."""

    @router.get(
        "/downloads/{token}",
        operation_id="serveExportDownload",
        responses={
            **_ERROR_RESPONSES,
            200: {
                "description": "Export JSON payload (single use).",
                "content": {"application/json": {}},
            },
        },
    )
    async def serve_export_download(token: str) -> JSONResponse:
        result = await bindings.serve_export_download.execute(
            ServeExportDownloadCommand(raw_token=token)
        )
        return JSONResponse(
            content=result.payload,
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": 'attachment; filename="svoi-pravila-export.json"',
            },
        )


def _register_rules(
    router: APIRouter,
    bindings: MiniappRouterBindings,
    actor_dep: Any,
) -> None:
    """Register rules routes."""

    @router.get(
        "/contacts/{contact_id}/rules",
        operation_id="listRules",
        response_model=RuleListResponse,
        responses=_ERROR_RESPONSES,
    )
    async def list_rules(
        contact_id: str,
        response: Response,
        actor: actor_dep,
    ) -> RuleListResponse:
        no_store(response)
        cid = ContactId(parse_path_uuid(contact_id))
        result = await bindings.list_rules.execute(
            ListRulesCommand(actor_id=actor.id, contact_id=cid)
        )
        by_id = {rule.id: rule for rule in result.rules}
        views = project_rules_for_list(result.rules)
        items = [
            _rule_item_from_view(
                view,
                needs_my_approval=_needs_my_approval(by_id[view.rule_id], actor.id),
            )
            for view in views
        ]
        return RuleListResponse(rules=items)

    @router.post(
        "/contacts/{contact_id}/rules",
        operation_id="createRule",
        response_model=RuleItem,
        status_code=status.HTTP_201_CREATED,
        responses=_ERROR_RESPONSES,
    )
    async def create_rule(
        contact_id: str,
        body: CreateRuleRequest,
        response: Response,
        actor: actor_dep,
    ) -> RuleItem:
        no_store(response)
        cid = ContactId(parse_path_uuid(contact_id))
        if body.shared:
            listed = await bindings.list_contacts.execute(ListContactsCommand(actor_id=actor.id))
            contact = next((item for item in listed.contacts if item.id == cid), None)
            if contact is None or contact.pair_id is None:
                raise MiniappHttpError(MiniappErrorCode.CONTACT_NOT_PAIRED, 409)
        result = await bindings.propose_rule.execute(
            ProposeRuleCommand(
                actor_id=actor.id,
                contact_id=cid,
                category=RuleCategory(body.category),
                text=RuleText(body.text),
                shared=body.shared,
            )
        )
        return _rule_item(result.rule, actor.id)

    @router.post(
        "/rules/{rule_id}/archive",
        operation_id="archiveRule",
        response_model=RuleItem,
        responses=_ERROR_RESPONSES,
    )
    async def archive_rule(
        rule_id: str,
        response: Response,
        actor: actor_dep,
    ) -> RuleItem:
        no_store(response)
        rid = RuleId(parse_path_uuid(rule_id))
        result = await bindings.archive_rule.execute(
            ArchiveRuleCommand(actor_id=actor.id, rule_id=rid)
        )
        return _rule_item(result.rule, actor.id)

    @router.post(
        "/rules/{rule_id}/approve",
        operation_id="approveRule",
        response_model=RuleItem,
        responses=_ERROR_RESPONSES,
    )
    async def approve_rule(
        rule_id: str,
        response: Response,
        actor: actor_dep,
    ) -> RuleItem:
        no_store(response)
        rid = RuleId(parse_path_uuid(rule_id))
        result = await bindings.approve_rule.execute(
            ApproveRuleCommand(actor_id=actor.id, rule_id=rid)
        )
        return _rule_item(result.rule, actor.id)

    @router.post(
        "/rules/{rule_id}/reject",
        operation_id="rejectPendingRule",
        response_model=RuleItem,
        responses=_ERROR_RESPONSES,
    )
    async def reject_pending_rule(
        rule_id: str,
        response: Response,
        actor: actor_dep,
    ) -> RuleItem:
        no_store(response)
        rid = RuleId(parse_path_uuid(rule_id))
        result = await bindings.reject_pending_rule.execute(
            RejectPendingRuleCommand(actor_id=actor.id, rule_id=rid)
        )
        return _rule_item(result.rule, actor.id)


def _register_suggestions(
    router: APIRouter,
    bindings: MiniappRouterBindings,
    actor_dep: Any,
) -> None:
    """Register suggestions routes."""

    @router.get(
        "/contacts/{contact_id}/suggestions",
        operation_id="listSuggestions",
        response_model=SuggestionListResponse,
        responses=_ERROR_RESPONSES,
    )
    async def list_suggestions(
        contact_id: str,
        response: Response,
        actor: actor_dep,
    ) -> SuggestionListResponse:
        no_store(response)
        cid = ContactId(parse_path_uuid(contact_id))
        result = await bindings.list_suggestions.execute(
            ListSuggestionsCommand(actor_id=actor.id, contact_id=cid)
        )
        items = [
            SuggestionItem.model_validate(
                {
                    "id": str(s.id),
                    "category": s.category.value,
                    "text": s.text.value,
                    "source": s.source.value,
                    "firmness": s.firmness.value if s.firmness is not None else None,
                    "created_at": s.created_at,
                }
            )
            for s in result.suggestions
        ]
        return SuggestionListResponse(suggestions=items)

    @router.post(
        "/suggestions/{suggestion_id}/accept",
        operation_id="acceptSuggestion",
        response_model=AcceptSuggestionResponse,
        responses=_ERROR_RESPONSES,
    )
    async def accept_suggestion(
        suggestion_id: str,
        response: Response,
        actor: actor_dep,
    ) -> AcceptSuggestionResponse:
        no_store(response)
        sid = RuleSuggestionId(parse_path_uuid(suggestion_id))
        result = await bindings.accept_suggestion.execute(
            AcceptSuggestionCommand(actor_id=actor.id, suggestion_id=sid)
        )
        suggestion_id_out = str(result.suggestion.id) if result.suggestion is not None else str(sid)
        return AcceptSuggestionResponse.model_validate(
            {
                "outcome": result.outcome.value,
                "suggestion_id": suggestion_id_out,
                "rule_id": str(result.rule.id) if result.rule is not None else None,
            }
        )

    @router.post(
        "/suggestions/{suggestion_id}/dismiss",
        operation_id="dismissSuggestion",
        response_model=DismissSuggestionResponse,
        responses=_ERROR_RESPONSES,
    )
    async def dismiss_suggestion(
        suggestion_id: str,
        response: Response,
        actor: actor_dep,
    ) -> DismissSuggestionResponse:
        no_store(response)
        sid = RuleSuggestionId(parse_path_uuid(suggestion_id))
        result = await bindings.dismiss_suggestion.execute(
            DismissSuggestionCommand(actor_id=actor.id, suggestion_id=sid)
        )
        suggestion_id_out = str(result.suggestion.id) if result.suggestion is not None else str(sid)
        return DismissSuggestionResponse.model_validate(
            {
                "outcome": result.outcome.value,
                "suggestion_id": suggestion_id_out,
            }
        )
