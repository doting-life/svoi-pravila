"""Invite token generator."""

from __future__ import annotations

import secrets


class SecretsInviteTokenGenerator:
    """Generate URL-safe invite tokens with at least 256 bits of entropy."""

    def new_invite_token(self) -> str:
        """Return ``secrets.token_urlsafe(32)`` (≤ 60 characters)."""
        return secrets.token_urlsafe(32)
