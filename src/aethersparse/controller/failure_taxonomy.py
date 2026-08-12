"""Mission 5 controller-failure taxonomy and deterministic aggregation."""

from __future__ import annotations

from collections import Counter, defaultdict
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class ControllerFailureClass(StrEnum):
    CLAIM_MISSING = "CLAIM_MISSING"
    CLAIM_MANGLED = "CLAIM_MANGLED"
    FRAME_WRONG = "FRAME_WRONG"
    ENTITY_BINDING_WRONG = "ENTITY_BINDING_WRONG"
    VALUE_NOT_ENUMERATED = "VALUE_NOT_ENUMERATED"
    VALUE_MISRANKED = "VALUE_MISRANKED"
    SUBJECT_BINDING_WRONG = "SUBJECT_BINDING_WRONG"
    RELATION_BINDING_WRONG = "RELATION_BINDING_WRONG"
    TEMPORAL_SCOPE_WRONG = "TEMPORAL_SCOPE_WRONG"
    ATTRIBUTION_WRONG = "ATTRIBUTION_WRONG"
    COMPOSITION_OPERATOR_MISSING = "COMPOSITION_OPERATOR_MISSING"
    DISPOSITION_WRONG = "DISPOSITION_WRONG"
    REALIZATION_ONLY = "REALIZATION_ONLY"
    CURRENT_TOOLS_REACHABLE = "CURRENT_TOOLS_REACHABLE"


LEGACY_CLASS_MAP: dict[str, ControllerFailureClass] = {
    "VALUE_NOT_ENUMERATED": ControllerFailureClass.VALUE_NOT_ENUMERATED,
    "VALUE_MISRANKED": ControllerFailureClass.VALUE_MISRANKED,
    "SUBJECT_BINDING_WRONG": ControllerFailureClass.SUBJECT_BINDING_WRONG,
    "RELATION_BINDING_WRONG": ControllerFailureClass.RELATION_BINDING_WRONG,
    "TEMPORAL_SCOPE_WRONG": ControllerFailureClass.TEMPORAL_SCOPE_WRONG,
    "ATTRIBUTION_BINDING_WRONG": ControllerFailureClass.ATTRIBUTION_WRONG,
    "COMPOSITION_OPERATOR_MISSING": ControllerFailureClass.COMPOSITION_OPERATOR_MISSING,
    "DISPOSITION_WRONG": ControllerFailureClass.DISPOSITION_WRONG,
    "REALIZATION_ONLY": ControllerFailureClass.REALIZATION_ONLY,
    "CANONICALIZATION_ONLY": ControllerFailureClass.REALIZATION_ONLY,
    "WRONG_TYPE": ControllerFailureClass.FRAME_WRONG,
}


class FailureRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    corpus_tier: str
    partition: str
    answer_shape: str
    categories: tuple[str, ...]
    source_mode: str
    failure_class: ControllerFailureClass
    policy_recoverable: bool


def normalize_failure_class(label: str, *, reachable: bool = False) -> ControllerFailureClass:
    if reachable:
        return ControllerFailureClass.CURRENT_TOOLS_REACHABLE
    if label in LEGACY_CLASS_MAP:
        return LEGACY_CLASS_MAP[label]
    try:
        return ControllerFailureClass(label)
    except ValueError:
        return ControllerFailureClass.CLAIM_MANGLED


def is_policy_recoverable(label: ControllerFailureClass) -> bool:
    return label not in {
        ControllerFailureClass.CLAIM_MISSING,
        ControllerFailureClass.CLAIM_MANGLED,
        ControllerFailureClass.FRAME_WRONG,
        ControllerFailureClass.ENTITY_BINDING_WRONG,
        ControllerFailureClass.COMPOSITION_OPERATOR_MISSING,
    }


def failure_record_from_legacy(
    payload: dict[str, Any], *, corpus_tier: str, reachable: bool = False
) -> FailureRecord:
    label = normalize_failure_class(
        str(payload.get("taxonomy", "CLAIM_MANGLED")), reachable=reachable
    )
    sources = int(payload.get("sources", 1))
    return FailureRecord(
        case_id=str(payload.get("case_id", "unknown")),
        corpus_tier=corpus_tier,
        partition=str(payload.get("partition", "unknown")),
        answer_shape=str(payload.get("shape", "unknown")),
        categories=tuple(str(item) for item in payload.get("categories", ())),
        source_mode="single-source" if sources == 1 else "composition",
        failure_class=label,
        policy_recoverable=is_policy_recoverable(label),
    )


def aggregate_failures(records: tuple[FailureRecord, ...]) -> dict[str, object]:
    taxonomy: Counter[str] = Counter()
    by_tier: dict[str, Counter[str]] = defaultdict(Counter)
    by_shape: dict[str, Counter[str]] = defaultdict(Counter)
    by_category: dict[str, Counter[str]] = defaultdict(Counter)
    by_source: dict[str, Counter[str]] = defaultdict(Counter)
    for record in records:
        label = record.failure_class.value
        taxonomy[label] += 1
        by_tier[record.corpus_tier][label] += 1
        by_shape[record.answer_shape][label] += 1
        by_source[record.source_mode][label] += 1
        for category in record.categories:
            by_category[category][label] += 1

    def project(group: dict[str, Counter[str]]) -> dict[str, dict[str, int]]:
        return {key: dict(sorted(value.items())) for key, value in sorted(group.items())}

    return {
        "record_count": len(records),
        "taxonomy": dict(sorted(taxonomy.items())),
        "policy_recoverable_count": sum(record.policy_recoverable for record in records),
        "compiler_or_knowledge_defect_count": sum(
            not record.policy_recoverable for record in records
        ),
        "by_tier": project(by_tier),
        "by_answer_shape": project(by_shape),
        "by_category": project(by_category),
        "by_source_mode": project(by_source),
    }
