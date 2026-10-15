"""Atomic CSV/Markdown package writer."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from svoi_pravila.adapters.system.csv_metrics_package import CsvMetricsPackageWriter, render_csv
from svoi_pravila.application.errors import ProgramExportErrorKind, ProgramExportFailed
from svoi_pravila.application.ports.metrics_package import TableFile, TextFile

_TABLE = TableFile(
    name="t.csv",
    header=("day", "at", "money", "ratio", "count", "text", "empty"),
    rows=(
        (
            date(2026, 3, 15),
            datetime(2026, 3, 15, 9, 1, tzinfo=UTC),
            Decimal("0.0650"),
            1.5,
            7,
            "a,b",
            None,
        ),
    ),
)
_TEXT = TextFile(name="d.md", content="# Title\n\u0422\u0435\u043a\u0441\u0442\n")


def _names(directory: Path) -> list[str]:
    return sorted(item.name for item in directory.iterdir())


@pytest.mark.unit
def test_render_csv_formats_cells() -> None:
    assert render_csv(_TABLE) == (
        'day,at,money,ratio,count,text,empty\n2026-03-15,2026-03-15T09:01:00+00:00,0.0650,1.5,7,"a,b",\n'
    )


@pytest.mark.unit
def test_render_csv_converts_timestamps_to_utc_and_never_uses_exponents() -> None:
    table = TableFile(
        name="t.csv",
        header=("at", "small"),
        rows=(
            (datetime(2026, 3, 15, 12, 0, tzinfo=timezone(timedelta(hours=3))), Decimal("1E-7")),
        ),
    )
    assert render_csv(table) == "at,small\n2026-03-15T09:00:00+00:00,0.0000001\n"


@pytest.mark.unit
async def test_write_creates_directory_and_utf8_files_without_bom(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "export"
    await CsvMetricsPackageWriter().write(out, (_TABLE, _TEXT), force=False)
    assert (out / "t.csv").read_bytes() == render_csv(_TABLE).encode("utf-8")
    raw = (out / "d.md").read_bytes()
    assert raw == _TEXT.content.encode("utf-8")
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert _names(out) == ["d.md", "t.csv"]


@pytest.mark.unit
async def test_existing_file_is_refused_without_force_and_nothing_is_written(
    tmp_path: Path,
) -> None:
    (tmp_path / "d.md").write_text("old", encoding="utf-8")
    with pytest.raises(ProgramExportFailed) as caught:
        await CsvMetricsPackageWriter().write(tmp_path, (_TABLE, _TEXT), force=False)
    assert caught.value.kind is ProgramExportErrorKind.TARGET_EXISTS
    assert (tmp_path / "d.md").read_text(encoding="utf-8") == "old"
    assert not (tmp_path / "t.csv").exists()


@pytest.mark.unit
async def test_force_overwrites_existing_files(tmp_path: Path) -> None:
    (tmp_path / "t.csv").write_text("old", encoding="utf-8")
    await CsvMetricsPackageWriter().write(tmp_path, (_TABLE,), force=True)
    assert (tmp_path / "t.csv").read_text(encoding="utf-8") == render_csv(_TABLE)


@pytest.mark.unit
async def test_unwritable_target_is_a_typed_error(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(ProgramExportFailed) as caught:
        await CsvMetricsPackageWriter().write(blocker / "sub", (_TABLE,), force=False)
    assert caught.value.kind is ProgramExportErrorKind.WRITE_FAILED


@pytest.mark.unit
async def test_failed_replace_leaves_no_temporary_file(tmp_path: Path) -> None:
    (tmp_path / "t.csv").mkdir()
    with pytest.raises(ProgramExportFailed) as caught:
        await CsvMetricsPackageWriter().write(tmp_path, (_TABLE,), force=True)
    assert caught.value.kind is ProgramExportErrorKind.WRITE_FAILED
    assert _names(tmp_path) == ["t.csv"]
