"""Application-layer errors."""

from __future__ import annotations

from svoi_pravila.domain.access import AccessStatus


class ApplicationError(Exception):
    """Base class for application errors."""


class NotFound(ApplicationError):
    """Entity not found or not visible to the actor (no existence leak)."""


class AccessNotGranted(ApplicationError):
    """Protected operation requires completed access steps."""

    def __init__(self, status: AccessStatus) -> None:
        self.status = status
        super().__init__("access not granted")


class AlreadyPaired(ApplicationError):
    """A pair between the two users already exists."""


class ContactLimitReached(ApplicationError):
    """Owner already has the maximum number of contacts."""


class OpenRuleLimitReached(ApplicationError):
    """Scope already has the maximum number of open rules."""


class ContactAlreadyLinked(ApplicationError):
    """Contact is already linked to a pair."""


class ConflictError(ApplicationError):
    """Unique constraint violated (duplicate key)."""
