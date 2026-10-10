"""Shared MiniappRouterBindings factory for API tests."""

from __future__ import annotations

from typing import Any

from svoi_pravila.adapters.channels.telegram.bot_username import BotUsernameCache
from svoi_pravila.adapters.system.tone_suggestion_catalog import StaticToneSuggestionCatalog
from svoi_pravila.api.miniapp import MiniappDeps, MiniappRouterBindings
from svoi_pravila.application.crisis_screen import CrisisScreen
from svoi_pravila.application.ports.pair_notifier import PairNotifier
from svoi_pravila.application.ports.rate_limiter import RateLimiter
from svoi_pravila.application.use_cases.accept_age_confirmation import AcceptAgeConfirmation
from svoi_pravila.application.use_cases.accept_invite import AcceptInvite
from svoi_pravila.application.use_cases.accept_suggestion import AcceptSuggestion
from svoi_pravila.application.use_cases.approve_rule import ApproveRule
from svoi_pravila.application.use_cases.archive_rule import ArchiveRule
from svoi_pravila.application.use_cases.compose_generation import (
    ComposeGeneration,
    ComposeGenerationPorts,
)
from svoi_pravila.application.use_cases.create_contact import CreateContact
from svoi_pravila.application.use_cases.create_invite import CreateInvite
from svoi_pravila.application.use_cases.delete_my_account import (
    DeleteMyAccount,
    DeleteMyAccountPorts,
)
from svoi_pravila.application.use_cases.dismiss_suggestion import DismissSuggestion
from svoi_pravila.application.use_cases.export_my_data import ExportMyData
from svoi_pravila.application.use_cases.get_consent_document import GetConsentDocument
from svoi_pravila.application.use_cases.grant_consent import GrantConsent
from svoi_pravila.application.use_cases.issue_export_download import IssueExportDownload
from svoi_pravila.application.use_cases.leave_pair import LeavePair
from svoi_pravila.application.use_cases.list_contacts import ListContacts
from svoi_pravila.application.use_cases.list_pending_rules import ListPendingRules
from svoi_pravila.application.use_cases.list_rules import ListRules
from svoi_pravila.application.use_cases.list_suggestions import ListSuggestions
from svoi_pravila.application.use_cases.propose_rule import ProposeRule
from svoi_pravila.application.use_cases.record_inline_choice import (
    RecordInlineChoice,
    RecordInlineChoicePorts,
)
from svoi_pravila.application.use_cases.reject_pending_rule import RejectPendingRule
from svoi_pravila.application.use_cases.rename_contact import RenameContact
from svoi_pravila.application.use_cases.resolve_invite import ResolveInvite
from svoi_pravila.application.use_cases.revoke_all_consents import RevokeAllConsents
from svoi_pravila.application.use_cases.serve_export_download import ServeExportDownload
from svoi_pravila.application.use_cases.set_active_contact import SetActiveContact
from tests.fakes.export_download import FakeExportDownloadStore
from tests.fakes.quota_budget import FakeLlmBudget, FakeQuotaGate
from tests.fakes.rate_limit import FakePseudonymizer, FakeRateLimiter
from tests.support.miniapp_decode import MiniappDecodeBundle


def build_test_miniapp_bindings(
    *,
    auth: MiniappDeps,
    world: Any,
    decode_bundle: MiniappDecodeBundle,
    reuse: Any,
    export_store: FakeExportDownloadStore | None = None,
    export_rate_limiter: RateLimiter | None = None,
    notifier: PairNotifier | None = None,
    miniapp_url: str | None = "https://miniapp.test",
    enable_test_routes: bool = True,
    bot_username: str = "test_bot",
    display_timezone: str = "Europe/Moscow",
    overrides: dict[str, Any] | None = None,
) -> MiniappRouterBindings:
    """Compose router bindings against an AppWorld-like test fixture."""
    store = export_store if export_store is not None else FakeExportDownloadStore()
    pair_notifier = notifier if notifier is not None else world.notifier
    export_my_data = ExportMyData(world.uow_factory, world.clock)
    limiter = export_rate_limiter if export_rate_limiter is not None else FakeRateLimiter(limit=3)
    tokens = getattr(world, "tokens", None)
    if tokens is None:
        msg = "world must expose tokens for CreateInvite"
        raise TypeError(msg)
    bindings_kwargs: dict[str, Any] = {
        "auth": auth,
        "list_contacts": ListContacts(world.uow_factory, world.catalog),
        "create_contact": CreateContact(world.uow_factory, world.catalog, world.ids, world.clock),
        "rename_contact": RenameContact(world.uow_factory, world.catalog),
        "set_active_contact": SetActiveContact(world.uow_factory, world.catalog),
        "create_invite": CreateInvite(
            world.uow_factory, world.catalog, world.ids, tokens, world.clock
        ),
        "leave_pair": LeavePair(world.uow_factory, world.ids, world.clock, pair_notifier),
        "list_rules": ListRules(world.uow_factory, world.catalog),
        "list_pending_rules": ListPendingRules(world.uow_factory, world.catalog),
        "propose_rule": ProposeRule(
            world.uow_factory, world.catalog, world.ids, world.clock, pair_notifier
        ),
        "archive_rule": ArchiveRule(world.uow_factory, world.catalog, world.clock),
        "approve_rule": ApproveRule(world.uow_factory, world.catalog, world.clock, pair_notifier),
        "reject_pending_rule": RejectPendingRule(
            world.uow_factory, world.catalog, world.clock, pair_notifier
        ),
        "list_suggestions": ListSuggestions(world.uow_factory, world.catalog),
        "accept_suggestion": AcceptSuggestion(
            world.uow_factory, world.catalog, world.ids, world.clock
        ),
        "dismiss_suggestion": DismissSuggestion(world.uow_factory, world.catalog, world.clock),
        "accept_age": AcceptAgeConfirmation(world.uow_factory, world.ids, world.clock),
        "grant_consent": GrantConsent(world.uow_factory, world.catalog, world.ids, world.clock),
        "get_consent_document": GetConsentDocument(world.catalog),
        "resolve_invite": ResolveInvite(world.uow_factory, world.catalog, world.clock),
        "accept_invite": AcceptInvite(
            world.uow_factory, world.catalog, world.ids, world.clock, pair_notifier
        ),
        "issue_export_download": IssueExportDownload(world.uow_factory, store, world.clock),
        "serve_export_download": ServeExportDownload(store, export_my_data, world.uow_factory),
        "revoke_all_consents": RevokeAllConsents(world.uow_factory, world.clock, reuse),
        "delete_my_account": DeleteMyAccount(
            DeleteMyAccountPorts(
                world.uow_factory,
                world.ids,
                FakePseudonymizer(),
                world.clock,
                reuse,
                pair_notifier,
            )
        ),
        "export_rate_limiter": limiter,
        "display_timezone": display_timezone,
        "analytics_timezone": display_timezone,
        "miniapp_url": miniapp_url,
        "decode_incoming": decode_bundle.decode_incoming,
        "suggest_rule_from_decode": decode_bundle.suggest_rule_from_decode,
        "compose_generation": ComposeGeneration(
            ComposeGenerationPorts(
                uow_factory=world.uow_factory,
                catalog=world.catalog,
                generator=decode_bundle.generator,
                quota_gate=FakeQuotaGate(limit=300),
                llm_budget=FakeLlmBudget(),
                sink=decode_bundle.sink,
                clock=world.clock,
                monotonic=world.clock,
                ids=world.ids,
                pseudonymizer=decode_bundle.pseudonymizer,
                crisis_screen=CrisisScreen.load_ru_v2(),
                deadline_seconds=8.0,
                analytics_timezone=display_timezone,
            )
        ),
        "record_choice": RecordInlineChoice(
            RecordInlineChoicePorts(
                sink=decode_bundle.sink,
                uow_factory=world.uow_factory,
                catalog=world.catalog,
                tone_catalog=StaticToneSuggestionCatalog(),
                clock=world.clock,
                ids=world.ids,
                pseudonymizer=decode_bundle.pseudonymizer,
            )
        ),
        "prepared_results": decode_bundle.prepared_results,
        "rule_sources": decode_bundle.rule_sources,
        "pseudonymizer": decode_bundle.pseudonymizer,
        "quota_gate": FakeQuotaGate(limit=40),
        "clock": world.clock,
        "bot_username": BotUsernameCache(username=bot_username),
        "enable_test_routes": enable_test_routes,
    }
    if overrides:
        bindings_kwargs.update(overrides)
    return MiniappRouterBindings(**bindings_kwargs)
