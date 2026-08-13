from __future__ import annotations

import hashlib

from aethersparse.controller.micro_ops import MicroState
from aethersparse.controller.value_repair import repair_state_with_typed_values


def _span(text: str = "Ada Lovelace was born in 1815.") -> dict[str, object]:
    return {
        "span_id": "span:source",
        "document_id": "doc:ada",
        "source_title": "Ada Lovelace",
        "source_revision": "1",
        "source_url": "https://example.test/ada",
        "source_family": "fixture",
        "source_class": "CORPUS",
        "char_start": 0,
        "char_end": len(text),
        "text": text,
        "text_hash": f"sha256:{hashlib.sha256(text.encode()).hexdigest()}",
    }


def _state(shape: str = "date") -> MicroState:
    return MicroState(
        case_id="case:ada",
        frame={
            "answer_shape": shape,
            "candidate_entity_ids": ["entity:ada"],
            "requested_relation_families": ["birth"],
        },
        claims=(),
        source_spans=(_span(),),
    )


def test_repair_copies_only_exact_retained_source_surfaces() -> None:
    result = repair_state_with_typed_values(_state())
    assert result.added_claims == 1
    claim = result.state.claims[0]
    assert claim["object_value"] == "1815"
    assert claim["subject_entity_id"] == "entity:ada"
    assert claim["relation_family"] == "birth"
    added_span = result.state.source_spans[-1]
    assert added_span["text"] == "1815"
    assert str(added_span["text"]) in str(_span()["text"])


def test_repair_preserves_existing_claim_order_and_is_deterministic() -> None:
    state = _state().model_copy(
        update={
            "claims": (
                {
                    "claim_id": "claim:existing",
                    "subject_entity_id": "entity:ada",
                    "relation_family": "birth",
                    "object_value": "1915",
                    "answer_shape": "date",
                    "source_span_ids": ["span:source"],
                },
            )
        }
    )
    first = repair_state_with_typed_values(state)
    second = repair_state_with_typed_values(state)
    assert first == second
    assert first.state.claims[0]["claim_id"] == "claim:existing"
    assert first.state.claims[1]["object_value"] == "1815"


def test_repair_preserves_competing_entity_hypotheses_without_selection() -> None:
    state = _state().model_copy(
        update={
            "frame": {
                **_state().frame,
                "candidate_entity_ids": ["entity:ada", "entity:other"],
            }
        }
    )
    result = repair_state_with_typed_values(state)
    assert {item["subject_entity_id"] for item in result.state.claims} == {
        "entity:ada",
        "entity:other",
    }


def test_repair_is_bounded_and_does_not_handle_unsupported_shapes() -> None:
    assert repair_state_with_typed_values(_state("definition")).added_claims == 0
    state = _state().model_copy(
        update={
            "claims": tuple(
                {
                    "claim_id": f"claim:{index}",
                    "subject_entity_id": "entity:ada",
                    "relation_family": "birth",
                    "object_value": str(index),
                    "answer_shape": "date",
                    "source_span_ids": ["span:source"],
                }
                for index in range(64)
            )
        }
    )
    result = repair_state_with_typed_values(state)
    assert result.added_claims == 0
    assert result.candidate_capacity_exhausted is True
