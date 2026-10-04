"""In-memory DialogState for tests."""

from __future__ import annotations

from svoi_pravila.application.ports.dialog_state import DialogRecord


class FakeDialogState:
    """Process-memory dialog records keyed by pseudonym."""

    def __init__(self) -> None:
        self._records: dict[str, DialogRecord] = {}

    async def get(self, pseudonym: str) -> DialogRecord | None:
        return self._records.get(pseudonym)

    async def set(self, pseudonym: str, record: DialogRecord) -> None:
        self._records[pseudonym] = record

    async def clear(self, pseudonym: str) -> None:
        self._records.pop(pseudonym, None)
