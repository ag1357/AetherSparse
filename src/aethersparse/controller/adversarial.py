"""Held-out adversarial supplement for the exact realization verifier.

The learned score in this module can only veto an already verified answer.  It
cannot approve a failed deterministic check, create evidence, or change a
disposition.  That boundary lets qualification measure whether a tiny learned
sentinel catches anything the exact verifier misses without weakening the
fail-closed runtime.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import Field

from aethersparse.controller.models import ControllerResult, FrozenModel, RealizedAnswer
from aethersparse.controller.verification import adversarial_mutations, verify_realization


class AdversarialVerifierReport(FrozenModel):
    experiment_id: str = "AETHERSPARSE_V050_ADVERSARIAL_VERIFIER_R1"
    source_answer_count: int = Field(ge=0)
    train_example_count: int = Field(ge=0)
    evaluation_example_count: int = Field(ge=0)
    evaluation_mutation_count: int = Field(ge=0)
    deterministic_mutation_rejection_rate: float = Field(ge=0.0, le=1.0)
    learned_accuracy: float = Field(ge=0.0, le=1.0)
    learned_supported_precision: float = Field(ge=0.0, le=1.0)
    learned_mutation_recall: float = Field(ge=0.0, le=1.0)
    learned_false_accept_rate: float = Field(ge=0.0, le=1.0)
    incremental_mutations_rejected: int = Field(ge=0)
    model_bytes: int = Field(ge=0)
    learned_component_can_only_veto: bool = True
    retained_in_primary_runtime: bool
    decision: str


@dataclass(frozen=True)
class _Example:
    query_id: str
    supported: bool
    deterministic_passed: bool
    features: tuple[float, ...]


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _expected_text(result: ControllerResult) -> str:
    assert result.plan is not None
    planned = result.plan.planned_claims
    if result.plan.answer_shape.value == "comparison" and len(planned) == 2:
        return f"{planned[0].surface} {result.plan.comparison_operator} {planned[1].surface}."
    if result.plan.answer_shape.value == "list":
        return "; ".join(item.surface for item in planned)
    return planned[0].surface


def _features(result: ControllerResult, answer: RealizedAnswer) -> tuple[float, ...]:
    assert result.plan is not None
    report = verify_realization(result.frame, result.graph, result.plan, answer)
    findings = {finding.code: finding.passed for finding in report.findings}

    def all_prefixed(prefix: str) -> float:
        values = [passed for code, passed in findings.items() if code.startswith(prefix)]
        return float(bool(values) and all(values))

    expected = _expected_text(result)
    expected_length = max(1, len(expected))
    bound_characters = sum(len(binding.surface) for binding in answer.bindings)
    return (
        float(answer.text == expected),
        all_prefixed("SURFACE_OFFSET:"),
        all_prefixed("PLAN_BINDING:"),
        all_prefixed("CLAIM_SOURCE:"),
        all_prefixed("SPAN_PRESENT:"),
        all_prefixed("SOURCE_CONTAINS_SURFACE:"),
        all_prefixed("ENTITY_DIRECTION:"),
        all_prefixed("RELATION_DIRECTION:"),
        float(findings.get("PLAN_COVERAGE", False)),
        float(findings.get("NO_GRAPH_CONTRADICTION", False)),
        min(2.0, len(answer.text) / expected_length),
        min(1.0, bound_characters / max(1, len(answer.text))),
    )


def _answer_variants(result: ControllerResult) -> tuple[RealizedAnswer, ...]:
    assert result.answer is not None
    variants = list(adversarial_mutations(result.answer))
    variants.append(
        result.answer.model_copy(update={"text": f"{result.answer.text} because unsupported"})
    )
    first = result.answer.bindings[0]
    variants.append(
        result.answer.model_copy(
            update={
                "bindings": (
                    first.model_copy(update={"source_span_ids": ("span:substituted",)}),
                    *result.answer.bindings[1:],
                )
            }
        )
    )
    variants.append(
        result.answer.model_copy(
            update={
                "bindings": (
                    first.model_copy(update={"structured_claim_ids": ("claim:substituted",)}),
                    *result.answer.bindings[1:],
                )
            }
        )
    )
    unique: dict[tuple[str, str], RealizedAnswer] = {}
    for variant in variants:
        binding_key = repr(
            tuple(
                (
                    item.start,
                    item.end,
                    item.structured_claim_ids,
                    item.source_span_ids,
                )
                for item in variant.bindings
            )
        )
        unique.setdefault((variant.text, binding_key), variant)
    return tuple(unique.values())


def _examples(results: Iterable[tuple[str, ControllerResult]]) -> tuple[_Example, ...]:
    rows: list[_Example] = []
    for query_id, result in results:
        if (
            result.answer is None
            or result.plan is None
            or result.verification is None
            or not result.verification.passed
        ):
            continue
        rows.append(
            _Example(
                query_id=query_id,
                supported=True,
                deterministic_passed=True,
                features=_features(result, result.answer),
            )
        )
        for mutation in _answer_variants(result):
            deterministic = verify_realization(
                result.frame, result.graph, result.plan, mutation
            ).passed
            rows.append(
                _Example(
                    query_id=query_id,
                    supported=False,
                    deterministic_passed=deterministic,
                    features=_features(result, mutation),
                )
            )
    return tuple(rows)


def _dot(weights: tuple[float, ...], features: tuple[float, ...], bias: float) -> float:
    return sum(weight * value for weight, value in zip(weights, features, strict=True)) + bias


def _train(rows: tuple[_Example, ...]) -> tuple[tuple[float, ...], float]:
    width = len(rows[0].features)
    weights = [0.0] * width
    bias = 0.0
    rate = 0.12
    for _epoch in range(320):
        for row in rows:
            target = float(row.supported)
            score = sum(
                weight * value for weight, value in zip(weights, row.features, strict=True)
            ) + bias
            probability = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, score))))
            error = probability - target
            for index, value in enumerate(row.features):
                weights[index] -= rate * (error * value + 0.0005 * weights[index])
            bias -= rate * error
        rate *= 0.992
    return tuple(weights), bias


def run_adversarial_verifier_experiment(
    results: Iterable[tuple[str, ControllerResult]],
) -> AdversarialVerifierReport:
    """Train by query group and evaluate on disjoint held-out query groups."""

    rows = _examples(results)
    source_ids = {row.query_id for row in rows if row.supported}
    if len(source_ids) < 5:
        return AdversarialVerifierReport(
            source_answer_count=len(source_ids),
            train_example_count=0,
            evaluation_example_count=0,
            evaluation_mutation_count=0,
            deterministic_mutation_rejection_rate=0.0,
            learned_accuracy=0.0,
            learned_supported_precision=0.0,
            learned_mutation_recall=0.0,
            learned_false_accept_rate=0.0,
            incremental_mutations_rejected=0,
            model_bytes=0,
            retained_in_primary_runtime=False,
            decision="INSUFFICIENT_DISJOINT_VERIFIED_ANSWERS",
        )

    def held_out(query_id: str) -> bool:
        digest = hashlib.sha256(query_id.encode()).digest()
        return int.from_bytes(digest[:2], "big") % 5 == 0

    train = tuple(row for row in rows if not held_out(row.query_id))
    evaluation = tuple(row for row in rows if held_out(row.query_id))
    if not train or not evaluation:
        ordered = sorted(source_ids)
        evaluation_ids = set(ordered[::5])
        train = tuple(row for row in rows if row.query_id not in evaluation_ids)
        evaluation = tuple(row for row in rows if row.query_id in evaluation_ids)
    weights, bias = _train(train)
    predictions = [
        _dot(weights, row.features, bias) >= 0.0 for row in evaluation
    ]
    correct = sum(
        prediction == row.supported
        for prediction, row in zip(predictions, evaluation, strict=True)
    )
    supported_predictions = sum(predictions)
    supported_true_positive = sum(
        prediction and row.supported
        for prediction, row in zip(predictions, evaluation, strict=True)
    )
    mutations = [row for row in evaluation if not row.supported]
    mutation_predictions = [
        prediction
        for prediction, row in zip(predictions, evaluation, strict=True)
        if not row.supported
    ]
    rejected_mutations = sum(not prediction for prediction in mutation_predictions)
    deterministic_rejected = sum(not row.deterministic_passed for row in mutations)
    incremental = sum(
        row.deterministic_passed and not prediction
        for row, prediction in zip(mutations, mutation_predictions, strict=True)
    )
    false_accepts = sum(mutation_predictions)
    return AdversarialVerifierReport(
        source_answer_count=len(source_ids),
        train_example_count=len(train),
        evaluation_example_count=len(evaluation),
        evaluation_mutation_count=len(mutations),
        deterministic_mutation_rejection_rate=_ratio(deterministic_rejected, len(mutations)),
        learned_accuracy=_ratio(correct, len(evaluation)),
        learned_supported_precision=_ratio(supported_true_positive, supported_predictions),
        learned_mutation_recall=_ratio(rejected_mutations, len(mutations)),
        learned_false_accept_rate=_ratio(false_accepts, len(mutations)),
        incremental_mutations_rejected=incremental,
        model_bytes=(len(weights) + 1) * 4,
        retained_in_primary_runtime=incremental > 0,
        decision=(
            "SUPPLEMENT_HAS_INCREMENTAL_VETO_VALUE"
            if incremental > 0
            else "SUPPLEMENT_NO_INCREMENTAL_VALUE"
        ),
    )
