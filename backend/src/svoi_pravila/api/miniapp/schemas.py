"""Pydantic request/response models for `/api/v1` (no domain types in schemas)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class MeResponse(BaseModel):
    """Current user onboarding and limits (C0/C1)."""

    onboarding_step: Literal["age", "consent", "done"]
    consent_kind: Literal["personal_data", "special_category"] | None = None
    consent_version: str | None = None
    active_contact_id: str | None = None
    max_contacts: int = Field(ge=1)
    max_open_rules: int = Field(ge=1)


class ContactItem(BaseModel):
    """Contact list/detail item."""

    id: str
    label: str
    relationship: Literal["partner", "family", "friend", "work", "other"]
    pair_id: str | None = None
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
