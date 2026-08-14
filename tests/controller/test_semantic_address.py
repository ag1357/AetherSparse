from __future__ import annotations

import gzip
import hashlib
import json
import math
from pathlib import Path

import pytest

from aethersparse.controller.models import EntityCandidate, EntityMention, ResolutionMethod
from aethersparse.controller.semantic_address import (
    RetainedAddressState,
    SemanticAddressDataError,
    SemanticAddressPlane,
    canonical_entity_id,
    classify_retained_address_state,
)


def _document() -> dict[str, object]:
    alpha_probability = 4.0 / 6.0
    beta_probability = 2.0 / 6.0
    entropy = -(
        alpha_probability * math.log(alpha_probability)
        + beta_probability * math.log(beta_probability)
    )
    return {
        "schema_version": "aethersparse.entity-anchor-statistics.v11",
        "source_pack_sha256": "a" * 64,
        "alpha": 1.0,
        "requested_mention_count": 2,
        "covered_mention_count": 1,
        "statistics": [
            {
                "mention": "alpha",
                "target_title": "alpha",
                "target_entity_id": canonical_entity_id("Alpha"),
                "occurrence_count": 3,
                "total_mention_occurrences": 4,
                "probability": alpha_probability,
                "ambiguity_count": 2,
                "entropy_nats": entropy,
                "source_document_count": 2,
                "title_indicator": True,
                "title_prior": 1.0,
                "redirect_indicator": True,
                "redirect_support_count": 1,
                "redirect_prior": 1.0,
                "alias_types": ["anchor", "title", "redirect"],
            },
            {
                "mention": "alpha",
                "target_title": "beta",
                "target_entity_id": None,
                "occurrence_count": 1,
                "total_mention_occurrences": 4,
                "probability": beta_probability,
                "ambiguity_count": 2,
                "entropy_nats": entropy,
                "source_document_count": 1,
                "title_indicator": False,
                "title_prior": 0.0,
                "redirect_indicator": False,
                "redirect_support_count": 0,
                "redirect_prior": 0.0,
                "alias_types": ["anchor"],
            },
        ],
    }


def test_plane_preserves_multi_address_probability_and_unresolved_mass() -> None:
    plane = SemanticAddressPlane.from_document(_document())
    unsupported = canonical_entity_id("Gamma")
    distribution = plane.distribution(
        "Alpha",
        retained_candidates=((canonical_entity_id("Alpha"), 0.9), (unsupported, 0.7)),
    )

    assert plane.requested_mention_count == 2
    assert plane.covered_mention_count == 1
    assert distribution.ambiguity_count == 2
    assert distribution.resolved_probability_mass == pytest.approx(4.0 / 6.0)
    assert distribution.unresolved_probability_mass == pytest.approx(2.0 / 6.0)
    assert distribution.probability_mass == pytest.approx(1.0)
    assert distribution.unsupported_retained_entity_ids == (unsupported,)
    assert distribution.smoothing_alpha == 1.0
    hypothesis = distribution.hypotheses[0]
    assert hypothesis.entity_id == canonical_entity_id("Alpha")
    assert hypothesis.occurrence_count == 3
    assert hypothesis.source_document_count == 2
    assert hypothesis.source_diversity == pytest.approx(2.0 / 3.0)
    assert hypothesis.alias_types == ("anchor", "title", "redirect")
    assert hypothesis.title_indicator is True
    assert hypothesis.title_prior == 1.0
    assert hypothesis.redirect_indicator is True
    assert hypothesis.redirect_prior == 1.0
    assert hypothesis.retained_candidate_rank == 1
    assert hypothesis.retained_candidate_confidence == pytest.approx(0.9)


def test_plane_rejects_a_minted_id_that_does_not_match_the_title() -> None:
    document = _document()
    statistics = document["statistics"]
    assert isinstance(statistics, list)
    statistics[0]["target_entity_id"] = canonical_entity_id("Wrong title")

    with pytest.raises(SemanticAddressDataError, match="canonical title"):
        SemanticAddressPlane.from_document(document)


def test_plane_rejects_probability_that_is_not_occurrence_derived() -> None:
    document = _document()
    statistics = document["statistics"]
    assert isinstance(statistics, list)
    statistics[0]["probability"] = 0.5

    with pytest.raises(SemanticAddressDataError, match="smoothed probability"):
        SemanticAddressPlane.from_document(document)


def test_gzip_loader_verifies_payload_and_hard_negative_identity(tmp_path: Path) -> None:
    document = _document()
    raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    compressed = gzip.compress(raw, mtime=0)
    statistics_path = tmp_path / "statistics.json.gz"
    manifest_path = tmp_path / "statistics.json.gz.manifest.json"
    statistics_path.write_bytes(compressed)
    manifest = {
        "schema_version": "aethersparse.entity-anchor-statistics-manifest.v11",
        "source_pack_sha256": document["source_pack_sha256"],
        "hard_negatives_sha256": "b" * 64,
        "alpha": 1.0,
        "requested_mention_count": 2,
        "covered_mention_count": 1,
        "statistic_count": 2,
        "output_gzip_sha256": hashlib.sha256(compressed).hexdigest(),
        "output_json_sha256": hashlib.sha256(raw).hexdigest(),
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    plane = SemanticAddressPlane.from_gzip(
        statistics_path,
        manifest_path,
        expected_hard_negatives_sha256="b" * 64,
    )
    assert plane.identity is not None
    assert plane.identity.statistic_count == 2

    manifest["output_json_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(SemanticAddressDataError, match="output_json_sha256"):
        SemanticAddressPlane.from_gzip(statistics_path, manifest_path)


def _mention(
    candidates: tuple[EntityCandidate, ...], selected_entity_id: str | None = None
) -> EntityMention:
    return EntityMention(
        surface="Alpha",
        char_start=0,
        char_end=5,
        candidates=candidates,
        selected_entity_id=selected_entity_id,
        selected_confidence=candidates[0].confidence if candidates else 0.0,
        resolution_method=(candidates[0].method if candidates else ResolutionMethod.UNKNOWN),
        copy_status="linked" if selected_entity_id else "ambiguous",
    )


def _candidate(entity_id: str, confidence: float) -> EntityCandidate:
    return EntityCandidate(
        entity_id=entity_id,
        title="Alpha",
        method=ResolutionMethod.ALIAS,
        name_score=0.9,
        type_score=1.0,
        relation_score=1.0,
        context_score=0.5,
        confidence=confidence,
    )


def test_retained_taxonomy_never_invents_outside_cap() -> None:
    required = canonical_entity_id("Alpha")
    wrong = canonical_entity_id("Beta")
    assert (
        classify_retained_address_state((required,), ()) is RetainedAddressState.MENTION_SET_EMPTY
    )
    assert (
        classify_retained_address_state((required,), (_mention((_candidate(wrong, 0.9),)),))
        is RetainedAddressState.REQUIRED_ABSENT_FROM_RETAINED_SET
    )
    assert (
        classify_retained_address_state(
            (required,), (_mention((_candidate(required, 0.8), _candidate(wrong, 0.75))),)
        )
        is RetainedAddressState.REQUIRED_TOP_RANKED_NOT_SELECTED
    )
    assert (
        classify_retained_address_state(
            (required,),
            (_mention((_candidate(wrong, 0.9), _candidate(required, 0.8)), wrong),),
        )
        is RetainedAddressState.REQUIRED_PRESENT_SELECTION_INCOMPLETE
    )
    assert (
        classify_retained_address_state(
            (required,), (_mention((_candidate(required, 0.9),), required),)
        )
        is RetainedAddressState.REQUIRED_SELECTED
    )
