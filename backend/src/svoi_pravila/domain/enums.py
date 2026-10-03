"""Domain enumerations."""

from __future__ import annotations

from enum import StrEnum


class RelationshipKind(StrEnum):
    """How the owner relates to a contact."""

    PARTNER = "partner"
    FAMILY = "family"
    FRIEND = "friend"
    WORK = "work"
    OTHER = "other"


class RuleCategory(StrEnum):
    """Category of a conversation rule."""

    TABOO_TOPIC = "taboo_topic"
    HOW_TO_ASK = "how_to_ask"
    APOLOGY = "apology"
    CONFLICT_PROTOCOL = "conflict_protocol"
    OTHER = "other"


class RuleStatus(StrEnum):
    """Lifecycle status of a rule aggregate."""

    PROPOSED = "proposed"
    ACTIVE = "active"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class ConsentKind(StrEnum):
    """Kind of legal consent."""

    PERSONAL_DATA = "personal_data"
    SPECIAL_CATEGORY = "special_category"
