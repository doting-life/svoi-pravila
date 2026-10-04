"""RecordInlineChoice and result_id parsing."""

from __future__ import annotations

import pytest

from svoi_pravila.application.errors import InvalidInlineResultRef
from svoi_pravila.application.inline_result_ref import (
    encode_inline_result_ref,
    parse_inline_result_ref,
)
from svoi_pravila.application.use_cases.record_inline_choice import (
    RecordInlineChoice,
    RecordInlineChoiceCommand,
)
from svoi_pravila.domain.enums import (
    Firmness,
    UsageEventKind,
    UsageScenario,
    UsageSurface,
)
from svoi_pravila.domain.ids import TelegramUserId
from tests.fakes.clock import FakeClock
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.rate_limit import FakePseudonymizer
from tests.fakes.usage_sink import FailingUsageEventSink, RecordingUsageEventSink


@pytest.mark.unit
def test_inline_result_ref_roundtrip() -> None:
    ref = encode_inline_result_ref(UsageScenario.SOFTEN, Firmness.GENTLE)
    assert len(ref.encode()) <= 64
    assert parse_inline_result_ref(ref) == (UsageScenario.SOFTEN, Firmness.GENTLE)


@pytest.mark.unit
def test_inline_result_ref_rejects_text_and_junk() -> None:
    with pytest.raises(InvalidInlineResultRef):
        parse_inline_result_ref("please leave me alone")
    with pytest.raises(InvalidInlineResultRef):
        parse_inline_result_ref("s:nope:f:gentle")
    with pytest.raises(InvalidInlineResultRef):
        parse_inline_result_ref("x" * 65)


@pytest.mark.unit
async def test_record_inline_choice_writes_c0_event() -> None:
    sink = RecordingUsageEventSink()
    use_case = RecordInlineChoice(sink, FakeClock(), FakeIdGenerator(), FakePseudonymizer())
    ref = encode_inline_result_ref(UsageScenario.HELP_SAY, Firmness.FIRM)
    await use_case.execute(RecordInlineChoiceCommand(TelegramUserId(9), ref))
    event = sink.events[0]
    assert event.event_kind is UsageEventKind.RESULT_CHOSEN
    assert event.surface is UsageSurface.INLINE
    assert event.scenario is UsageScenario.HELP_SAY
    assert event.variant_firmness is Firmness.FIRM
    assert event.model is None


@pytest.mark.unit
async def test_record_inline_choice_invalid_ref_does_not_write() -> None:
    sink = RecordingUsageEventSink()
    use_case = RecordInlineChoice(sink, FakeClock(), FakeIdGenerator(), FakePseudonymizer())
    with pytest.raises(InvalidInlineResultRef):
        await use_case.execute(RecordInlineChoiceCommand(TelegramUserId(9), "nope"))
    assert sink.events == []


@pytest.mark.unit
async def test_record_inline_choice_swallows_sink_failure() -> None:
    use_case = RecordInlineChoice(
        FailingUsageEventSink(), FakeClock(), FakeIdGenerator(), FakePseudonymizer()
    )
    await use_case.execute(
        RecordInlineChoiceCommand(
            TelegramUserId(9),
            encode_inline_result_ref(UsageScenario.DECODE, Firmness.BALANCED),
        )
    )
