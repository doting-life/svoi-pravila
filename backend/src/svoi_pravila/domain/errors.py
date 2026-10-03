"""Domain error hierarchy."""

from __future__ import annotations


class DomainError(Exception):
    """Base class for all domain errors."""


class InvalidTimestampError(DomainError):
    """Raised when a datetime is naive or not UTC."""


class InvalidValueError(DomainError):
    """Raised when a value object or field fails validation."""


class InvalidTransitionError(DomainError):
    """Raised when an entity state transition is not allowed."""


class NotAMemberError(DomainError):
    """Raised when a user is not a member of a pair."""


class AlreadyLinkedError(DomainError):
    """Raised when a contact is already linked to a pair."""


class InviteExpiredError(DomainError):
    """Raised when an invite has expired."""


class InviteAlreadyAcceptedError(DomainError):
    """Raised when an invite has already been accepted."""


class SelfInviteAcceptError(DomainError):
    """Raised when the inviter attempts to accept their own invite."""
