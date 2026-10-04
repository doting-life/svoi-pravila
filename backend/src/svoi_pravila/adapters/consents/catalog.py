"""Consent catalog backed by versioned package markdown resources."""

from __future__ import annotations

import hashlib
import importlib.resources
import re
from functools import cached_property

from svoi_pravila.domain.access import AccessRequirement, ConsentText
from svoi_pravila.domain.consent_document import ConsentDocument
from svoi_pravila.domain.enums import ConsentKind
from svoi_pravila.domain.text import Sha256Hex

_VERSION_RE = re.compile(r"^v(\d+)\.md$")


class PackageConsentCatalog:
    """Load consent texts from ``adapters.consents.<kind>.vN.md`` package data."""

    def current_requirement(self) -> AccessRequirement:
        """Return version+hash for every consent kind."""
        return AccessRequirement.from_kinds(
            {
                kind: ConsentText(document.version, document.sha256)
                for kind, document in self._documents.items()
            }
        )

    def current_document(self, kind: ConsentKind) -> ConsentDocument:
        """Return the current document for ``kind``."""
        return self._documents[kind]

    @cached_property
    def _documents(self) -> dict[ConsentKind, ConsentDocument]:
        return {kind: self._load_current(kind) for kind in ConsentKind}

    def _load_current(self, kind: ConsentKind) -> ConsentDocument:
        package = importlib.resources.files("svoi_pravila.adapters.consents").joinpath(kind.value)
        if not package.is_dir():
            msg = f"missing consent package directory for {kind.value}"
            raise FileNotFoundError(msg)
        versions: list[tuple[int, bytes]] = []
        for entry in package.iterdir():
            if not entry.is_file():
                continue
            match = _VERSION_RE.fullmatch(entry.name)
            if match is None:
                continue
            versions.append((int(match.group(1)), entry.read_bytes()))
        if not versions:
            msg = f"no consent versions found for {kind.value}"
            raise FileNotFoundError(msg)
        version_number, raw = max(versions, key=lambda item: item[0])
        text = raw.decode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        return ConsentDocument(
            kind=kind,
            version=str(version_number),
            sha256=Sha256Hex(digest),
            text=text,
        )
