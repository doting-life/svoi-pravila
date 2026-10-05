"""FastAPI router for `/api/v1` mini-app endpoints."""

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status

from svoi_pravila.api.miniapp.deps import (
    MiniappAuthContext,
    MiniappAuthenticator,
    MiniappDeps,
    no_store,
    parse_path_uuid,
)
from svoi_pravila.api.miniapp.errors import ErrorBody, MiniappErrorCode
from svoi_pravila.api.miniapp.http import MiniappHttpError
from svoi_pravila.api.miniapp.schemas import (
    AcceptSuggestionResponse,
    ConfirmTrueRequest,
    ContactItem,
    ContactListResponse,
    CreateContactRequest,
    CreateRuleRequest,
    DismissSuggestionResponse,
    ExportDeliveryResponse,
    MeResponse,
    RenameContactRequest,
    RuleItem,
    RuleListResponse,
    SuggestionItem,
    SuggestionListResponse,
)
from svoi_pravila.application.ports.rate_limiter import RateLimiter
from svoi_pravila.application.rule_view import RuleListItemView, project_rules_for_list
from svoi_pravila.application.use_cases.accept_suggestion import (
    AcceptSuggestion,
    AcceptSuggestionCommand,
)
from svoi_pravila.application.use_cases.archive_rule import ArchiveRule, ArchiveRuleCommand
from svoi_pravila.application.use_cases.create_contact import (
    CreateContact,
    CreateContactCommand,
)
from svoi_pravila.application.use_cases.delete_my_account import (
    DeleteMyAccount,
    DeleteMyAccountCommand,
)
from svoi_pravila.application.use_cases.dismiss_suggestion import (
    DismissSuggestion,
    DismissSuggestionCommand,
)
from svoi_pravila.application.use_cases.get_onboarding_step import GetOnboardingStepQuery
from svoi_pravila.application.use_cases.list_contacts import ListContacts, ListContactsCommand
from svoi_pravila.application.use_cases.list_rules import ListRules, ListRulesCommand
from svoi_pravila.application.use_cases.list_suggestions import (
    ListSuggestions,
    ListSuggestionsCommand,
)
from svoi_pravila.application.use_cases.propose_rule import ProposeRule, ProposeRuleCommand
from svoi_pravila.application.use_cases.rename_contact import (
    RenameContact,
    RenameContactCommand,
)
from svoi_pravila.application.use_cases.request_my_data_export import (
    RequestMyDataExport,
    RequestMyDataExportCommand,
)
from svoi_pravila.application.use_cases.revoke_all_consents import (
    RevokeAllConsents,
    RevokeAllConsentsCommand,
)
from svoi_pravila.application.use_cases.set_active_contact import (
    SetActiveContact,
    SetActiveContactCommand,
)
from svoi_pravila.domain.contact import MAX_CONTACTS_PER_USER, Contact
from svoi_pravila.domain.enums import RelationshipKind, RuleCategory
from svoi_pravila.domain.ids import ContactId, RuleId, RuleSuggestionId
from svoi_pravila.domain.rules import MAX_OPEN_RULES_PER_SCOPE, PairScope, Rule
from svoi_pravila.domain.text import ContactLabel, RuleText
from svoi_pravila.domain.user import User

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorBody},
    403: {"model": ErrorBody},
    404: {"model": ErrorBody},
    409: {"model": ErrorBody},
    413: {"model": ErrorBody},
    422: {"model": ErrorBody},
    429: {"model": ErrorBody},
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
    list_rules: ListRules
    propose_rule: ProposeRule
    archive_rule: ArchiveRule
    list_suggestions: ListSuggestions
    accept_suggestion: AcceptSuggestion
    dismiss_suggestion: DismissSuggestion
    request_my_data_export: RequestMyDataExport
    revoke_all_consents: RevokeAllConsents
    delete_my_account: DeleteMyAccount
    export_rate_limiter: RateLimiter
    display_timezone: str


def _contact_item(contact: Contact) -> ContactItem:
    return ContactItem.model_validate(
        {
            "id": str(contact.id),
            "label": contact.label.value,
            "relationship": contact.relationship.value,
            "pair_id": str(contact.pair_id) if contact.pair_id is not None else None,
            "created_at": contact.created_at,
        }
    )


def _rule_item_from_view(view: RuleListItemView) -> RuleItem:
    return RuleItem.model_validate(
        {
            "id": str(view.rule_id),
            "category": view.category.value,
            "status": view.status.value,
            "text": view.text.value,
            "shared": view.shared,
            "created_at": view.created_at,
            "effective_since": view.effective_since,
            "has_pending_edit": view.has_pending_edit,
        }
    )


def _rule_item(rule: Rule) -> RuleItem:
    """Map a rule for mutation responses (list projection, or closed-status fallback)."""
    views = project_rules_for_list((rule,))
    if views:
        return _rule_item_from_view(views[0])
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
    _register_contacts(router, bindings, actor_dep)
    _register_rules(router, bindings, actor_dep)
    _register_suggestions(router, bindings, actor_dep)
    return router


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
        step_result = await bindings.auth.get_onboarding_step.execute(
            GetOnboardingStepQuery(telegram_user_id=auth.telegram_user_id)
        )
        step = step_result.step
        active = (
            str(auth.user.active_contact_id)
            if auth.user is not None and auth.user.active_contact_id is not None
            else None
        )
        return MeResponse.model_validate(
            {
                "onboarding_step": step.kind.value,
                "consent_kind": (
                    step.consent_kind.value if step.consent_kind is not None else None
                ),
                "consent_version": step.consent_version,
                "active_contact_id": active,
                "max_contacts": MAX_CONTACTS_PER_USER,
                "max_open_rules": MAX_OPEN_RULES_PER_SCOPE,
                "display_timezone": bindings.display_timezone,
            }
        )

    @router.post(
        "/me/export",
        operation_id="exportMyData",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=ExportDeliveryResponse,
        responses=_ERROR_RESPONSES,
    )
    async def export_my_data(
        response: Response,
        actor: actor_dep,
    ) -> ExportDeliveryResponse:
        no_store(response)
        pseudonym = bindings.auth.pseudonymizer.pseudonymize(
            _EXPORT_RATE_PURPOSE,
            str(actor.telegram_user_id.value),
        )
        decision = await bindings.export_rate_limiter.check(pseudonym)
        if not decision.allowed:
            raise MiniappHttpError(MiniappErrorCode.RATE_LIMITED, 429)
        result = await bindings.request_my_data_export.execute(
            RequestMyDataExportCommand(telegram_user_id=actor.telegram_user_id)
        )
        return ExportDeliveryResponse.model_validate({"delivered_to": result.delivered_to})

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
        views = project_rules_for_list(result.rules)
        return RuleListResponse(rules=[_rule_item_from_view(view) for view in views])

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
        result = await bindings.propose_rule.execute(
            ProposeRuleCommand(
                actor_id=actor.id,
                contact_id=cid,
                category=RuleCategory(body.category),
                text=RuleText(body.text),
                shared=False,
            )
        )
        return _rule_item(result.rule)

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
        return _rule_item(result.rule)


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
