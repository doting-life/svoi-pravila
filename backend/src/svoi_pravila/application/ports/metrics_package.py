"""Writer port for the program-metrics file package."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

type Cell = str | int | float | Decimal | date | datetime | None


@dataclass(frozen=True, slots=True)
class TableFile:
    """A CSV file: header and rows of already rounded cells (``None`` is an empty cell)."""

    name: str
    header: tuple[str, ...]
    rows: tuple[tuple[Cell, ...], ...]


@dataclass(frozen=True, slots=True)
class TextFile:
    """A UTF-8 text file written verbatim."""

    name: str
    content: str


class MetricsPackageWriter(Protocol):
    """Atomic per-file writer; raises ``ProgramExportFailed`` with a typed kind."""

    async def write(
        self,
        out_dir: Path,
        files: Sequence[TableFile | TextFile],
        *,
        force: bool,
    ) -> None:
        """Write every file into ``out_dir``; never overwrite unless ``force``."""
        ...
