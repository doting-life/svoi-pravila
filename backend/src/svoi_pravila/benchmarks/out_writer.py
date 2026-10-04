"""Incremental C0 markdown report writer for the LLM benchmark."""

from __future__ import annotations

from pathlib import Path


class OutWriter:
    """Append flushed markdown rows to ``--out`` as cells complete."""

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._started_sections: set[str] = set()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

    def write_header_once(self, header: str) -> None:
        """Write a table header the first time its text is seen."""
        if self._path is None or header in self._started_sections:
            return
        self._started_sections.add(header)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(header + "\n")
            handle.flush()

    def write_row(self, row: str) -> None:
        """Append one completed cell row."""
        if self._path is None:
            return
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(row + "\n")
            handle.flush()

    def write_reasons(self, line: str) -> None:
        """Append one C0 invalid-reason breakdown line for a cell."""
        if self._path is None:
            return
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()

    def write_incomplete(
        self,
        *,
        http_status: int | None = None,
        rate_limit_headers: tuple[tuple[str, str], ...] = (),
        token_budget: tuple[int, int] | None = None,
    ) -> None:
        """Append the INCOMPLETE marker and C0 diagnostics."""
        if self._path is None:
            return
        if token_budget is not None:
            spent, limit = token_budget
            lines = [f"**INCOMPLETE** — token budget reached (spent {spent} of {limit})"]
        else:
            lines = ["**INCOMPLETE** — stopped after first `rate_limited`"]
            if http_status is not None:
                lines.append(f"http_status: {http_status}")
            for key, value in rate_limit_headers:
                lines.append(f"header {key}: {value}")
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
            handle.flush()
