from __future__ import annotations

from collections.abc import Callable

from aethersparse.substrate import (
    FlatHybridRetriever,
    FlatStructuredPack,
    ObjectKind,
    RetrievalRequest,
)


def test_deterministic_fusion_uses_entity_relation_and_answer_shape(
    build_fixture_pack: Callable[[], FlatStructuredPack],
) -> None:
    pack = build_fixture_pack()
    mercury_id = next(
        entity.entity_id for entity in pack.entities if entity.canonical_title == "Mercury"
    )
    request = RetrievalRequest(
        text="What mass does Quick Silver have?",
        entity_ids=(mercury_id,),
        relation_families=("mass",),
        answer_kind=ObjectKind.QUANTITY,
        max_candidates=16,
        top_k=4,
    )

    first = FlatHybridRetriever(pack).retrieve(request)
    second = FlatHybridRetriever(pack).retrieve(request)

    assert first == second
    assert first.evidence
    best = first.evidence[0]
    assert "3.3011\N{MULTIPLICATION SIGN}10^23 kg" in next(
        chunk.text for chunk in pack.chunks if chunk.chunk_id == best.chunk_id
    )
    assert best.features.entity_fit == 1
    assert best.features.relation_fit == 1
    assert best.features.answer_type_fit == 1
    assert best.features.redirect_fit == 1


def test_candidate_and_result_bounds_are_enforced(
    build_fixture_pack: Callable[[], FlatStructuredPack],
) -> None:
    pack = build_fixture_pack()
    result = FlatHybridRetriever(pack).retrieve(
        RetrievalRequest(
            text="Mercury planet transit astronomy mass observation",
            max_candidates=2,
            top_k=1,
        )
    )

    assert result.considered_candidates <= 2
    assert len(result.evidence) <= 1
    assert result.truncated
