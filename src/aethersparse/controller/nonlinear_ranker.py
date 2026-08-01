"""Tiny nonlinear evidence scorer used only for a frozen hard-negative ablation."""

from __future__ import annotations

import math
from collections.abc import Sequence

from pydantic import Field, model_validator

from aethersparse.controller.models import EvidenceRecord, FrozenModel

FEATURE_COUNT = 9
HIDDEN_UNITS = 6


class RankerExample(FrozenModel):
    query_id: str
    features: tuple[float, ...]
    relevant: bool

    @model_validator(mode="after")
    def feature_width(self) -> RankerExample:
        if len(self.features) != FEATURE_COUNT:
            raise ValueError(f"expected {FEATURE_COUNT} ranker features")
        return self


class TinyEvidenceMLP(FrozenModel):
    input_weights: tuple[tuple[float, ...], ...]
    hidden_bias: tuple[float, ...]
    output_weights: tuple[float, ...]
    output_bias: float
    training_examples: int = Field(ge=0)

    @model_validator(mode="after")
    def valid_shape(self) -> TinyEvidenceMLP:
        if len(self.input_weights) != HIDDEN_UNITS:
            raise ValueError("wrong hidden-unit count")
        if any(len(row) != FEATURE_COUNT for row in self.input_weights):
            raise ValueError("wrong input feature width")
        if len(self.hidden_bias) != HIDDEN_UNITS or len(self.output_weights) != HIDDEN_UNITS:
            raise ValueError("wrong output feature width")
        return self

    @property
    def parameter_count(self) -> int:
        return HIDDEN_UNITS * FEATURE_COUNT + HIDDEN_UNITS * 2 + 1

    @property
    def int8_model_bytes(self) -> int:
        # One byte per quantized parameter plus one float scale per tensor.
        return self.parameter_count + (HIDDEN_UNITS + 2) * 4

    @property
    def macs_per_record(self) -> int:
        return HIDDEN_UNITS * FEATURE_COUNT + HIDDEN_UNITS

    def score(self, features: tuple[float, ...]) -> float:
        if len(features) != FEATURE_COUNT:
            raise ValueError(f"expected {FEATURE_COUNT} ranker features")
        hidden = tuple(
            math.tanh(
                sum(weight * value for weight, value in zip(row, features, strict=True))
                + bias
            )
            for row, bias in zip(self.input_weights, self.hidden_bias, strict=True)
        )
        logit = sum(
            weight * value
            for weight, value in zip(self.output_weights, hidden, strict=True)
        ) + self.output_bias
        return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, logit))))


def ranker_features(record: EvidenceRecord) -> tuple[float, ...]:
    return (
        record.entity_fit,
        record.relation_fit,
        record.answerability,
        record.answer_shape_fit,
        record.temporal_fit,
        record.attribution_fit,
        record.source_quality,
        min(1.0, len(record.facet_coverage) / 6.0),
        record.claim.confidence,
    )


def train_tiny_evidence_mlp(
    examples: Sequence[RankerExample],
    *,
    epochs: int = 80,
) -> TinyEvidenceMLP:
    """Train deterministically; examples must already be partitioned and frozen."""

    if not examples:
        raise ValueError("at least one hard-negative example is required")
    if epochs < 1 or epochs > 500:
        raise ValueError("epochs must be in [1,500]")
    if not any(example.relevant for example in examples) or all(
        example.relevant for example in examples
    ):
        raise ValueError("training requires both relevant and hard-negative records")

    input_weights = [
        [((unit + 1) * (feature + 3) % 11 - 5) * 0.012 for feature in range(FEATURE_COUNT)]
        for unit in range(HIDDEN_UNITS)
    ]
    hidden_bias = [0.0] * HIDDEN_UNITS
    output_weights = [((unit * 7) % 9 - 4) * 0.015 for unit in range(HIDDEN_UNITS)]
    output_bias = 0.0
    positives = sum(example.relevant for example in examples)
    negative_weight = len(examples) / max(1, 2 * (len(examples) - positives))
    positive_weight = len(examples) / max(1, 2 * positives)
    rate = 0.055
    ordered = tuple(sorted(examples, key=lambda item: (item.query_id, not item.relevant)))
    for _epoch in range(epochs):
        for example in ordered:
            hidden = [
                math.tanh(
                    sum(
                        weight * value
                        for weight, value in zip(row, example.features, strict=True)
                    )
                    + bias
                )
                for row, bias in zip(input_weights, hidden_bias, strict=True)
            ]
            logit = sum(
                weight * value
                for weight, value in zip(output_weights, hidden, strict=True)
            ) + output_bias
            probability = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, logit))))
            target = float(example.relevant)
            class_weight = positive_weight if example.relevant else negative_weight
            output_gradient = (probability - target) * class_weight
            prior_output_weights = output_weights[:]
            for unit in range(HIDDEN_UNITS):
                output_weights[unit] -= rate * (
                    output_gradient * hidden[unit] + 0.0008 * output_weights[unit]
                )
            output_bias -= rate * output_gradient
            for unit in range(HIDDEN_UNITS):
                hidden_gradient = (
                    output_gradient
                    * prior_output_weights[unit]
                    * (1.0 - hidden[unit] * hidden[unit])
                )
                for feature in range(FEATURE_COUNT):
                    input_weights[unit][feature] -= rate * (
                        hidden_gradient * example.features[feature]
                        + 0.0008 * input_weights[unit][feature]
                    )
                hidden_bias[unit] -= rate * hidden_gradient
        rate *= 0.975
    return TinyEvidenceMLP(
        input_weights=tuple(tuple(row) for row in input_weights),
        hidden_bias=tuple(hidden_bias),
        output_weights=tuple(output_weights),
        output_bias=output_bias,
        training_examples=len(examples),
    )


def rerank_records(
    model: TinyEvidenceMLP,
    records: Sequence[EvidenceRecord],
) -> tuple[EvidenceRecord, ...]:
    return tuple(
        sorted(
            records,
            key=lambda record: (
                -model.score(ranker_features(record)),
                record.claim.claim_id,
            ),
        )
    )
