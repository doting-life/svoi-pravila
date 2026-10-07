"""Shared decode/suggest fakes for mini-app router tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast

from svoi_pravila.application.crisis_screen import CrisisScreen
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.id_generator import IdGenerator
from svoi_pravila.application.ports.monotonic import MonotonicClock
from svoi_pravila.application.ports.prepared_results import PreparedResults
from svoi_pravila.application.ports.pseudonymizer import Pseudonymizer
from svoi_pravila.application.ports.rule_sources import RuleSources
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases.decode_incoming import DecodeIncoming, DecodeIncomingPorts
from svoi_pravila.application.use_cases.suggest_rule_from_decode import (
    SuggestRuleFromDecode,
    SuggestRuleFromDecodePorts,
)
from tests.fakes.concurrency import FakeConcurrencyGuard
from tests.fakes.generation import FakeTextGenerator
from tests.fakes.prepared import FakePreparedResults
from tests.fakes.quota_budget import FakeLlmBudget, FakeQuotaGate
from tests.fakes.rate_limit import FakePseudonymizer
from tests.fakes.rule_sources import FakeRuleSources
from tests.fakes.usage_sink import RecordingUsageEventSink

_ANALYTICS_TZ = "Europe/Moscow"


class _DecodeWorld(Protocol):
    """Minimal world surface for wiring decode fakes."""

    @property
    def uow_factory(self) -> UnitOfWorkFactory: ...

    @property
    def catalog(self) -> ConsentCatalog: ...

    @property
    def clock(self) -> Clock: ...

    @property
    def ids(self) -> IdGenerator: ...


@dataclass(frozen=True, slots=True)
class MiniappDecodeBundle:
    """Decode-related ports for MiniappRouterBindings in tests."""

    decode_incoming: DecodeIncoming
    suggest_rule_from_decode: SuggestRuleFromDecode
    prepared_results: PreparedResults
    rule_sources: RuleSources
    pseudonymizer: Pseudonymizer
    sink: RecordingUsageEventSink
    generator: FakeTextGenerator


def build_miniapp_decode_bundle(
    world: _DecodeWorld,
    *,
    generator: FakeTextGenerator | None = None,
    prepared_results: PreparedResults | None = None,
    rule_sources: RuleSources | None = None,
    pseudonymizer: Pseudonymizer | None = None,
    quota_gate: FakeQuotaGate | None = None,
    llm_budget: FakeLlmBudget | None = None,
    suggest_llm_budget: FakeLlmBudget | None = None,
) -> MiniappDecodeBundle:
    """Wire DecodeIncoming and SuggestRuleFromDecode with in-memory fakes."""
    gen = generator or FakeTextGenerator(stream_chunks=("анализ ", "готово"))
    prep = prepared_results if prepared_results is not None else FakePreparedResults()
    sources = rule_sources if rule_sources is not None else FakeRuleSources()
    pseudo = pseudonymizer if pseudonymizer is not None else FakePseudonymizer()
    sink = RecordingUsageEventSink()
    crisis = CrisisScreen.load_ru_v2()
    monotonic = cast(MonotonicClock, world.clock)
    gate = quota_gate if quota_gate is not None else FakeQuotaGate(limit=20)
    budget = llm_budget if llm_budget is not None else FakeLlmBudget()
    suggest_budget = suggest_llm_budget if suggest_llm_budget is not None else budget
    decode = DecodeIncoming(
        DecodeIncomingPorts(
            uow_factory=world.uow_factory,
            catalog=world.catalog,
            generator=gen,
            guard=FakeConcurrencyGuard(),
            quota_gate=gate,
            llm_budget=budget,
            sink=sink,
            clock=world.clock,
            monotonic=monotonic,
            ids=world.ids,
            pseudonymizer=pseudo,
            crisis_screen=crisis,
            deadline_seconds=45.0,
            max_output_tokens=1000,
            analytics_timezone=_ANALYTICS_TZ,
        )
    )
    suggest = SuggestRuleFromDecode(
        SuggestRuleFromDecodePorts(
            uow_factory=world.uow_factory,
            catalog=world.catalog,
            rule_sources=sources,
            generator=gen,
            llm_budget=suggest_budget,
            sink=sink,
            clock=world.clock,
            monotonic=monotonic,
            ids=world.ids,
            pseudonymizer=pseudo,
            crisis_screen=crisis,
            deadline_seconds=45.0,
            max_output_tokens=1000,
            analytics_timezone=_ANALYTICS_TZ,
        )
    )
    return MiniappDecodeBundle(
        decode_incoming=decode,
        suggest_rule_from_decode=suggest,
        prepared_results=prep,
        rule_sources=sources,
        pseudonymizer=pseudo,
        sink=sink,
        generator=gen,
    )
