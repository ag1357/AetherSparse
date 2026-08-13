"""Split-safe matched qualification for v11 specialist configurations."""

from __future__ import annotations

import math
from collections import Counter
from enum import StrEnum

from pydantic import Field, model_validator

from aethersparse.controller.models import FrozenModel


class DatasetUse(StrEnum):
    FIT = "fit"
    CALIBRATE_SELECT = "calibrate_select"
    FROZEN_HOLDOUT = "frozen_holdout"


class CaseQualification(FrozenModel):
    case_id: str
    partition: str
    corpus_tier: str
    training_eligible: bool
    configuration_id: str
    architecture_sha256: str
    exact_correct: bool
    canonical_correct: bool
    entity_top1_correct: bool | None = None
    entity_topk_recalled: bool | None = None
    value_enumerated: bool | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    selected_claim_count: int = Field(ge=0)
    unsupported_claim_count: int = Field(ge=0)
    active_experts: int = Field(ge=0)
    active_parameters: int = Field(ge=0)
    cognitive_cycles: int = Field(ge=0, le=64)
    active_macs: int = Field(ge=0)
    projected_p4_latency_ms: float | None = Field(default=None, ge=0.0)
    route_signature: str

    @model_validator(mode="after")
    def provenance_count_is_bounded(self) -> CaseQualification:
        if self.unsupported_claim_count > self.selected_claim_count:
            raise ValueError("unsupported claims cannot exceed selected claims")
        return self

    @property
    def provenance_correct(self) -> bool:
        return self.unsupported_claim_count == 0


class RiskCoveragePoint(FrozenModel):
    threshold: float = Field(ge=0.0, le=1.0)
    coverage: float = Field(ge=0.0, le=1.0)
    selective_risk: float = Field(ge=0.0, le=1.0)


class QualificationSummary(FrozenModel):
    configuration_id: str
    architecture_sha256: str
    dataset_use: DatasetUse
    case_count: int = Field(ge=1)
    partition_counts: dict[str, int]
    tier_counts: dict[str, int]
    exact_accuracy: float = Field(ge=0.0, le=1.0)
    canonical_accuracy: float = Field(ge=0.0, le=1.0)
    provenance_perfect_case_fraction: float = Field(ge=0.0, le=1.0)
    unsupported_claim_count: int = Field(ge=0)
    entity_top1_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    entity_topk_recall: float | None = Field(default=None, ge=0.0, le=1.0)
    value_enumeration_recall: float | None = Field(default=None, ge=0.0, le=1.0)
    brier_score: float = Field(ge=0.0, le=1.0)
    negative_log_likelihood: float = Field(ge=0.0)
    expected_calibration_error: float = Field(ge=0.0, le=1.0)
    risk_coverage: tuple[RiskCoveragePoint, ...]
    mean_active_experts: float = Field(ge=0.0)
    p95_active_experts: float = Field(ge=0.0)
    mean_active_parameters: float = Field(ge=0.0)
    p95_active_parameters: float = Field(ge=0.0)
    mean_cognitive_cycles: float = Field(ge=0.0)
    p95_cognitive_cycles: float = Field(ge=0.0)
    mean_active_macs: float = Field(ge=0.0)
    p95_active_macs: float = Field(ge=0.0)
    mean_projected_p4_latency_ms: float | None = Field(default=None, ge=0.0)
    route_counts: dict[str, int]


class AblationDelta(FrozenModel):
    baseline_configuration_id: str
    candidate_configuration_id: str
    case_count: int
    canonical_accuracy_delta: float
    exact_accuracy_delta: float
    provenance_perfect_case_fraction_delta: float
    expected_calibration_error_delta: float
    mean_active_parameters_delta: float
    mean_active_macs_delta: float
    mean_cognitive_cycles_delta: float


def _validate_use(
    records: tuple[CaseQualification, ...],
    dataset_use: DatasetUse,
    *,
    architecture_frozen: bool,
) -> None:
    allowed = {
        DatasetUse.FIT: {"development"},
        DatasetUse.CALIBRATE_SELECT: {"tuning"},
        DatasetUse.FROZEN_HOLDOUT: {"evaluation", "final_held"},
    }[dataset_use]
    invalid = sorted({record.partition for record in records if record.partition not in allowed})
    if invalid:
        raise ValueError(f"{dataset_use} may not consume partitions: {invalid}")
    if dataset_use == DatasetUse.FROZEN_HOLDOUT and not architecture_frozen:
        raise ValueError("held-out qualification requires a frozen architecture")
    for record in records:
        if (
            dataset_use in {DatasetUse.FIT, DatasetUse.CALIBRATE_SELECT}
            and not record.training_eligible
        ):
            raise ValueError("fit/calibration records must be training eligible")
        if dataset_use == DatasetUse.FROZEN_HOLDOUT and record.training_eligible:
            raise ValueError("held-out records must be non-training")
    partitions_by_case: dict[str, set[str]] = {}
    for record in records:
        partitions_by_case.setdefault(record.case_id, set()).add(record.partition)
    leaking = sorted(case_id for case_id, values in partitions_by_case.items() if len(values) != 1)
    if leaking:
        raise ValueError(f"tier replicas cross partitions: {leaking[:5]}")


def _mean_boolean(records: list[bool]) -> float | None:
    return sum(records) / len(records) if records else None


def _mean(values: tuple[float, ...]) -> float:
    return sum(values) / len(values)


def _percentile(values: tuple[int, ...], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _calibration(records: tuple[CaseQualification, ...]) -> tuple[float, float, float]:
    epsilon = 1e-12
    outcomes = tuple(float(record.canonical_correct) for record in records)
    confidences = tuple(record.confidence for record in records)
    brier = _mean(
        tuple(
            (confidence - outcome) ** 2
            for confidence, outcome in zip(confidences, outcomes, strict=True)
        )
    )
    nll = -_mean(
        tuple(
            outcome * math.log(max(epsilon, confidence))
            + (1.0 - outcome) * math.log(max(epsilon, 1.0 - confidence))
            for confidence, outcome in zip(confidences, outcomes, strict=True)
        )
    )
    ece = 0.0
    for index in range(10):
        lower = index / 10.0
        upper = (index + 1) / 10.0
        members = [
            item
            for item in zip(confidences, outcomes, strict=True)
            if lower <= item[0] <= upper and (index == 9 or item[0] < upper)
        ]
        if members:
            bin_confidence = sum(item[0] for item in members) / len(members)
            bin_accuracy = sum(item[1] for item in members) / len(members)
            ece += len(members) / len(records) * abs(bin_confidence - bin_accuracy)
    return brier, nll, ece


def _risk_coverage(records: tuple[CaseQualification, ...]) -> tuple[RiskCoveragePoint, ...]:
    points: list[RiskCoveragePoint] = []
    for threshold in (0.0, 0.5, 0.75, 0.9, 0.95, 0.99):
        retained = [record for record in records if record.confidence >= threshold]
        risk = (
            1.0 - sum(record.canonical_correct for record in retained) / len(retained)
            if retained
            else 0.0
        )
        points.append(
            RiskCoveragePoint(
                threshold=threshold,
                coverage=len(retained) / len(records),
                selective_risk=risk,
            )
        )
    return tuple(points)


def evaluate_configuration(
    records: tuple[CaseQualification, ...],
    dataset_use: DatasetUse,
    *,
    architecture_frozen: bool = False,
) -> QualificationSummary:
    if not records:
        raise ValueError("qualification requires at least one case")
    _validate_use(records, dataset_use, architecture_frozen=architecture_frozen)
    configuration_ids = {record.configuration_id for record in records}
    architecture_ids = {record.architecture_sha256 for record in records}
    if len(configuration_ids) != 1 or len(architecture_ids) != 1:
        raise ValueError("one qualification call must contain one frozen configuration")
    brier, nll, ece = _calibration(records)
    entity_top1 = [
        record.entity_top1_correct
        for record in records
        if record.entity_top1_correct is not None
    ]
    entity_topk = [
        record.entity_topk_recalled
        for record in records
        if record.entity_topk_recalled is not None
    ]
    values = [record.value_enumerated for record in records if record.value_enumerated is not None]
    latencies = tuple(
        record.projected_p4_latency_ms
        for record in records
        if record.projected_p4_latency_ms is not None
    )
    return QualificationSummary(
        configuration_id=next(iter(configuration_ids)),
        architecture_sha256=next(iter(architecture_ids)),
        dataset_use=dataset_use,
        case_count=len(records),
        partition_counts=dict(sorted(Counter(item.partition for item in records).items())),
        tier_counts=dict(sorted(Counter(item.corpus_tier for item in records).items())),
        exact_accuracy=sum(item.exact_correct for item in records) / len(records),
        canonical_accuracy=sum(item.canonical_correct for item in records) / len(records),
        provenance_perfect_case_fraction=sum(item.provenance_correct for item in records)
        / len(records),
        unsupported_claim_count=sum(item.unsupported_claim_count for item in records),
        entity_top1_accuracy=_mean_boolean(entity_top1),
        entity_topk_recall=_mean_boolean(entity_topk),
        value_enumeration_recall=_mean_boolean(values),
        brier_score=brier,
        negative_log_likelihood=nll,
        expected_calibration_error=ece,
        risk_coverage=_risk_coverage(records),
        mean_active_experts=_mean(tuple(float(item.active_experts) for item in records)),
        p95_active_experts=_percentile(tuple(item.active_experts for item in records), 0.95),
        mean_active_parameters=_mean(tuple(float(item.active_parameters) for item in records)),
        p95_active_parameters=_percentile(tuple(item.active_parameters for item in records), 0.95),
        mean_cognitive_cycles=_mean(tuple(float(item.cognitive_cycles) for item in records)),
        p95_cognitive_cycles=_percentile(tuple(item.cognitive_cycles for item in records), 0.95),
        mean_active_macs=_mean(tuple(float(item.active_macs) for item in records)),
        p95_active_macs=_percentile(tuple(item.active_macs for item in records), 0.95),
        mean_projected_p4_latency_ms=_mean(latencies) if latencies else None,
        route_counts=dict(sorted(Counter(item.route_signature for item in records).items())),
    )


def matched_ablation(
    baseline_records: tuple[CaseQualification, ...],
    candidate_records: tuple[CaseQualification, ...],
    dataset_use: DatasetUse,
    *,
    architecture_frozen: bool = False,
) -> AblationDelta:
    baseline_keys = {(item.case_id, item.corpus_tier) for item in baseline_records}
    candidate_keys = {(item.case_id, item.corpus_tier) for item in candidate_records}
    if baseline_keys != candidate_keys:
        raise ValueError("ablation configurations must contain identical case/tier keys")
    baseline = evaluate_configuration(
        baseline_records, dataset_use, architecture_frozen=architecture_frozen
    )
    candidate = evaluate_configuration(
        candidate_records, dataset_use, architecture_frozen=architecture_frozen
    )
    return AblationDelta(
        baseline_configuration_id=baseline.configuration_id,
        candidate_configuration_id=candidate.configuration_id,
        case_count=baseline.case_count,
        canonical_accuracy_delta=candidate.canonical_accuracy - baseline.canonical_accuracy,
        exact_accuracy_delta=candidate.exact_accuracy - baseline.exact_accuracy,
        provenance_perfect_case_fraction_delta=(
            candidate.provenance_perfect_case_fraction
            - baseline.provenance_perfect_case_fraction
        ),
        expected_calibration_error_delta=(
            candidate.expected_calibration_error - baseline.expected_calibration_error
        ),
        mean_active_parameters_delta=(
            candidate.mean_active_parameters - baseline.mean_active_parameters
        ),
        mean_active_macs_delta=candidate.mean_active_macs - baseline.mean_active_macs,
        mean_cognitive_cycles_delta=(
            candidate.mean_cognitive_cycles - baseline.mean_cognitive_cycles
        ),
    )
