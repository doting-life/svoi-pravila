"""Pydantic request/response models for `/api/v1` (no domain types in schemas)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MeResponse(BaseModel):
    """Current user onboarding and limits (C0/C1)."""

    onboarding_step: Literal["age", "consent", "done"]
    consent_kind: Literal["personal_data", "special_category"] | None = None
    consent_version: str | None = None
    active_contact_id: str | None = None
    account_exists: bool
    max_contacts: int = Field(ge=1)
    max_open_rules: int = Field(ge=1)
    display_timezone: str = Field(min_length=1)


class ConfirmTrueRequest(BaseModel):
    """Destructive confirmations: body must be exactly ``{\"confirm\": true}``."""

    model_config = ConfigDict(extra="forbid")

    confirm: Literal[True]


class ExportDeliveryResponse(BaseModel):
    """POST /me/export — delivery acknowledgement (never the export payload)."""

    delivered_to: Literal["bot_chat"]


class PrivacyExportTextsResponse(BaseModel):
    """Export disclosure copy from the shared privacy catalog (C0)."""

    description: str = Field(min_length=1)
    sections: dict[str, str]


class PrivacyActionTextsResponse(BaseModel):
    """Revoke or delete blurb and confirm copy (C0)."""

    description: str = Field(min_length=1)
    confirm: str = Field(min_length=1)


class PrivacyTextsResponse(BaseModel):
    """GET /privacy/texts — shared privacy copy for the mini-app (C0)."""

    export: PrivacyExportTextsResponse
    revoke: PrivacyActionTextsResponse
    delete: PrivacyActionTextsResponse
    leave_pair: PrivacyActionTextsResponse


class ContactItem(BaseModel):
    """Contact list/detail item."""

    id: str
    label: str
    relationship: Literal["partner", "family", "friend", "work", "other"]
    pair_id: str | None = None
    paired: bool
    created_at: datetime


class ContactListResponse(BaseModel):
    """GET /contacts."""

    contacts: list[ContactItem]


class CreateContactRequest(BaseModel):
    """POST /contacts."""

    label: str = Field(min_length=1, max_length=32)
    relationship: Literal["partner", "family", "friend", "work", "other"]


class RenameContactRequest(BaseModel):
    """PATCH /contacts/{id}."""

    label: str = Field(min_length=1, max_length=32)


class RuleItem(BaseModel):
    """Rule list item with effective dates."""

    id: str
    category: Literal["taboo_topic", "how_to_ask", "apology", "conflict_protocol", "other"]
    status: Literal["proposed", "active", "rejected", "archived"]
    text: str
    shared: bool
    needs_my_approval: bool = False
    created_at: datetime
    effective_since: datetime | None = None
    has_pending_edit: bool = False


class RuleListResponse(BaseModel):
    """GET /contacts/{id}/rules."""

    rules: list[RuleItem]


class CreateRuleRequest(BaseModel):
    """POST /contacts/{id}/rules."""

    category: Literal["taboo_topic", "how_to_ask", "apology", "conflict_protocol", "other"]
    text: str = Field(min_length=1, max_length=280)
    shared: bool = False


class InviteResponse(BaseModel):
    """POST /contacts/{id}/invite — deep-link returned once."""

    link: str = Field(min_length=1)
    expires_at: datetime


class SuggestionItem(BaseModel):
    """Pending suggestion item."""

    id: str
    category: Literal["taboo_topic", "how_to_ask", "apology", "conflict_protocol", "other"]
    text: str
    source: str
    firmness: Literal["gentle", "balanced", "firm"] | None = None
    created_at: datetime


class SuggestionListResponse(BaseModel):
    """GET /contacts/{id}/suggestions."""

    suggestions: list[SuggestionItem]


class AcceptSuggestionResponse(BaseModel):
    """POST /suggestions/{id}/accept."""

    outcome: Literal["accepted", "already_decided", "open_rule_limit"]
    suggestion_id: str
    rule_id: str | None = None


class DismissSuggestionResponse(BaseModel):
    """POST /suggestions/{id}/dismiss."""

    outcome: Literal["dismissed", "already_decided"]
    suggestion_id: str


class DecodeRequest(BaseModel):
    """POST /decode — incoming text to decode (same limits as DM)."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4000)


class SuggestFromDecodeRequest(BaseModel):
    """POST /suggestions/from-decode — one-shot rule-source token."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=128)


class SuggestFromDecodeResponse(BaseModel):
    """POST /suggestions/from-decode outcomes (same as the bot)."""

    outcome: Literal[
        "ok",
        "none",
        "unavailable",
        "crisis",
        "pending_exists",
    ]
    suggestion: SuggestionItem | None = None
    lead: str | None = None
    resources: list[str] | None = None
