"""Crisis-screen normalization and v2 pattern precision."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from svoi_pravila.application.crisis_screen import CrisisScreen, load_crisis_pattern_sources
from svoi_pravila.domain.safety import crisis_hit, load_data_lines, normalize_crisis_text

_SCREEN = CrisisScreen.load_ru_v2()

_POSITIVE: tuple[str, ...] = (
    "Я не хочу жить",
    "он сказал, что не хочет жить",
    "он сказал: «не хочу жить»",
    "думаю о суициде",
    "совершу самоубийство",
    "хочу покончить с собой",
    "покончу с собой сегодня",
    "она покончит с собой",
    "хочу умереть",
    "он хочет умереть",
    "хочу сдохнуть",
    "уйду из жизни",
    "хочет уйти из жизни",
    "нет смысла жить",
    "незачем жить",
    "лучше умереть",
    "лучше быть мёртвым",
    "сведу счеты с жизнью",
    "свести счеты с жизнью",
    "повешусь",
    "хочу повеситься",
    "отравлюсь",
    "хочу отравиться",
    "спрыгну с крыши",
    "прыгну с крыши",
    "брошусь под машину",
    "кинусь под поезд",
    "наглотаюсь таблеток",
    "убью себя",
    "хочу убить себя",
    "порежу вены",
    "порезать вены",
    "режу вены",
    "вскрою вены",
    "это самоповреждение",
    "причиню себе вред",
    "нанесу себе вред",
)

THREAT_AND_HYPERBOLE_NEGATIVES: tuple[str, ...] = (
    "я тебя убью, если опять опоздаешь",
    "убью за такое",
    "хочу умереть со смеху",
    "можно отравиться этим супом",
    "лучше умереть, чем снова идти на это собрание",
    "повеситься можно с этой работой",
    "убить время",
    "самоубийственная идея",
    "убью тебя если не замолчишь",
    "я его убью на собрании шутка",
    "хочу умереть от стыда",
    "хочу умереть от смеха",
    "спрыгну с этой темы",
    "убить двух зайцев",
    "не хочу жить в этой квартире",
)

_NEGATIVE: tuple[str, ...] = (
    *THREAT_AND_HYPERBOLE_NEGATIVES,
    "мне тяжело",
    "я устал",
    "я на пределе",
    "давай убьём время",
    "убить скуку",
    "не хочу ссориться",
    "хочу жить дальше",
    "не умирай",
    "живой человек рядом",
    "я злюсь",
    "помоги сказать нет",
    "порезать салат",
    "повесить картину",
    "свести счеты в бухгалтерии",
    "прыгну в омут с головой",
    "я устал от этой жизни",
    "мне плохо на душе",
    "хочу всё закончить на работе",
    "временно трудно",
    "крик души",
    "жизнь прекрасна",
    "суета сует",
    "сам по себе",
    "вены на руках болят",
    "не хочу об этом говорить",
    " suffocating deadline ",
    "я на грани срыва на работе",
    "лучше быть честным",
    "хочу убить время вечером",
)


@pytest.mark.unit
def test_v2_pattern_count_is_stable() -> None:
    sources = load_crisis_pattern_sources()
    assert len(sources) >= 30
    assert all(source.startswith("\\b") for source in sources)


@pytest.mark.unit
@pytest.mark.parametrize("phrase", _POSITIVE)
def test_explicit_crisis_hits(phrase: str) -> None:
    assert _SCREEN.hit(phrase)


@pytest.mark.unit
@pytest.mark.parametrize("phrase", _NEGATIVE)
def test_mild_and_idiom_misses(phrase: str) -> None:
    assert not _SCREEN.hit(phrase)


@pytest.mark.unit
def test_normalize_maps_yo_and_strips_edge_punct() -> None:
    assert normalize_crisis_text("  Ёлка, «Жить!!!»  ") == "елка жить"


@pytest.mark.unit
def test_load_data_lines_skips_comments() -> None:
    assert load_data_lines("# c\n\nабв\n#x\nгд") == ("абв", "гд")


@pytest.mark.unit
def test_empty_normalized_text_misses() -> None:
    assert not crisis_hit("!!!", _SCREEN.patterns)


@given(st.sampled_from(_POSITIVE))
@pytest.mark.unit
def test_positive_property(phrase: str) -> None:
    padded = f"«{phrase}!!!»"
    assert _SCREEN.hit(padded)


@given(st.sampled_from(_NEGATIVE))
@pytest.mark.unit
def test_negative_property(phrase: str) -> None:
    assert not _SCREEN.hit(f"  {phrase}  ")
