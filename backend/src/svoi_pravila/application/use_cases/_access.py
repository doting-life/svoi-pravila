"""Shared access-status helpers for use cases."""

from __future__ import annotations

from svoi_pravila.application.errors import AccessNotGranted, NotFound
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.unit_of_work import UnitOfWork
from svoi_pravila.domain.access import AccessStatus, evaluate_access
from svoi_pravila.domain.ids import UserId


async def load_access_status(
    uow: UnitOfWork,
    catalog: ConsentCatalog,
    user_id: UserId,
) -> AccessStatus:
    """Load user and consents and evaluate access; raise NotFound if user missing."""
    user = await uow.users.get(user_id)
    if user is None:
        raise NotFound()
    consents = await uow.consents.list_for_user(user_id)
    return evaluate_access(user, consents, catalog.current_requirement())


async def require_access(
    uow: UnitOfWork,
    catalog: ConsentCatalog,
    user_id: UserId,
) -> AccessStatus:
    """Raise AccessNotGranted unless access is fully granted."""
    status = await load_access_status(uow, catalog, user_id)
    if not status.granted:
        raise AccessNotGranted(status)
    return status
