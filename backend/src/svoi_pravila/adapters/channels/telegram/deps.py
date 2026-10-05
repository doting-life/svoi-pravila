"""Dependencies injected into the Telegram dispatcher workflow data."""

from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from svoi_pravila.adapters.channels.telegram.bot_username import BotUsernameCache
from svoi_pravila.adapters.channels.telegram.inline_scheduler import InlineQueryCoordinator
from svoi_pravila.adapters.channels.telegram.localization import TelegramStrings
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.confirmation_tokens import ConfirmationTokens
from svoi_pravila.application.ports.dialog_state import DialogState
from svoi_pravila.application.ports.monotonic import MonotonicClock
from svoi_pravila.application.ports.prepared_results import PreparedResults
from svoi_pravila.application.ports.pseudonymizer import Pseudonymizer
from svoi_pravila.application.ports.rate_limiter import RateLimiter
from svoi_pravila.application.ports.rule_sources import RuleSources
from svoi_pravila.application.ports.update_deduplicator import UpdateDeduplicator
from svoi_pravila.application.use_cases.accept_age_confirmation import AcceptAgeConfirmation
from svoi_pravila.application.use_cases.accept_invite import AcceptInvite
from svoi_pravila.application.use_cases.accept_suggestion import AcceptSuggestion
from svoi_pravila.application.use_cases.approve_rule import ApproveRule
from svoi_pravila.application.use_cases.archive_rule import ArchiveRule
from svoi_pravila.application.use_cases.create_contact import CreateContact
from svoi_pravila.application.use_cases.create_invite import CreateInvite
from svoi_pravila.application.use_cases.decode_incoming import IncomingDecoder
from svoi_pravila.application.use_cases.delete_my_account import DeleteMyAccount
from svoi_pravila.application.use_cases.dismiss_suggestion import DismissSuggestion
from svoi_pravila.application.use_cases.export_my_data import ExportMyData
from svoi_pravila.application.use_cases.get_consent_document import GetConsentDocument
from svoi_pravila.application.use_cases.get_onboarding_step import GetOnboardingStep
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramId
from svoi_pravila.application.use_cases.grant_consent import GrantConsent
from svoi_pravila.application.use_cases.inline_compose import InlineCompose
from svoi_pravila.application.use_cases.leave_pair import LeavePair
from svoi_pravila.application.use_cases.list_contacts import ListContacts
from svoi_pravila.application.use_cases.list_rules import ListRules
from svoi_pravila.application.use_cases.list_suggestions import ListSuggestions
from svoi_pravila.application.use_cases.propose_rule import ProposeRule
from svoi_pravila.application.use_cases.record_inline_choice import RecordInlineChoice
from svoi_pravila.application.use_cases.reject_pending_rule import RejectPendingRule
from svoi_pravila.application.use_cases.rename_contact import RenameContact
from svoi_pravila.application.use_cases.resolve_invite import ResolveInvite
from svoi_pravila.application.use_cases.revoke_all_consents import RevokeAllConsents
from svoi_pravila.application.use_cases.set_active_contact import SetActiveContact
from svoi_pravila.application.use_cases.suggest_rule_from_decode import SuggestRuleFromDecode


@dataclass(frozen=True, slots=True)
class TelegramDeps:
    """Application collaborators available to Telegram middlewares and handlers."""

    strings: TelegramStrings
    get_onboarding_step: GetOnboardingStep
    get_user_by_telegram_id: GetUserByTelegramId
    accept_age: AcceptAgeConfirmation
    grant_consent: GrantConsent
    get_consent_document: GetConsentDocument
    decode_incoming: IncomingDecoder
    inline_compose: InlineCompose
    record_inline_choice: RecordInlineChoice
    prepared_results: PreparedResults
    rule_sources: RuleSources
    suggest_rule_from_decode: SuggestRuleFromDecode
    inline_queries: InlineQueryCoordinator
    revoke_all_consents: RevokeAllConsents
    delete_my_account: DeleteMyAccount
    export_my_data: ExportMyData
    confirmation_tokens: ConfirmationTokens
    create_contact: CreateContact
    list_contacts: ListContacts
    rename_contact: RenameContact
    set_active_contact: SetActiveContact
    create_invite: CreateInvite
    resolve_invite: ResolveInvite
    accept_invite: AcceptInvite
    leave_pair: LeavePair
    propose_rule: ProposeRule
    approve_rule: ApproveRule
    reject_pending_rule: RejectPendingRule
    list_rules: ListRules
    archive_rule: ArchiveRule
    list_suggestions: ListSuggestions
    accept_suggestion: AcceptSuggestion
    dismiss_suggestion: DismissSuggestion
    dialog_state: DialogState
    bot_username: BotUsernameCache
    clock: Clock
    display_timezone: ZoneInfo
    deduplicator: UpdateDeduplicator
    rate_limiter: RateLimiter
    pseudonymizer: Pseudonymizer
    monotonic: MonotonicClock
    draft_min_interval_ms: int
    inline_cache_seconds: int
