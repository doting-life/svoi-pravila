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


class UsageScenario(StrEnum):
    """Scenario that produced a usage event."""

    DECODE = "decode"
    SOFTEN = "soften"
    HELP_SAY = "help_say"


class UsageSurface(StrEnum):
    """Channel surface that produced a usage event."""

    DM = "dm"
    INLINE = "inline"
    MINIAPP = "miniapp"


class UsageOutcome(StrEnum):
    """C0 outcome of a generation attempt that reached the provider."""

    OK = "ok"
    INVALID_OUTPUT = "invalid_output"
    REFUSED = "refused"
    UNAVAILABLE = "unavailable"
