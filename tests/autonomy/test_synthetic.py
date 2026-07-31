from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from aethersparse.autonomy.extraction import (
    AdjudicationDecision,
    IndependentAdjudicator,
    IndependentExtractor,
    IndependentValidator,
    ValidationDecision,
)
from aethersparse.autonomy.synthetic import (
    DEBUG_SCALE,
    DECISIVE_SCALE,
    INTERMEDIATE_SCALE,
    SyntheticWorld,
    generate_partition_pair,
    generate_world,
    verify_world,
)


@pytest.fixture(scope="module")
def worlds() -> tuple[SyntheticWorld, SyntheticWorld]:
    return generate_partition_pair(DEBUG_SCALE, master_seed="autonomy-test-seed")


@pytest.fixture(scope="module")
def pipeline(
    worlds: tuple[SyntheticWorld, SyntheticWorld],
) -> tuple[object, object, object]:
    development, _ = worlds
    extraction = IndependentExtractor(development.entities).extract_world(development)
    validation = IndependentValidator(development.entities).validate_world(
        development,
        extraction,
    )
    adjudication = IndependentAdjudicator().adjudicate_world(
        development,
        extraction,
        validation,
    )
    return extraction, validation, adjudication


def test_scale_contracts_match_progressive_targets() -> None:
    assert (
        DEBUG_SCALE.entity_count,
        DEBUG_SCALE.packet_count,
        DEBUG_SCALE.question_count,
    ) == (100, 1_000, 500)
    assert (
        INTERMEDIATE_SCALE.entity_count,
        INTERMEDIATE_SCALE.packet_count,
        INTERMEDIATE_SCALE.question_count,
    ) == (1_000, 10_000, 5_000)
    assert (
        DECISIVE_SCALE.entity_count,
        DECISIVE_SCALE.packet_count,
        DECISIVE_SCALE.question_count,
    ) == (5_000, 50_000, 20_000)


def test_generation_is_deterministic_and_content_addressed(
    worlds: tuple[SyntheticWorld, SyntheticWorld],
    tmp_path: Path,
) -> None:
    development, _ = worlds
    regenerated = generate_world(
        DEBUG_SCALE,
        partition="development",
        master_seed="autonomy-test-seed",
        cache_dir=tmp_path,
    )
    cached = generate_world(
        DEBUG_SCALE,
        partition="development",
        master_seed="autonomy-test-seed",
        cache_dir=tmp_path,
    )

    assert regenerated == development
    assert cached == regenerated
    assert list(tmp_path.rglob("*.json"))
    assert regenerated.manifest.generator_version == "1.0.0"
    assert regenerated.manifest.artifact_hash.startswith("sha256:")


def test_hidden_partition_has_a_separate_seed_and_identity(
    worlds: tuple[SyntheticWorld, SyntheticWorld],
) -> None:
    development, evaluation = worlds

    assert development.manifest.partition == "development"
    assert evaluation.manifest.partition == "evaluation"
    assert development.manifest.seed_digest != evaluation.manifest.seed_digest
    assert development.manifest.world_id != evaluation.manifest.world_id
    assert {entity.entity_id for entity in development.entities}.isdisjoint(
        entity.entity_id for entity in evaluation.entities
    )
    assert {claim.claim_id for claim in development.claims}.isdisjoint(
        claim.claim_id for claim in evaluation.claims
    )
    assert {question.question_id for question in development.questions}.isdisjoint(
        question.question_id for question in evaluation.questions
    )


def test_world_contains_required_gold_and_adversarial_structures(
    worlds: tuple[SyntheticWorld, SyntheticWorld],
) -> None:
    development, _ = worlds
    packet_types = {claim.packet_type for claim in development.claims}
    relations = {claim.relation for claim in development.claims}
    categories = {question.category for question in development.questions}

    assert packet_types == {"PROPOSITION", "EVENT", "QUOTATION", "PERSPECTIVE"}
    assert {"precedes", "causes", "has_mass", "activated", "said"} <= relations
    assert any(claim.date_value for claim in development.claims)
    assert any(claim.quantity_value is not None for claim in development.claims)
    assert any(claim.contradiction_of for claim in development.claims)
    assert any(claim.missing_evidence for claim in development.claims)
    assert any(claim.ambiguous_entity for claim in development.claims)
    assert any(claim.contains_unknown_term for claim in development.claims)
    assert any(claim.domain == "out_of_domain" for claim in development.claims)
    assert any(source.source_family == "family_derived" for source in development.sources)
    assert {
        "wrong_premise",
        "conflicting_sources",
        "missing_evidence",
        "unknown_term",
        "out_of_domain",
        "session_followup",
        "negation_mutation",
        "attribution_mutation",
    } <= categories


def test_rendered_prose_has_exact_character_and_byte_alignments(
    worlds: tuple[SyntheticWorld, SyntheticWorld],
) -> None:
    development, _ = worlds
    verify_world(development)
    saw_multibyte_prefix = False
    for source in development.sources:
        for span in source.spans:
            assert (
                source.raw_text[span.raw_char_start : span.raw_char_end]
                == span.raw_text
            )
            assert len(source.raw_text[: span.raw_char_start].encode()) == span.raw_byte_start
            assert len(source.raw_text[: span.raw_char_end].encode()) == span.raw_byte_end
            saw_multibyte_prefix |= span.raw_byte_start != span.raw_char_start
    assert saw_multibyte_prefix


def test_extractor_validator_and_adjudicator_are_separate_and_hashed(
    worlds: tuple[SyntheticWorld, SyntheticWorld],
    pipeline: tuple[object, object, object],
) -> None:
    development, _ = worlds
    extraction, validation, adjudication = pipeline
    extractor = IndependentExtractor(development.entities)
    validator = IndependentValidator(development.entities)
    adjudicator = IndependentAdjudicator()

    assert extraction.extractor_identity == extractor.identity
    assert validation.validator_identity == validator.identity
    assert adjudication.adjudicator_identity == adjudicator.identity
    assert len(
        {
            extraction.extractor_identity,
            validation.validator_identity,
            adjudication.adjudicator_identity,
        }
    ) == 3
    assert extraction.artifact_hash.startswith("sha256:")
    assert validation.artifact_hash.startswith("sha256:")
    assert adjudication.artifact_hash.startswith("sha256:")
    assert extraction.candidate_count == len(extraction.candidates)
    assert validation.result_count == extraction.candidate_count
    assert adjudication.result_count == extraction.candidate_count
    assert Counter(result.decision for result in adjudication.results)[
        AdjudicationDecision.CANONICAL
    ] > 0


def test_extractor_validator_disagreement_is_quarantined(
    worlds: tuple[SyntheticWorld, SyntheticWorld],
    pipeline: tuple[object, object, object],
) -> None:
    development, _ = worlds
    extraction, validation, adjudication_artifact = pipeline
    canonical_ids = {
        result.candidate_id
        for result in adjudication_artifact.results
        if result.decision is AdjudicationDecision.CANONICAL
    }
    original_validation = next(
        result
        for result in validation.results
        if result.candidate_id in canonical_ids
        and result.decision is ValidationDecision.PASS
    )
    candidate = next(
        item
        for item in extraction.candidates
        if item.candidate_id == original_validation.candidate_id
    )
    disagreement = original_validation.model_copy(
        update={"decision": ValidationDecision.REVIEW}
    )

    result = IndependentAdjudicator().adjudicate(
        candidate,
        disagreement,
        synthetic_world=development,
    )

    assert result.decision is AdjudicationDecision.QUARANTINE
    assert "extractor_validator_disagreement" in result.reasons


def test_gold_mutation_is_rejected_and_self_approval_is_forbidden(
    worlds: tuple[SyntheticWorld, SyntheticWorld],
    pipeline: tuple[object, object, object],
) -> None:
    development, _ = worlds
    extraction, validation, adjudication_artifact = pipeline
    canonical_id = next(
        result.candidate_id
        for result in adjudication_artifact.results
        if result.decision is AdjudicationDecision.CANONICAL
    )
    candidate = next(item for item in extraction.candidates if item.candidate_id == canonical_id)
    validator_result = next(
        item for item in validation.results if item.candidate_id == canonical_id
    )
    adjudicator = IndependentAdjudicator()

    mutated = candidate.model_copy(update={"relation": "mutated_relation"})
    rejected = adjudicator.adjudicate(
        mutated,
        validator_result,
        synthetic_world=development,
    )
    self_approval = validator_result.model_copy(
        update={
            "validator_identity": candidate.extractor_identity,
            "independent_from_extractor": False,
        }
    )
    forbidden = adjudicator.adjudicate(
        candidate,
        self_approval,
        synthetic_world=development,
    )

    assert rejected.decision is AdjudicationDecision.REJECT
    assert "structured_gold_mismatch" in rejected.reasons
    assert forbidden.decision is AdjudicationDecision.REJECT
    assert "self_approval_forbidden" in forbidden.reasons
