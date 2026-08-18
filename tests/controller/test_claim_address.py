from __future__ import annotations

import hashlib
import json

import pytest

from aethersparse.controller.answering import make_answer_plan, realize_plan, select_answer
from aethersparse.controller.claim_address import (
    ClaimAddressIndex,
    evidence_records_from_replay,
)
from aethersparse.controller.evidence import build_evidence_graph
from aethersparse.controller.models import (
    AnswerShape,
    EvidenceRecord,
    ExactSourceSpan,
    QueryFrame,
    StructuredClaim,
)
from aethersparse.controller.verification import verify_realization


def _record(
    claim_id: str,
    *,
    entity: str = "entity:ada",
    relation: str = "birth",
    value: str = "1815-12-10",
    confidence: float = 0.9,
) -> EvidenceRecord:
    text = f"The exact value is {value}."
    span = ExactSourceSpan(
        span_id=f"span:{claim_id}",
        document_id=f"doc:{claim_id}",
        source_title="Fixture",
        source_revision="1",
        source_url=f"https://example.test/{claim_id}",
        source_family=f"family:{claim_id}",
        char_start=0,
        char_end=len(text),
        text=text,
        text_hash=f"sha256:{hashlib.sha256(text.encode()).hexdigest()}",
    )
    claim = StructuredClaim(
        claim_id=claim_id,
        subject_entity_id=entity,
        relation_family=relation,
        object_value=value,
        answer_shape=AnswerShape.DATE,
        source_span_ids=(span.span_id,),
        occurred_at=value,
        confidence=confidence,
    )
    return EvidenceRecord(
        claim=claim,
        source_spans=(span,),
        entity_fit=1.0,
        relation_fit=1.0,
        answerability=1.0,
        answer_shape_fit=1.0,
        temporal_fit=1.0,
        attribution_fit=1.0,
        source_quality=1.0,
    )


def _frame(*, relations: tuple[str, ...] = ("birth",)) -> QueryFrame:
    return QueryFrame(
        normalized_query="When was Ada Lovelace born?",
        entity_mentions=(),
        answer_shape=AnswerShape.DATE,
        candidate_entity_ids=("entity:ada",),
        requested_relation_families=relations,
        required_facets=(),
        uncertainty=0.0,
        clarification_need=False,
    )


def test_exact_address_lookup_integrates_lattice_and_verifier() -> None:
    record = _record("claim:ada")
    result = ClaimAddressIndex((record,)).lookup(_frame(), limit=8)

    assert result.records == (record,)
    assert result.entity_postings_touched == 1
    assert result.relation_postings_touched == 1
    assert result.posting_bytes_read > 0
    assert result.value_lattice().candidates[0].source_span == record.source_spans[0]

    graph = result.evidence_graph("q:ada", _frame())
    selection = select_answer(_frame(), graph)
    assert selection is not None
    plan = make_answer_plan(selection, graph)
    realized = realize_plan(plan)
    assert verify_realization(_frame(), graph, plan, realized).passed


def test_union_occurs_before_global_cap_and_keeps_canonical_ids() -> None:
    records = (
        _record("claim:a", entity="entity:a", confidence=0.7),
        _record("claim:b", entity="entity:b", confidence=0.95),
        _record("claim:c", entity="entity:a", confidence=0.8),
    )
    frame = _frame().model_copy(update={"candidate_entity_ids": ("entity:a", "entity:b")})
    result = ClaimAddressIndex(records).lookup(frame, limit=2)

    assert result.candidate_count_before_cap == 3
    assert tuple(record.claim.claim_id for record in result.records) == (
        "claim:b",
        "claim:c",
    )


def test_posting_accounting_includes_every_eligible_pre_cap_record() -> None:
    records = tuple(_record(f"claim:{index}") for index in range(40))
    index = ClaimAddressIndex(records)

    bounded = index.lookup(_frame(), limit=16)
    unbounded = index.lookup(_frame(), limit=64)

    assert bounded.candidate_count_before_cap == 40
    assert bounded.candidate_count_after_cap == 16
    assert bounded.posting_bytes_read == unbounded.posting_bytes_read
    assert sum(bounded.posting_region_payload_bytes) == bounded.posting_bytes_read


def test_typed_address_does_not_charge_an_ineligible_shape_posting() -> None:
    date_record = _record("claim:date")
    entity_record = _record("claim:entity", value="Ada Lovelace").model_copy(
        update={
            "claim": _record("claim:entity", value="Ada Lovelace").claim.model_copy(
                update={"answer_shape": AnswerShape.ENTITY, "occurred_at": None}
            )
        }
    )

    date_only = ClaimAddressIndex((date_record,)).lookup(_frame(), limit=8)
    mixed = ClaimAddressIndex((date_record, entity_record)).lookup(_frame(), limit=8)

    assert mixed.records == (date_record,)
    assert mixed.relation_postings_touched == 1
    assert mixed.posting_bytes_read == date_only.posting_bytes_read


def test_missing_relation_fails_closed_instead_of_scanning_entity() -> None:
    result = ClaimAddressIndex((_record("claim:ada"),)).lookup(
        _frame(relations=("death",)), limit=8
    )

    assert result.records == ()
    assert result.unresolved_relation_ids == ("death",)


def test_serialization_is_deterministic_and_rejects_wrong_schema() -> None:
    forward = ClaimAddressIndex((_record("claim:b"), _record("claim:a")))
    reverse = ClaimAddressIndex((_record("claim:a"), _record("claim:b")))

    assert forward.to_bytes() == reverse.to_bytes()
    assert forward.manifest.index_sha256 == reverse.manifest.index_sha256
    assert forward.manifest.posting_serialized_bytes < forward.manifest.serialized_bytes
    assert forward.manifest.source_region_bytes > 0
    assert ClaimAddressIndex.from_bytes(forward.to_bytes()).to_bytes() == forward.to_bytes()
    corrupt = json.loads(forward.to_bytes())
    corrupt["schema_version"] = "wrong"
    with pytest.raises(ValueError, match="schema version"):
        ClaimAddressIndex.from_bytes(json.dumps(corrupt).encode())


def test_replay_lift_omits_unbound_claims_without_repairing_them() -> None:
    record = _record("claim:valid")
    claims = [
        record.claim.model_dump(mode="json"),
        record.claim.model_copy(
            update={"claim_id": "claim:missing", "source_span_ids": ("span:missing",)}
        ).model_dump(mode="json"),
    ]
    lifted = evidence_records_from_replay(
        claims,
        [record.source_spans[0].model_dump(mode="json")],
    )

    assert tuple(item.claim.claim_id for item in lifted) == ("claim:valid",)


def test_lattice_deduplicates_multiple_claims_at_one_typed_address() -> None:
    record = _record("claim:first")
    duplicate = record.model_copy(
        update={"claim": record.claim.model_copy(update={"claim_id": "claim:second"})}
    )
    result = ClaimAddressIndex((record, duplicate)).lookup(_frame(), limit=8)

    assert len(result.records) == 2
    assert len(result.value_lattice().candidates) == 1
    assert result.source_region_payload_bytes == (len(record.source_spans[0].text.encode()),)


def test_evidence_graph_stays_bounded_after_address_lookup() -> None:
    records = tuple(_record(f"claim:{index}", confidence=0.5 + index / 100) for index in range(20))
    result = ClaimAddressIndex(records).lookup(_frame(), limit=16)
    graph = build_evidence_graph("q", _frame(), result.records, max_claims=8)

    assert len(result.records) == 16
    assert len(graph.claims) == 8
