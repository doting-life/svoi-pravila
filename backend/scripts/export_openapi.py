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
from svoi_pravila.application.ports.bot_username import BotUsername
from svoi_pravila.application.ports.prepared_results import PreparedResults
from svoi_pravila.application.ports.pseudonymizer import Pseudonymizer
from svoi_pravila.application.ports.rate_limiter import RateLimiter
from svoi_pravila.application.ports.rule_sources import RuleSources
from svoi_pravila.application.use_cases.accept_age_confirmation import AcceptAgeConfirmation
from svoi_pravila.application.use_cases.accept_invite import AcceptInvite
from svoi_pravila.application.use_cases.accept_suggestion import AcceptSuggestion
from svoi_pravila.application.use_cases.approve_rule import ApproveRule
from svoi_pravila.application.use_cases.archive_rule import ArchiveRule
from svoi_pravila.application.use_cases.check_readiness import CheckReadiness
from svoi_pravila.application.use_cases.compose_generation import ComposeGeneration
from svoi_pravila.application.use_cases.create_contact import CreateContact
from svoi_pravila.application.use_cases.create_invite import CreateInvite
from svoi_pravila.application.use_cases.decode_incoming import DecodeIncoming
from svoi_pravila.application.use_cases.delete_my_account import DeleteMyAccount
from svoi_pravila.application.use_cases.dismiss_suggestion import DismissSuggestion
from svoi_pravila.application.use_cases.get_consent_document import GetConsentDocument
from svoi_pravila.application.use_cases.grant_consent import GrantConsent
from svoi_pravila.application.use_cases.issue_export_download import IssueExportDownload
from svoi_pravila.application.use_cases.leave_pair import LeavePair
from svoi_pravila.application.use_cases.list_contacts import ListContacts
from svoi_pravila.application.use_cases.list_pending_rules import ListPendingRules
from svoi_pravila.application.use_cases.list_rules import ListRules
from svoi_pravila.application.use_cases.list_suggestions import ListSuggestions
from svoi_pravila.application.use_cases.propose_rule import ProposeRule
from svoi_pravila.application.use_cases.record_inline_choice import RecordInlineChoice
from svoi_pravila.application.use_cases.reject_pending_rule import RejectPendingRule
from svoi_pravila.application.use_cases.rename_contact import RenameContact
from svoi_pravila.application.use_cases.resolve_invite import ResolveInvite
from svoi_pravila.application.use_cases.revoke_all_consents import RevokeAllConsents
from svoi_pravila.application.use_cases.serve_export_download import ServeExportDownload
from svoi_pravila.application.use_cases.set_active_contact import SetActiveContact
from svoi_pravila.application.use_cases.suggest_rule_from_decode import SuggestRuleFromDecode
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
        create_invite=cast(CreateInvite, _stub()),
        leave_pair=cast(LeavePair, _stub()),
        list_rules=cast(ListRules, _stub()),
        list_pending_rules=cast(ListPendingRules, _stub()),
        propose_rule=cast(ProposeRule, _stub()),
        archive_rule=cast(ArchiveRule, _stub()),
        approve_rule=cast(ApproveRule, _stub()),
        reject_pending_rule=cast(RejectPendingRule, _stub()),
        list_suggestions=cast(ListSuggestions, _stub()),
        accept_suggestion=cast(AcceptSuggestion, _stub()),
        dismiss_suggestion=cast(DismissSuggestion, _stub()),
        accept_age=cast(AcceptAgeConfirmation, _stub()),
        grant_consent=cast(GrantConsent, _stub()),
        get_consent_document=cast(GetConsentDocument, _stub()),
        resolve_invite=cast(ResolveInvite, _stub()),
        accept_invite=cast(AcceptInvite, _stub()),
        issue_export_download=cast(IssueExportDownload, _stub()),
        serve_export_download=cast(ServeExportDownload, _stub()),
        revoke_all_consents=cast(RevokeAllConsents, _stub()),
        delete_my_account=cast(DeleteMyAccount, _stub()),
        export_rate_limiter=cast(RateLimiter, _stub()),
        display_timezone="Europe/Moscow",
        analytics_timezone="Europe/Moscow",
        miniapp_url="https://miniapp.example",
        decode_incoming=cast(DecodeIncoming, _stub()),
        suggest_rule_from_decode=cast(SuggestRuleFromDecode, _stub()),
        compose_generation=cast(ComposeGeneration, _stub()),
        record_choice=cast(RecordInlineChoice, _stub()),
        prepared_results=cast(PreparedResults, _stub()),
        rule_sources=cast(RuleSources, _stub()),
        pseudonymizer=cast(Pseudonymizer, _stub()),
        quota_gate=cast(Any, _stub()),
        clock=cast(Any, _stub()),
        bot_username=cast(BotUsername, _stub()),
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
