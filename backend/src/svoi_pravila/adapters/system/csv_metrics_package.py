"""Stdlib ``csv`` writer for the program-metrics package (atomic per file)."""

from __future__ import annotations

import asyncio
import csv
import io
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from svoi_pravila.application.errors import ProgramExportErrorKind, ProgramExportFailed
from svoi_pravila.application.ports.metrics_package import Cell, TableFile, TextFile

_TEMP_SUFFIX = ".tmp"


class CsvMetricsPackageWriter:
    """UTF-8 without BOM, ``,`` separator, ``.`` decimals, ISO dates, UTC timestamps."""

    async def write(
        self,
        out_dir: Path,
        files: Sequence[TableFile | TextFile],
        *,
        force: bool,
    ) -> None:
        """Write every file into ``out_dir``; refuse existing files unless ``force``."""
        await asyncio.to_thread(_write_all, out_dir, files, force=force)


def render_csv(table: TableFile) -> str:
    """Render ``table`` with ``\\n`` line ends and minimal quoting."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(table.header)
    for row in table.rows:
        writer.writerow([_format(cell) for cell in row])
    return buffer.getvalue()


def _format(cell: Cell) -> str:
    if cell is None:
        return ""
    if isinstance(cell, datetime):
        return cell.astimezone(UTC).isoformat(timespec="seconds")
    if isinstance(cell, date):
        return cell.isoformat()
    if isinstance(cell, Decimal):
        return format(cell, "f")
    return str(cell)


def _write_all(out_dir: Path, files: Sequence[TableFile | TextFile], *, force: bool) -> None:
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        if not force and any((out_dir / item.name).exists() for item in files):
            raise ProgramExportFailed(ProgramExportErrorKind.TARGET_EXISTS)
        for item in files:
            text = item.content if isinstance(item, TextFile) else render_csv(item)
            _write_atomic(out_dir / item.name, text.encode("utf-8"))
    except OSError as exc:
        raise ProgramExportFailed(ProgramExportErrorKind.WRITE_FAILED) from exc


def _write_atomic(target: Path, data: bytes) -> None:
    temp = target.with_name(target.name + _TEMP_SUFFIX)
    try:
        temp.write_bytes(data)
        temp.replace(target)
    except OSError:
        temp.unlink(missing_ok=True)
        raise
