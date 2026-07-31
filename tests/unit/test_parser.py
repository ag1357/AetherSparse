from __future__ import annotations

from aethersparse.models import Intent
from aethersparse.parser import APOLLO_11, REL_OCCURRED_ON, DeterministicParser


def test_temporal_frame_is_executable() -> None:
    frame = DeterministicParser().parse("When did Apollo 11 land on the Moon?")

    assert frame.intent is Intent.TEMPORAL_WHEN
    assert frame.entity_id == APOLLO_11
    assert frame.relation_id == REL_OCCURRED_ON
    assert frame.answer_slot == "occurred_on"


def test_unknown_apollo_mission_is_copied_not_substituted() -> None:
    text = "When did Apollo 13 land on the Moon?"
    frame = DeterministicParser().parse(text)

    assert frame.intent is Intent.UNKNOWN
    assert frame.entity_id is None
    assert len(frame.unknown_spans) == 1
    assert frame.unknown_spans[0].surface == "Apollo 13"
    assert text[frame.unknown_spans[0].char_start : frame.unknown_spans[0].char_end] == "Apollo 13"


def test_unknown_part_number_is_copied_exactly() -> None:
    text = "Did Apollo 11 use RV1106?"
    frame = DeterministicParser().parse(text)

    assert [span.surface for span in frame.unknown_spans] == ["RV1106"]

