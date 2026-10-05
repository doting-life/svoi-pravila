"""Export the FastAPI OpenAPI schema for the mini-app (no server, no Settings/.env)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

from svoi_pravila.api.app import AppLifecycleHooks, create_app
from svoi_pravila.api.miniapp import (
    MiniappDeps,
    MiniappRouterBindings,
    build_miniapp_router,
)
from svoi_pravila.application.use_cases.accept_suggestion import AcceptSuggestion
from svoi_pravila.application.use_cases.archive_rule import ArchiveRule
from svoi_pravila.application.use_cases.check_readiness import CheckReadiness
from svoi_pravila.application.use_cases.create_contact import CreateContact
from svoi_pravila.application.use_cases.dismiss_suggestion import DismissSuggestion
from svoi_pravila.application.use_cases.list_contacts import ListContacts
from svoi_pravila.application.use_cases.list_rules import ListRules
from svoi_pravila.application.use_cases.list_suggestions import ListSuggestions
from svoi_pravila.application.use_cases.propose_rule import ProposeRule
from svoi_pravila.application.use_cases.rename_contact import RenameContact
from svoi_pravila.application.use_cases.set_active_contact import SetActiveContact
from svoi_pravila.config import Environment


def _stub() -> Any:
    """Inert stand-in for schema export (handlers are never invoked)."""
    return object()


def _openapi_bindings() -> MiniappRouterBindings:
    return MiniappRouterBindings(
        auth=cast(MiniappDeps, _stub()),
        list_contacts=cast(ListContacts, _stub()),
        create_contact=cast(CreateContact, _stub()),
        rename_contact=cast(RenameContact, _stub()),
        set_active_contact=cast(SetActiveContact, _stub()),
        list_rules=cast(ListRules, _stub()),
        propose_rule=cast(ProposeRule, _stub()),
        archive_rule=cast(ArchiveRule, _stub()),
        list_suggestions=cast(ListSuggestions, _stub()),
        accept_suggestion=cast(AcceptSuggestion, _stub()),
        dismiss_suggestion=cast(DismissSuggestion, _stub()),
    )


def export_openapi(destination: Path) -> None:
    app = create_app(
        CheckReadiness(probes=(), timeout_seconds=1.0),
        Environment.TEST,
        AppLifecycleHooks(extra_routers=(build_miniapp_router(_openapi_bindings()),)),
    )
    schema = app.openapi()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Destination path for openapi.json",
    )
    args = parser.parse_args()
    export_openapi(args.out)


if __name__ == "__main__":
    main()
