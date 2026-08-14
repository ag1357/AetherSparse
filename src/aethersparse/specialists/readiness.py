"""Split-safe readiness gates for contextual specialist qualification.

The Mission 6 handoff contains useful deterministic corpus statistics, but a
parameter sweep is only lawful when its candidate set, labels, and tier
coverage are sufficient.  This module turns those prerequisites into measured
gates.  It never fits a model and never consumes evaluation or final-held rows.
"""

from __future__ import annotations

import math
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

TRAINING_PARTITIONS = frozenset({"development", "tuning"})
REQUIRED_TIERS = ("10k", "25k", "397k")
REQUESTED_ENTITY_PARAMETER_COUNTS = (250_000, 1_000_000, 3_000_000, 5_000_000)

# A contextual ranker is not useful when its correct address is usually absent.
# This is a readiness threshold, not a product-accuracy claim.  It is applied to
# tuning only after deterministic candidate generation is frozen on development.
MINIMUM_TUNING_CANDIDATE_COMPLETE_RECALL = 0.90
STRICT_REACHABILITY_THRESHOLD = 0.60


def _normalize_mention(value: str) -> str:
    replaced = value.replace("_", " ")
    return " ".join(unicodedata.normalize("NFKC", replaced).casefold().split())


def _objects(value: object, *, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{field} must be a list of objects")
    return [dict(item) for item in value]


def _mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return dict(value)


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _partition_summary(counter: Mapping[str, Counter[str]]) -> dict[str, dict[str, int]]:
    return {
        partition: dict(sorted(values.items()))
        for partition, values in sorted(counter.items())
    }


def _anchor_index(anchor_statistics: dict[str, Any]) -> dict[str, set[str]]:
    statistics = _objects(anchor_statistics.get("statistics"), field="anchor.statistics")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    result: dict[str, set[str]] = defaultdict(set)
    for row in statistics:
        mention = str(row.get("mention", ""))
        if not mention:
            raise ValueError("anchor statistic lacks mention")
        grouped[mention].append(row)
        entity_id = row.get("target_entity_id")
        if isinstance(entity_id, str) and entity_id:
            result[mention].add(entity_id)
    for mention, rows in grouped.items():
        probabilities = [float(row["probability"]) for row in rows]
        if not math.isclose(sum(probabilities), 1.0, rel_tol=0.0, abs_tol=1e-10):
            raise ValueError(f"anchor probabilities do not sum to one: {mention}")
        occurrences = sum(int(row["occurrence_count"]) for row in rows)
        if any(int(row["total_mention_occurrences"]) != occurrences for row in rows):
            raise ValueError(f"anchor occurrence total is inconsistent: {mention}")
        if any(int(row["ambiguity_count"]) != len(rows) for row in rows):
            raise ValueError(f"anchor ambiguity count is inconsistent: {mention}")
    return dict(result)


def _entity_metrics(
    hard_negatives: dict[str, Any],
    anchor_statistics: dict[str, Any],
    *,
    available_anchor_tiers: Sequence[str],
) -> tuple[dict[str, Any], set[tuple[str, str, str]]]:
    cases = _objects(hard_negatives.get("cases"), field="entity.cases")
    anchor_ids = _anchor_index(anchor_statistics)
    anchor_rows = _objects(anchor_statistics.get("statistics"), field="anchor.statistics")
    case_partitions: dict[str, set[str]] = defaultdict(set)
    partition_metrics: dict[str, Counter[str]] = defaultdict(Counter)
    tier_metrics: dict[str, Counter[str]] = defaultdict(Counter)
    taxonomy: Counter[str] = Counter()
    replica_keys: set[tuple[str, str, str]] = set()
    current_complete_cases: set[str] = set()
    expanded_complete_cases: set[str] = set()
    current_complete_cases_by_partition: dict[str, set[str]] = defaultdict(set)
    expanded_complete_cases_by_partition: dict[str, set[str]] = defaultdict(set)
    explicit_alignment_records = 0
    mention_records = 0
    development_cases: set[str] = set()
    explicitly_aligned_development_cases: set[str] = set()
    normalized_surfaces: set[str] = set()

    for case in cases:
        case_id = str(case.get("case_id", ""))
        partition = str(case.get("partition", ""))
        if not case_id or partition not in TRAINING_PARTITIONS:
            raise ValueError(f"entity case entered protected or unknown partition: {case_id}")
        case_partitions[case_id].add(partition)
        if partition == "development":
            development_cases.add(case_id)
        required = {str(item) for item in case.get("correct_entity_ids", ())}
        if not required:
            raise ValueError(f"entity case lacks required canonical IDs: {case_id}")
        case_explicit_alignment = False
        replicas = _objects(case.get("replicas"), field=f"entity.case[{case_id}].replicas")
        for replica in replicas:
            tier = str(replica.get("corpus_tier", ""))
            if tier not in REQUIRED_TIERS:
                raise ValueError(f"entity replica has unsupported tier: {(case_id, tier)}")
            if not bool(replica.get("training_eligible")):
                raise ValueError(
                    f"non-training entity replica entered readiness: {(case_id, tier)}"
                )
            key = (case_id, tier, partition)
            if key in replica_keys:
                raise ValueError(f"duplicate entity replica: {key}")
            replica_keys.add(key)
            mentions = _objects(
                replica.get("mentions"), field=f"entity.replica[{case_id},{tier}].mentions"
            )
            candidate_ids: set[str] = set()
            expanded_ids: set[str] = set()
            for mention in mentions:
                mention_records += 1
                surface = _normalize_mention(str(mention.get("surface", "")))
                if surface:
                    normalized_surfaces.add(surface)
                    expanded_ids.update(anchor_ids.get(surface, ()))
                candidates = _objects(
                    mention.get("candidates"),
                    field=f"entity.mention[{case_id},{tier},{surface}].candidates",
                )
                candidate_ids.update(str(item["entity_id"]) for item in candidates)
                aligned = mention.get("correct_entity_per_mention")
                if isinstance(aligned, str) and aligned:
                    explicit_alignment_records += 1
                    case_explicit_alignment = True
            expanded_ids.update(candidate_ids)
            current_complete = required <= candidate_ids
            expanded_complete = required <= expanded_ids
            partition_metrics[partition]["replicas"] += 1
            partition_metrics[partition]["mention_missing"] += int(not mentions)
            partition_metrics[partition]["current_candidate_complete"] += int(
                current_complete
            )
            partition_metrics[partition]["anchor_expanded_candidate_complete"] += int(
                expanded_complete
            )
            tier_metrics[tier]["replicas"] += 1
            tier_metrics[tier]["current_candidate_complete"] += int(current_complete)
            tier_metrics[tier]["anchor_expanded_candidate_complete"] += int(
                expanded_complete
            )
            if partition == "tuning":
                tier_metrics[tier]["tuning_replicas"] += 1
                tier_metrics[tier]["tuning_anchor_expanded_candidate_complete"] += int(
                    expanded_complete
                )
            taxonomy[str(replica.get("failure_class", "UNKNOWN"))] += 1
            if current_complete:
                current_complete_cases.add(case_id)
                current_complete_cases_by_partition[partition].add(case_id)
            if expanded_complete:
                expanded_complete_cases.add(case_id)
                expanded_complete_cases_by_partition[partition].add(case_id)
        if partition == "development" and case_explicit_alignment:
            explicitly_aligned_development_cases.add(case_id)

    leaking = sorted(case_id for case_id, values in case_partitions.items() if len(values) != 1)
    if leaking:
        raise ValueError(f"entity tier replicas cross partitions: {leaking[:5]}")
    unavailable = _mapping(
        hard_negatives.get("unavailable_fields", {}), field="entity.unavailable_fields"
    )
    protected = set(str(item) for item in hard_negatives.get("sealed_partitions_excluded", ()))
    if not {"evaluation", "final_held"} <= protected:
        raise ValueError("entity artifact does not declare protected partitions excluded")

    tuning = partition_metrics["tuning"]
    tuning_complete_rate = _rate(
        tuning["anchor_expanded_candidate_complete"], tuning["replicas"]
    )
    tuning_tier_rates = {
        tier: _rate(
            tier_metrics[tier]["tuning_anchor_expanded_candidate_complete"],
            tier_metrics[tier]["tuning_replicas"],
        )
        for tier in REQUIRED_TIERS
    }
    label_coverage = _rate(explicit_alignment_records, mention_records)
    pre_cap_available = not {
        "candidate_pool_before_top8",
        "candidate_cap_failure",
        "correct_entity_outside_top_k",
    }.intersection(unavailable)
    tier_coverage_complete = set(available_anchor_tiers) == set(REQUIRED_TIERS)
    candidate_recall_sufficient = (
        tuning_complete_rate >= MINIMUM_TUNING_CANDIDATE_COMPLETE_RECALL
        and all(
            tuning_tier_rates[tier] >= MINIMUM_TUNING_CANDIDATE_COMPLETE_RECALL
            for tier in REQUIRED_TIERS
        )
    )
    explicit_alignment_complete = mention_records > 0 and label_coverage == 1.0

    metrics: dict[str, Any] = {
        "replica_count": len(replica_keys),
        "unique_case_count": len(case_partitions),
        "partition_metrics": _partition_summary(partition_metrics),
        "tier_metrics": _partition_summary(tier_metrics),
        "taxonomy": dict(sorted(taxonomy.items())),
        "mention_records": mention_records,
        "explicit_mention_alignment_records": explicit_alignment_records,
        "explicit_mention_alignment_coverage": label_coverage,
        "development_unique_cases": len(development_cases),
        "explicitly_aligned_development_unique_cases": len(
            explicitly_aligned_development_cases
        ),
        "current_candidate_complete_replicas": sum(
            values["current_candidate_complete"] for values in partition_metrics.values()
        ),
        "anchor_expanded_candidate_complete_replicas": sum(
            values["anchor_expanded_candidate_complete"]
            for values in partition_metrics.values()
        ),
        "current_candidate_complete_unique_cases": len(current_complete_cases),
        "anchor_expanded_candidate_complete_unique_cases": len(expanded_complete_cases),
        "current_candidate_complete_unique_cases_by_partition": {
            partition: len(values)
            for partition, values in sorted(current_complete_cases_by_partition.items())
        },
        "anchor_expanded_candidate_complete_unique_cases_by_partition": {
            partition: len(values)
            for partition, values in sorted(expanded_complete_cases_by_partition.items())
        },
        "anchor_requested_mention_count": int(
            anchor_statistics.get("requested_mention_count", 0)
        ),
        "anchor_covered_mention_count": int(anchor_statistics.get("covered_mention_count", 0)),
        "hard_negative_normalized_surface_count": len(normalized_surfaces),
        "anchor_statistic_count": len(anchor_rows),
        "anchor_resolved_entity_statistic_count": sum(
            isinstance(row.get("target_entity_id"), str) and bool(row["target_entity_id"])
            for row in anchor_rows
        ),
        "anchor_unresolved_entity_statistic_count": sum(
            not isinstance(row.get("target_entity_id"), str) or not row["target_entity_id"]
            for row in anchor_rows
        ),
        "available_anchor_tiers": sorted(set(available_anchor_tiers)),
        "required_anchor_tiers": list(REQUIRED_TIERS),
        "minimum_tuning_candidate_complete_recall": (
            MINIMUM_TUNING_CANDIDATE_COMPLETE_RECALL
        ),
        "tuning_anchor_expanded_candidate_complete_recall": tuning_complete_rate,
        "tuning_anchor_expanded_candidate_complete_recall_by_tier": tuning_tier_rates,
        "gates": {
            "explicit_mention_alignment_complete": explicit_alignment_complete,
            "pre_cap_candidate_state_available": pre_cap_available,
            "all_tier_anchor_coverage_available": tier_coverage_complete,
            "candidate_recall_sufficient": candidate_recall_sufficient,
        },
    }
    return metrics, replica_keys


def _target_present(targets: Sequence[str], values: Sequence[str]) -> bool:
    present = set(values)
    return bool(targets) and all(target in present for target in targets)


def _recompute_value_causal_stage(replica: dict[str, Any]) -> str:
    """Classify one residual from source availability before downstream stages.

    Replay candidates are already exact source-bound hypotheses. A quotation
    surface copied from a selected chunk is likewise an available span even
    when both typed compiler and runtime extractors miss it. Other raw text is
    not called an address until the typed runtime has produced an exact match.
    """

    targets = [str(item) for item in replica.get("target_atomic_values", ())]
    capture = _mapping(replica.get("pack_capture"), field="value.pack_capture")
    chunks = _objects(capture.get("selected_chunks"), field="value.pack_capture.selected_chunks")
    complete = [item for item in chunks if not bool(item.get("missing_from_pack"))]
    raw_texts = [str(item.get("complete_chunk_text", "")) for item in complete]
    exact_replay_values = [str(item) for item in replica.get("runtime_candidate_values", ())]
    replay_source_bound = _target_present(targets, exact_replay_values)
    quotation_copyable = (
        str(replica.get("answer_shape")) == "quotation"
        and bool(targets)
        and all(any(target in text for text in raw_texts) for target in targets)
    )
    compiler_values: list[str] = []
    pre_region: list[str] = []
    top_regions: list[str] = []
    pre_dedup: list[str] = []
    post_dedup: list[str] = []
    pre_cap: list[str] = []
    post_cap: list[str] = []
    binding_failures = 0
    for document in _objects(
        capture.get("compiler_documents"), field="value.pack_capture.compiler_documents"
    ):
        if bool(document.get("missing_from_pack")):
            continue
        boundary = _mapping(document.get("boundary"), field="value.compiler_boundary")
        compiler_values.extend(
            str(item.get("object_value", ""))
            for item in _objects(
                boundary.get("all_typed_matches_before_type_caps"),
                field="value.compiler_boundary.all_typed_matches_before_type_caps",
            )
        )
    for chunk in complete:
        runtime = _mapping(chunk.get("runtime_boundary"), field="value.runtime_boundary")
        matches = _objects(
            runtime.get("all_matches_before_region_pruning"),
            field="value.runtime_boundary.all_matches_before_region_pruning",
        )
        pre_region.extend(str(item.get("surface", "")) for item in matches)
        binding_failures += sum(not bool(item.get("document_binding_success")) for item in matches)
        top_regions.extend(
            str(item.get("surface", ""))
            for item in _objects(
                runtime.get("top8_matches_before_deduplication"),
                field="value.runtime_boundary.top8_matches_before_deduplication",
            )
        )
        pre_dedup.extend(str(item) for item in runtime.get("pre_dedup_values", ()))
        post_dedup.extend(str(item) for item in runtime.get("post_dedup_values", ()))
        pre_cap.extend(str(item) for item in runtime.get("pre_cap_values", ()))
        post_cap.extend(str(item) for item in runtime.get("post_cap_values", ()))
    runtime_source_bound = _target_present(targets, pre_region)
    if not (replay_source_bound or quotation_copyable or runtime_source_bound):
        return "SELECTED_SOURCE_CHUNK_ABSENCE"
    if replay_source_bound:
        return "SEMANTIC_SUBJECT_RELATION_BINDING"
    compiler_present = _target_present(targets, compiler_values)
    runtime_present = _target_present(targets, pre_region)
    if not compiler_present and not runtime_present:
        return "DUAL_COMPILER_RUNTIME_QUOTATION_EXTRACTION"
    if not compiler_present:
        return "COMPILER_EXTRACTION"
    if not _target_present(targets, pre_region):
        return "RUNTIME_EXTRACTION"
    if not _target_present(targets, top_regions):
        return "REGION_PRUNING"
    if not _target_present(targets, pre_dedup):
        return "PRE_DEDUP_PIPELINE_DROP"
    if not _target_present(targets, post_dedup):
        return "DEDUPLICATION"
    if not _target_present(targets, pre_cap):
        return "PRE_CAP_PIPELINE_DROP"
    if not _target_present(targets, post_cap):
        return "CAP"
    if binding_failures:
        return "REBINDING"
    return "SEMANTIC_SUBJECT_RELATION_BINDING"


def _value_metrics(
    value_diagnostic: dict[str, Any],
) -> tuple[dict[str, Any], set[tuple[str, str, str]]]:
    scope = _mapping(value_diagnostic.get("scope"), field="value.scope")
    if bool(scope.get("evaluation_and_final_held_used")):
        raise ValueError("value diagnostic consumed protected labels")
    partitions = {str(item) for item in scope.get("partitions", ())}
    if partitions != TRAINING_PARTITIONS:
        raise ValueError(f"value diagnostic partition scope changed: {sorted(partitions)}")
    replicas = _objects(value_diagnostic.get("replicas"), field="value.replicas")
    partition_counts: Counter[str] = Counter()
    tier_counts: Counter[str] = Counter()
    old_classification: Counter[str] = Counter()
    causal_classification: Counter[str] = Counter()
    case_partitions: dict[str, set[str]] = defaultdict(set)
    keys: set[tuple[str, str, str]] = set()
    pack_capture_count = 0
    selected_chunk_count = 0
    selected_chunk_missing = 0
    compiler_document_missing = 0
    document_binding_failures = 0

    for replica in replicas:
        case_id = str(replica.get("case_id", ""))
        partition = str(replica.get("partition", ""))
        tier = str(replica.get("corpus_tier", ""))
        if not case_id or partition not in TRAINING_PARTITIONS:
            raise ValueError(f"value case entered protected or unknown partition: {case_id}")
        if tier not in REQUIRED_TIERS:
            raise ValueError(f"value replica has unsupported tier: {(case_id, tier)}")
        key = (case_id, tier, partition)
        if key in keys:
            raise ValueError(f"duplicate value replica: {key}")
        keys.add(key)
        case_partitions[case_id].add(partition)
        partition_counts[partition] += 1
        tier_counts[tier] += 1
        classification = str(replica.get("classification", "UNKNOWN"))
        old_classification[classification] += 1
        capture = replica.get("pack_capture")
        if isinstance(capture, dict):
            pack_capture_count += 1
            chunks = _objects(capture.get("selected_chunks"), field="value.selected_chunks")
            selected_chunk_count += len(chunks)
            selected_chunk_missing += sum(bool(item.get("missing_from_pack")) for item in chunks)
            compiler = _objects(
                capture.get("compiler_documents"), field="value.compiler_documents"
            )
            compiler_document_missing += sum(
                bool(item.get("missing_from_pack")) for item in compiler
            )
            for chunk in chunks:
                if bool(chunk.get("missing_from_pack")):
                    continue
                runtime = _mapping(chunk.get("runtime_boundary"), field="value.runtime_boundary")
                matches = _objects(
                    runtime.get("all_matches_before_region_pruning"),
                    field="value.runtime_boundary.all_matches_before_region_pruning",
                )
                document_binding_failures += sum(
                    not bool(item.get("document_binding_success")) for item in matches
                )
        causal_classification[_recompute_value_causal_stage(replica)] += 1

    leaking = sorted(case_id for case_id, values in case_partitions.items() if len(values) != 1)
    if leaking:
        raise ValueError(f"value tier replicas cross partitions: {leaking[:5]}")
    neural = _mapping(
        value_diagnostic.get("neural_value_specialist"), field="value.neural_value_specialist"
    )
    metrics: dict[str, Any] = {
        "replica_count": len(replicas),
        "unique_case_count": len(case_partitions),
        "partition_counts": dict(sorted(partition_counts.items())),
        "tier_counts": dict(sorted(tier_counts.items())),
        "pack_capture_count": pack_capture_count,
        "selected_chunk_count": selected_chunk_count,
        "selected_chunk_missing_from_pack": selected_chunk_missing,
        "compiler_document_missing_from_pack": compiler_document_missing,
        "document_binding_failures": document_binding_failures,
        "historical_classification_counts": dict(sorted(old_classification.items())),
        "trace_recomputed_causal_decomposition_counts": {
            name: causal_classification[name]
            for name in (
                "SELECTED_SOURCE_CHUNK_ABSENCE",
                "DUAL_COMPILER_RUNTIME_QUOTATION_EXTRACTION",
                "SEMANTIC_SUBJECT_RELATION_BINDING",
                "COMPILER_EXTRACTION",
                "RUNTIME_EXTRACTION",
                "REGION_PRUNING",
                "PRE_DEDUP_PIPELINE_DROP",
                "DEDUPLICATION",
                "PRE_CAP_PIPELINE_DROP",
                "CAP",
                "REBINDING",
            )
        },
        "previously_missing_boundary_fields_complete": (
            pack_capture_count == len(replicas)
            and selected_chunk_missing == 0
            and compiler_document_missing == 0
        ),
        "neural_value_specialist_decision": neural.get("decision"),
        "direct_unique_development_spans": int(
            neural.get("direct_unique_development_spans", 0)
        ),
    }
    return metrics, keys


def qualify_specialist_readiness(
    hard_negatives: dict[str, Any],
    anchor_statistics: dict[str, Any],
    value_diagnostic: dict[str, Any],
    reachability: dict[str, Any],
    *,
    available_anchor_tiers: Sequence[str] = ("10k",),
    source_identity: Mapping[str, str] | None = None,
    integrity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Measure prerequisites and return a deterministic no-training decision."""

    entity, entity_keys = _entity_metrics(
        hard_negatives,
        anchor_statistics,
        available_anchor_tiers=available_anchor_tiers,
    )
    value, value_keys = _value_metrics(value_diagnostic)
    rows = _objects(reachability.get("per_case"), field="reachability.per_case")
    reach_entity_keys = {
        (str(row["case_id"]), str(row["corpus_tier"]), str(row["partition"]))
        for row in rows
        if row.get("old_failure_class") == "ENTITY_BINDING_WRONG"
        and not bool(row.get("recovered"))
    }
    reach_value_keys = {
        (str(row["case_id"]), str(row["corpus_tier"]), str(row["partition"]))
        for row in rows
        if row.get("old_failure_class") == "VALUE_NOT_ENUMERATED"
        and not bool(row.get("recovered"))
    }
    if entity_keys != reach_entity_keys:
        raise ValueError("entity handoff keys do not match the certified residual")
    if value_keys != reach_value_keys:
        raise ValueError("value handoff keys do not match the certified residual")

    training_failures = int(reachability.get("training_failures", 0))
    current_reachable = int(reachability.get("new_reachable", 0))
    if training_failures <= 0 or current_reachable < 0:
        raise ValueError("reachability report lacks valid training counts")
    strict_target = math.floor(STRICT_REACHABILITY_THRESHOLD * training_failures) + 1
    candidate_complete = int(entity["anchor_expanded_candidate_complete_replicas"])
    value_residual = int(value["replica_count"])
    entity_only_ceiling = min(training_failures, current_reachable + candidate_complete)
    combined_ceiling = min(training_failures, entity_only_ceiling + value_residual)
    required_value_recoveries = max(0, strict_target - entity_only_ceiling)

    entity_gates = _mapping(entity["gates"], field="entity.gates")
    readiness_gates = {
        "artifact_integrity_verified": bool(integrity and integrity.get("all_verified")),
        "protected_partitions_excluded": True,
        "explicit_mention_alignment_complete": bool(
            entity_gates["explicit_mention_alignment_complete"]
        ),
        "pre_cap_candidate_state_available": bool(
            entity_gates["pre_cap_candidate_state_available"]
        ),
        "all_tier_anchor_coverage_available": bool(
            entity_gates["all_tier_anchor_coverage_available"]
        ),
        "candidate_recall_sufficient": bool(entity_gates["candidate_recall_sufficient"]),
    }
    block_reasons = [name for name, passed in readiness_gates.items() if not passed]
    sweep_ready = not block_reasons
    decision = (
        "CONTEXTUAL_ENTITY_SUCCESSIVE_HALVING_READY"
        if sweep_ready
        else "BLOCK_CONTEXTUAL_ENTITY_SUCCESSIVE_HALVING"
    )

    return {
        "schema_version": "aethercore.specialist-readiness.v1",
        "status": "COMPLETE",
        "decision": decision,
        "source_identity": dict(sorted((source_identity or {}).items())),
        "split_policy": {
            "fit_partitions": ["development"],
            "calibration_and_selection_partitions": ["tuning"],
            "protected_partitions_not_consumed": ["evaluation", "final_held"],
            "group_key": "case_id",
            "tier_replicas_never_cross_partitions": True,
            "development_and_tuning_never_fit_jointly": True,
        },
        "integrity": dict(integrity or {}),
        "entity_readiness": entity,
        "value_readiness": value,
        "strict_60_feasibility": {
            "training_failure_count": training_failures,
            "current_reachable": current_reachable,
            "current_reachability": _rate(current_reachable, training_failures),
            "strict_threshold_fraction": STRICT_REACHABILITY_THRESHOLD,
            "minimum_count_strictly_exceeding_threshold": strict_target,
            "additional_certified_recoveries_required": max(
                0, strict_target - current_reachable
            ),
            "anchor_expanded_candidate_complete_entity_replicas": candidate_complete,
            "loose_entity_only_oracle_ceiling_count": entity_only_ceiling,
            "loose_entity_only_oracle_ceiling_fraction": _rate(
                entity_only_ceiling, training_failures
            ),
            "remaining_value_residual_replicas": value_residual,
            "minimum_value_recoveries_if_every_candidate_complete_entity_recovers": (
                required_value_recoveries
            ),
            "loose_combined_oracle_ceiling_count": combined_ceiling,
            "loose_combined_oracle_ceiling_fraction": _rate(
                combined_ceiling, training_failures
            ),
            "loose_combined_ceiling_can_exceed_60": combined_ceiling >= strict_target,
            "warning": (
                "Candidate completeness is only an availability ceiling; it is not semantic "
                "selection, value availability, or controller reachability."
            ),
        },
        "readiness_gates": readiness_gates,
        "successive_halving": {
            "requested_parameter_counts": list(REQUESTED_ENTITY_PARAMETER_COUNTS),
            "status": "NOT_STARTED" if not sweep_ready else "READY_NOT_STARTED",
            "started": False,
            "block_reasons": block_reasons,
            "protocol_after_unblock": [
                "fit every requested size on development only with case-group weighting",
                "promote configurations by frozen tuning metrics only",
                "calibrate confidence and abstention thresholds on tuning only",
                "freeze model, fusion, gate, and registry identities before held-out use",
            ],
        },
        "integration_dependencies": [
            "explicit mention-to-canonical-entity labels and gold mention spans",
            "pre-cap candidate generation records with channel and rejection provenance",
            "occurrence statistics covering 10k, 25k, and 397k source tiers",
            "gold-blind semantic-address constructor preserving exact IDs and uncertainty",
            "controller-state regeneration that separates candidate support from selected binding",
            "full affected-cohort reachability rerun rather than carrying forward changed states",
        ],
        "protected_partition_labels_consumed": False,
        "contextual_model_trained": False,
        "fusion_or_depth_architecture_changed": False,
    }
