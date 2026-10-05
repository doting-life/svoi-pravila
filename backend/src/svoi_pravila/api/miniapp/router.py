"""FastAPI router for `/api/v1` mini-app endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status

from svoi_pravila.api.miniapp.deps import (
    MiniappAuthContext,
    MiniappDeps,
    authenticate_miniapp,
    enforce_body_limit,
    map_access_error,
    no_store,
    parse_path_uuid,
    require_actor,
)
from svoi_pravila.api.miniapp.errors import ErrorBody, MiniappErrorCode
from svoi_pravila.api.miniapp.http import MiniappHttpError
from svoi_pravila.api.miniapp.schemas import (
    AcceptSuggestionResponse,
    ContactItem,
    ContactListResponse,
    CreateContactRequest,
    CreateRuleRequest,
    DismissSuggestionResponse,
    MeResponse,
    RenameContactRequest,
    RuleItem,
    RuleListResponse,
    SuggestionItem,
    SuggestionListResponse,
)
from svoi_pravila.application.errors import (
    AccessNotGranted,
    ContactLimitReached,
    NotFound,
    OpenRuleLimitReached,
)
from svoi_pravila.application.use_cases.accept_suggestion import (
    AcceptSuggestion,
    AcceptSuggestionCommand,
)
from svoi_pravila.application.use_cases.archive_rule import ArchiveRule, ArchiveRuleCommand
from svoi_pravila.application.use_cases.create_contact import (
    CreateContact,
    CreateContactCommand,
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
from svoi_pravila.application.use_cases.set_active_contact import (
    SetActiveContact,
    SetActiveContactCommand,
)
from svoi_pravila.domain.contact import MAX_CONTACTS_PER_USER, Contact
from svoi_pravila.domain.enums import RelationshipKind, RuleCategory
from svoi_pravila.domain.errors import InvalidTransitionError, InvalidValueError
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


def _rule_item(rule: Rule) -> RuleItem:
    latest = rule.revisions[-1]
    shared = isinstance(rule.scope, PairScope)
    return RuleItem.model_validate(
        {
            "id": str(rule.id),
            "category": rule.category.value,
            "status": rule.status.value,
            "text": latest.text.value,
            "shared": shared,
            "created_at": rule.created_at,
            "effective_since": latest.effective_since,
        }
    )


def build_miniapp_router(bindings: MiniappRouterBindings) -> APIRouter:
    """Create `/api/v1` routes bound to composition-root use cases."""
    router = APIRouter(prefix="/api/v1", tags=["miniapp"])
    _register_routes(router, bindings)
    return router


def _register_routes(router: APIRouter, bindings: MiniappRouterBindings) -> None:
    """Attach all `/api/v1` handlers to ``router``."""
    _register_me(router, bindings)
    _register_contacts(router, bindings)
    _register_rules(router, bindings)
    _register_suggestions(router, bindings)


def _register_me(router: APIRouter, bindings: MiniappRouterBindings) -> None:
    """Register me routes."""

    @router.get(
        "/me",
        operation_id="getMe",
        response_model=MeResponse,
        responses=_ERROR_RESPONSES,
    )
    async def get_me(
        response: Response,
        auth: Annotated[MiniappAuthContext, Depends(authenticate_miniapp)],
        _: Annotated[None, Depends(enforce_body_limit)],
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
            }
        )


def _register_contacts(router: APIRouter, bindings: MiniappRouterBindings) -> None:
    """Register contacts routes."""

    @router.get(
        "/contacts",
        operation_id="listContacts",
        response_model=ContactListResponse,
        responses=_ERROR_RESPONSES,
    )
    async def list_contacts(
        response: Response,
        actor: Annotated[User, Depends(require_actor)],
        _: Annotated[None, Depends(enforce_body_limit)],
    ) -> ContactListResponse:
        no_store(response)
        try:
            result = await bindings.list_contacts.execute(ListContactsCommand(actor_id=actor.id))
        except AccessNotGranted as exc:
            raise map_access_error(exc) from exc
        except NotFound as exc:
            raise MiniappHttpError(MiniappErrorCode.NOT_FOUND, 404) from exc
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
        actor: Annotated[User, Depends(require_actor)],
        _: Annotated[None, Depends(enforce_body_limit)],
    ) -> ContactItem:
        no_store(response)
        try:
            label = ContactLabel(body.label)
            relationship = RelationshipKind(body.relationship)
            result = await bindings.create_contact.execute(
                CreateContactCommand(
                    actor_id=actor.id,
                    label=label,
                    relationship=relationship,
                )
            )
        except InvalidValueError as exc:
            raise MiniappHttpError(MiniappErrorCode.VALIDATION_ERROR, 422) from exc
        except ContactLimitReached as exc:
            raise MiniappHttpError(MiniappErrorCode.CONTACT_LIMIT, 409) from exc
        except AccessNotGranted as exc:
            raise map_access_error(exc) from exc
        except NotFound as exc:
            raise MiniappHttpError(MiniappErrorCode.NOT_FOUND, 404) from exc
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
        actor: Annotated[User, Depends(require_actor)],
        _: Annotated[None, Depends(enforce_body_limit)],
    ) -> ContactItem:
        no_store(response)
        cid = ContactId(parse_path_uuid(contact_id))
        try:
            result = await bindings.rename_contact.execute(
                RenameContactCommand(
                    actor_id=actor.id,
                    contact_id=cid,
                    label=ContactLabel(body.label),
                )
            )
        except InvalidValueError as exc:
            raise MiniappHttpError(MiniappErrorCode.VALIDATION_ERROR, 422) from exc
        except AccessNotGranted as exc:
            raise map_access_error(exc) from exc
        except NotFound as exc:
            raise MiniappHttpError(MiniappErrorCode.NOT_FOUND, 404) from exc
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
        actor: Annotated[User, Depends(require_actor)],
        _: Annotated[None, Depends(enforce_body_limit)],
    ) -> Response:
        no_store(response)
        cid = ContactId(parse_path_uuid(contact_id))
        try:
            await bindings.set_active_contact.execute(
                SetActiveContactCommand(actor_id=actor.id, contact_id=cid)
            )
        except AccessNotGranted as exc:
            raise map_access_error(exc) from exc
        except NotFound as exc:
            raise MiniappHttpError(MiniappErrorCode.NOT_FOUND, 404) from exc
        return Response(
            status_code=status.HTTP_204_NO_CONTENT,
            headers={"Cache-Control": "no-store"},
        )


def _register_rules(router: APIRouter, bindings: MiniappRouterBindings) -> None:
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
        actor: Annotated[User, Depends(require_actor)],
        _: Annotated[None, Depends(enforce_body_limit)],
    ) -> RuleListResponse:
        no_store(response)
        cid = ContactId(parse_path_uuid(contact_id))
        try:
            result = await bindings.list_rules.execute(
                ListRulesCommand(actor_id=actor.id, contact_id=cid)
            )
        except AccessNotGranted as exc:
            raise map_access_error(exc) from exc
        except NotFound as exc:
            raise MiniappHttpError(MiniappErrorCode.NOT_FOUND, 404) from exc
        return RuleListResponse(rules=[_rule_item(r) for r in result.rules])

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
        actor: Annotated[User, Depends(require_actor)],
        _: Annotated[None, Depends(enforce_body_limit)],
    ) -> RuleItem:
        no_store(response)
        cid = ContactId(parse_path_uuid(contact_id))
        try:
            result = await bindings.propose_rule.execute(
                ProposeRuleCommand(
                    actor_id=actor.id,
                    contact_id=cid,
                    category=RuleCategory(body.category),
                    text=RuleText(body.text),
                    shared=False,
                )
            )
        except InvalidValueError as exc:
            raise MiniappHttpError(MiniappErrorCode.VALIDATION_ERROR, 422) from exc
        except OpenRuleLimitReached as exc:
            raise MiniappHttpError(MiniappErrorCode.OPEN_RULE_LIMIT, 409) from exc
        except AccessNotGranted as exc:
            raise map_access_error(exc) from exc
        except NotFound as exc:
            raise MiniappHttpError(MiniappErrorCode.NOT_FOUND, 404) from exc
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
        actor: Annotated[User, Depends(require_actor)],
        _: Annotated[None, Depends(enforce_body_limit)],
    ) -> RuleItem:
        no_store(response)
        rid = RuleId(parse_path_uuid(rule_id))
        try:
            result = await bindings.archive_rule.execute(
                ArchiveRuleCommand(actor_id=actor.id, rule_id=rid)
            )
        except InvalidTransitionError as exc:
            raise MiniappHttpError(MiniappErrorCode.INVALID_TRANSITION, 409) from exc
        except AccessNotGranted as exc:
            raise map_access_error(exc) from exc
        except NotFound as exc:
            raise MiniappHttpError(MiniappErrorCode.NOT_FOUND, 404) from exc
        return _rule_item(result.rule)


def _register_suggestions(router: APIRouter, bindings: MiniappRouterBindings) -> None:
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
        actor: Annotated[User, Depends(require_actor)],
        _: Annotated[None, Depends(enforce_body_limit)],
    ) -> SuggestionListResponse:
        no_store(response)
        cid = ContactId(parse_path_uuid(contact_id))
        try:
            result = await bindings.list_suggestions.execute(
                ListSuggestionsCommand(actor_id=actor.id, contact_id=cid)
            )
        except AccessNotGranted as exc:
            raise map_access_error(exc) from exc
        except NotFound as exc:
            raise MiniappHttpError(MiniappErrorCode.NOT_FOUND, 404) from exc
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
        actor: Annotated[User, Depends(require_actor)],
        _: Annotated[None, Depends(enforce_body_limit)],
    ) -> AcceptSuggestionResponse:
        no_store(response)
        sid = RuleSuggestionId(parse_path_uuid(suggestion_id))
        try:
            result = await bindings.accept_suggestion.execute(
                AcceptSuggestionCommand(actor_id=actor.id, suggestion_id=sid)
            )
        except AccessNotGranted as exc:
            raise map_access_error(exc) from exc
        except NotFound as exc:
            raise MiniappHttpError(MiniappErrorCode.NOT_FOUND, 404) from exc
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
        actor: Annotated[User, Depends(require_actor)],
        _: Annotated[None, Depends(enforce_body_limit)],
    ) -> DismissSuggestionResponse:
        no_store(response)
        sid = RuleSuggestionId(parse_path_uuid(suggestion_id))
        try:
            result = await bindings.dismiss_suggestion.execute(
                DismissSuggestionCommand(actor_id=actor.id, suggestion_id=sid)
            )
        except AccessNotGranted as exc:
            raise map_access_error(exc) from exc
        except NotFound as exc:
            raise MiniappHttpError(MiniappErrorCode.NOT_FOUND, 404) from exc
        suggestion_id_out = str(result.suggestion.id) if result.suggestion is not None else str(sid)
        return DismissSuggestionResponse.model_validate(
            {
                "outcome": result.outcome.value,
                "suggestion_id": suggestion_id_out,
            }
        )
