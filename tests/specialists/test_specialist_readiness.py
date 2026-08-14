from __future__ import annotations

from copy import deepcopy

import pytest

from aethersparse.specialists.readiness import qualify_specialist_readiness


def _candidate(entity_id: str) -> dict[str, object]:
    return {
        "entity_id": entity_id,
        "title": entity_id,
        "method": "alias",
        "name_score": 0.9,
        "type_score": 1.0,
        "relation_score": 1.0,
        "context_score": 0.0,
        "confidence": 0.8,
    }


def _mention(surface: str, *entity_ids: str) -> dict[str, object]:
    return {
        "surface": surface,
        "correct_entity_per_mention": None,
        "candidates": [_candidate(entity_id) for entity_id in entity_ids],
    }


def _entity_case(
    case_id: str,
    partition: str,
    tier: str,
    required: str,
    mention: dict[str, object],
) -> dict[str, object]:
    return {
        "case_id": case_id,
        "partition": partition,
        "correct_entity_ids": [required],
        "replicas": [
            {
                "corpus_tier": tier,
                "training_eligible": True,
                "failure_class": "correct_entity_present_but_misranked",
                "mentions": [mention],
            }
        ],
    }


def _value_replica(
    *,
    case_id: str = "value:source",
    partition: str = "tuning",
    tier: str = "397k",
    classification: str = "BLOCKED_MISSING_SOURCE_CHUNK_PREPRUNING_STATE",
    answer_shape: str = "comparison",
    target: str = "42%",
    chunk_text: str = "No requested quantity is in this selected chunk.",
    runtime_candidate_values: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "case_id": case_id,
        "partition": partition,
        "corpus_tier": tier,
        "classification": classification,
        "answer_shape": answer_shape,
        "target_atomic_values": [target],
        "runtime_candidate_values": list(runtime_candidate_values),
        "pack_capture": {
            "selected_chunks": [
                {
                    "complete_chunk_text": chunk_text,
                    "runtime_boundary": {
                        "all_matches_before_region_pruning": [],
                        "top8_matches_before_deduplication": [],
                        "pre_dedup_values": [],
                        "post_dedup_values": [],
                        "pre_cap_values": [],
                        "post_cap_values": [],
                    },
                }
            ],
            "compiler_documents": [
                {
                    "document_id": "doc:value",
                    "boundary": {"all_typed_matches_before_type_caps": []},
                }
            ],
        },
    }


def _documents() -> tuple[dict[str, object], ...]:
    hard_negatives: dict[str, object] = {
        "sealed_partitions_excluded": ["evaluation", "final_held"],
        "unavailable_fields": {
            "candidate_pool_before_top8": "absent",
            "candidate_cap_failure": "absent",
            "correct_entity_outside_top_k": "absent",
        },
        "cases": [
            _entity_case(
                "entity:dev",
                "development",
                "10k",
                "entity:right-dev",
                _mention("Alpha", "entity:right-dev", "entity:wrong"),
            ),
            _entity_case(
                "entity:tune",
                "tuning",
                "397k",
                "entity:right-tune",
                _mention("Beta", "entity:wrong"),
            ),
        ],
    }
    anchors: dict[str, object] = {
        "requested_mention_count": 2,
        "covered_mention_count": 1,
        "statistics": [
            {
                "mention": "beta",
                "target_entity_id": "entity:right-tune",
                "probability": 1.0,
                "occurrence_count": 3,
                "total_mention_occurrences": 3,
                "ambiguity_count": 1,
            }
        ],
    }
    value: dict[str, object] = {
        "scope": {
            "evaluation_and_final_held_used": False,
            "partitions": ["development", "tuning"],
        },
        "replicas": [
            _value_replica(),
            _value_replica(
                case_id="value:dual",
                partition="tuning",
                tier="25k",
                classification="COMPILER_EXTRACTION_FAILURE",
                answer_shape="quotation",
                target="quoted target",
                chunk_text="The passage contains the quoted target verbatim.",
            ),
            _value_replica(
                case_id="value:semantic",
                partition="development",
                tier="10k",
                classification="CORRECT_VALUE_PRESENT_NOT_BOUND_TO_SUBJECT_RELATION",
                target="7%",
                runtime_candidate_values=("7%",),
            ),
        ],
        "neural_value_specialist": {
            "decision": "NOT_TRAINED_INSUFFICIENT_EXACT_DEVELOPMENT_SPANS",
            "direct_unique_development_spans": 116,
        },
    }
    reachability: dict[str, object] = {
        "training_failures": 695,
        "new_reachable": 306,
        "per_case": [
            {
                "case_id": "entity:dev",
                "partition": "development",
                "corpus_tier": "10k",
                "old_failure_class": "ENTITY_BINDING_WRONG",
                "recovered": False,
            },
            {
                "case_id": "entity:tune",
                "partition": "tuning",
                "corpus_tier": "397k",
                "old_failure_class": "ENTITY_BINDING_WRONG",
                "recovered": False,
            },
            *[
                {
                    "case_id": replica["case_id"],
                    "partition": replica["partition"],
                    "corpus_tier": replica["corpus_tier"],
                    "old_failure_class": "VALUE_NOT_ENUMERATED",
                    "recovered": False,
                }
                for replica in value["replicas"]
            ],
        ],
    }
    return hard_negatives, anchors, value, reachability


def test_readiness_blocks_sweep_and_quantifies_strict_threshold() -> None:
    hard_negatives, anchors, value, reachability = _documents()
    report = qualify_specialist_readiness(
        hard_negatives,
        anchors,
        value,
        reachability,
        available_anchor_tiers=("10k",),
        integrity={"all_verified": True},
    )

    assert report["decision"] == "BLOCK_CONTEXTUAL_ENTITY_SUCCESSIVE_HALVING"
    assert report["successive_halving"]["started"] is False
    assert report["successive_halving"]["requested_parameter_counts"] == [
        250_000,
        1_000_000,
        3_000_000,
        5_000_000,
    ]
    assert report["entity_readiness"]["current_candidate_complete_replicas"] == 1
    assert report["entity_readiness"]["anchor_expanded_candidate_complete_replicas"] == 2
    assert report["entity_readiness"]["explicit_mention_alignment_records"] == 0
    assert report["strict_60_feasibility"][
        "minimum_count_strictly_exceeding_threshold"
    ] == 418
    assert report["protected_partition_labels_consumed"] is False


def test_value_pack_capture_recomputes_source_first_causal_stages() -> None:
    hard_negatives, anchors, value, reachability = _documents()
    report = qualify_specialist_readiness(
        hard_negatives,
        anchors,
        value,
        reachability,
        integrity={"all_verified": True},
    )
    measured = report["value_readiness"]
    assert measured["previously_missing_boundary_fields_complete"] is True
    assert measured["historical_classification_counts"] == {
        "BLOCKED_MISSING_SOURCE_CHUNK_PREPRUNING_STATE": 1,
        "COMPILER_EXTRACTION_FAILURE": 1,
        "CORRECT_VALUE_PRESENT_NOT_BOUND_TO_SUBJECT_RELATION": 1,
    }
    assert measured["trace_recomputed_causal_decomposition_counts"] == {
        "SELECTED_SOURCE_CHUNK_ABSENCE": 1,
        "DUAL_COMPILER_RUNTIME_QUOTATION_EXTRACTION": 1,
        "SEMANTIC_SUBJECT_RELATION_BINDING": 1,
        "COMPILER_EXTRACTION": 0,
        "RUNTIME_EXTRACTION": 0,
        "REGION_PRUNING": 0,
        "PRE_DEDUP_PIPELINE_DROP": 0,
        "DEDUPLICATION": 0,
        "PRE_CAP_PIPELINE_DROP": 0,
        "CAP": 0,
        "REBINDING": 0,
    }


def test_protected_entity_partition_is_rejected() -> None:
    hard_negatives, anchors, value, reachability = _documents()
    hard_negatives["cases"][0]["partition"] = "evaluation"  # type: ignore[index]
    with pytest.raises(ValueError, match="protected or unknown"):
        qualify_specialist_readiness(
            hard_negatives,
            anchors,
            value,
            reachability,
            integrity={"all_verified": True},
        )


def test_cross_partition_tier_replica_group_is_rejected() -> None:
    hard_negatives, anchors, value, reachability = _documents()
    duplicate = deepcopy(hard_negatives["cases"][0])  # type: ignore[index]
    duplicate["partition"] = "tuning"
    duplicate["replicas"][0]["corpus_tier"] = "25k"  # type: ignore[index]
    hard_negatives["cases"].append(duplicate)  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="cross partitions"):
        qualify_specialist_readiness(
            hard_negatives,
            anchors,
            value,
            reachability,
            integrity={"all_verified": True},
        )


def test_anchor_probability_invariant_is_enforced() -> None:
    hard_negatives, anchors, value, reachability = _documents()
    anchors["statistics"][0]["probability"] = 0.5  # type: ignore[index]
    with pytest.raises(ValueError, match="sum to one"):
        qualify_specialist_readiness(
            hard_negatives,
            anchors,
            value,
            reachability,
            integrity={"all_verified": True},
        )
