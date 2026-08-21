"""COG-derived, same-scale controller repair for exact micro-operations.

This module deliberately remains a view over :class:`MicroState`: it does not
own the authoritative Cognitive Obligation Graph.  The features are generic
obligation/claim contrasts and contain neither answer labels nor benchmark
identities.  This keeps the policy usable by the Python reference while the
native runtime consumes the frozen integer view.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aethersparse.controller.micro_ops import (
    MICRO_OPERATIONS,
    MicroAction,
    MicroState,
    legal_actions,
)

ADAPTIVE_POLICY_SCHEMA = "aethercore.cog-masked-linear-policy.v1"
QUANTIZED_POLICY_SCHEMA = "aethercore.cog-masked-linear-policy.int8.v1"
FEATURE_SCALE = 256

# 38 features x 34 exact operations = 1,292 learned parameters.  This is the
# same small-controller scale as V13's 918 parameters, not a capacity jump.
ADAPTIVE_FEATURE_NAMES = (
    "bias",
    "shape_direct",
    "shape_date",
    "shape_quantity",
    "shape_quotation",
    "shape_list",
    "shape_comparison",
    "has_active_claims",
    "has_selected_claims",
    "has_bound_claims",
    "has_derived_values",
    "has_plan",
    "verification_passed",
    "selected_count",
    "bound_count",
    "total_actions",
    "operation_repeat_count",
    "argument_present",
    "obligations_required",
    "obligations_satisfied",
    "obligations_violated",
    "obligations_unresolved_after",
    "completion_if_action",
    "competing_hypotheses",
    "contradiction",
    "claim_subject_matches_hypothesis",
    "claim_subject_conflicts_hypothesis",
    "claim_subject_hypothesis_confidence",
    "claim_relation_matches",
    "claim_answer_shape_matches",
    "claim_time_compatible",
    "claim_attribution_compatible",
    "claim_exact_evidence_surface",
    "claim_evidence_context",
    "claim_value_position",
    "claim_value_occurrence_inverse",
    "claim_confidence",
    "claim_confidence_contrast",
)


def _strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item))
    return ()


def _claim_value(claim: dict[str, Any], shape: str) -> str:
    keys = ("quotation", "object_value") if shape == "quotation" else (
        "object_value",
        "quantity_value",
        "quotation",
    )
    return next((str(claim[key]) for key in keys if claim.get(key)), "")


def _value_kind(value: str) -> str:
    lowered = value.strip().lower()
    if len(lowered) >= 4 and lowered[:4].isdigit():
        return "date"
    if any(char.isdigit() for char in lowered):
        return "quantity"
    return "text"


def _entity_hypotheses(frame: dict[str, Any]) -> dict[str, float]:
    """Recover explicit subject hypotheses without treating ambiguity as match-all."""

    hypotheses = {entity_id: 1.0 for entity_id in _strings(frame.get("candidate_entity_ids"))}
    for mention in frame.get("entity_mentions", ()):
        if not isinstance(mention, dict):
            continue
        selected = str(mention.get("selected_entity_id") or "")
        if selected:
            confidence = float(mention.get("selected_confidence") or 0.0)
            hypotheses[selected] = max(hypotheses.get(selected, 0.0), confidence)
        for candidate in mention.get("candidates", ()):
            if not isinstance(candidate, dict):
                continue
            entity_id = str(candidate.get("entity_id") or "")
            if entity_id:
                confidence = float(candidate.get("confidence") or 0.0)
                hypotheses[entity_id] = max(hypotheses.get(entity_id, 0.0), confidence)
    return hypotheses


def _evidence_features(
    state: MicroState, claim: dict[str, Any], value: str
) -> tuple[float, float, float, float]:
    """Measure evidence quality from content/layout, never synthetic span names."""

    spans = {
        str(span.get("span_id", "")): str(span.get("text", ""))
        for span in state.source_spans
    }
    supporting = [
        spans[source_id]
        for source_id in _strings(claim.get("source_span_ids"))
        if source_id in spans
    ]
    value_lower = value.casefold()
    matches: list[tuple[str, int, int]] = []
    for text in supporting:
        lowered = text.casefold()
        position = lowered.find(value_lower) if value_lower else -1
        if position >= 0:
            matches.append((text, position, lowered.count(value_lower)))
    exact = float(bool(matches))
    if not matches:
        return exact, 0.0, 0.0, 0.0
    # Context is useful provenance: a copied value embedded in a bounded source
    # passage carries more relational evidence than a value-only fragment.
    text, position, occurrences = max(
        matches,
        key=lambda item: (len(item[0]), -item[1]),
    )
    context = min(1.0, math.log2(1.0 + len(text)) / 10.0)
    position_score = 1.0 - min(1.0, position / max(1, len(text)))
    occurrence_inverse = 1.0 / max(1, occurrences)
    return exact, context, position_score, occurrence_inverse


def _required_obligations(frame: dict[str, Any], shape: str) -> set[str]:
    facets = {item.casefold() for item in _strings(frame.get("required_facets"))}
    required = {"claim", "answer_type", "evidence"}
    if "subject" in facets or _entity_hypotheses(frame):
        required.add("subject")
    if "relation" in facets or _strings(frame.get("requested_relation_families")):
        required.add("relation")
    if "object" in facets:
        required.add("object")
    if "time" in facets or _strings(frame.get("temporal_constraints")) or shape == "date":
        required.add("time")
    if "attribution" in facets or _strings(frame.get("attribution_constraints")):
        required.add("attribution")
    if "location" in facets or _strings(frame.get("location_constraints")):
        required.add("location")
    return required


def adaptive_action_features(state: MicroState, action: MicroAction) -> tuple[float, ...]:
    """Return a compact COG/claim-contrast view with no answer-label leakage."""

    shape = str(state.frame.get("answer_shape", ""))
    claim_id = action.arguments.get("claim_id", "")
    claim = next(
        (item for item in state.claims if str(item.get("claim_id", "")) == claim_id), None
    )
    hypotheses = _entity_hypotheses(state.frame)
    relations = set(_strings(state.frame.get("requested_relation_families")))
    required = _required_obligations(state.frame, shape)
    satisfied: set[str] = set()
    violated: set[str] = set()
    subject_match = subject_conflict = subject_confidence = 0.0
    relation_match = shape_match = time_match = attribution_match = 0.0
    exact = context = position = occurrence_inverse = confidence = confidence_contrast = 0.0
    contradiction = float(bool(state.frame.get("contradictions")))
    if claim is not None:
        subjects = {
            str(claim.get("subject_entity_id") or ""),
            str(claim.get("object_entity_id") or ""),
        } - {""}
        matching = subjects & set(hypotheses)
        subject_match = float(bool(matching))
        subject_conflict = float(bool(hypotheses) and not matching)
        subject_confidence = max((hypotheses[item] for item in matching), default=0.0)
        relation = str(claim.get("relation_family") or "")
        relation_match = float(not relations or relation in relations)
        value = _claim_value(claim, shape)
        claim_shape = str(claim.get("answer_shape") or "")
        expected_kind = "quantity" if shape in {"quantity", "comparison"} else shape
        shape_match = float(
            claim_shape == shape
            or (shape == "list" and claim_shape in {"definition", "entity", "person"})
            or _value_kind(value) == expected_kind
        )
        temporal = _strings(state.frame.get("temporal_constraints"))
        occurred = str(claim.get("occurred_at") or value)
        time_match = float(
            (shape != "date" and not temporal)
            or (bool(occurred) and (not temporal or any(item in occurred for item in temporal)))
        )
        attribution = _strings(state.frame.get("attribution_constraints"))
        speaker = str(claim.get("speaker_entity_id") or "")
        attribution_match = float(not attribution or speaker in attribution)
        exact, context, position, occurrence_inverse = _evidence_features(state, claim, value)
        confidence = max(0.0, min(1.0, float(claim.get("confidence") or 0.0)))
        peer_confidences = [
            max(0.0, min(1.0, float(item.get("confidence") or 0.0)))
            for item in state.claims
            if str(item.get("claim_id", "")) in state.active_claim_ids
        ]
        confidence_contrast = confidence - max(peer_confidences, default=confidence)
        tests = {
            "claim": bool(value),
            "answer_type": bool(shape_match),
            "evidence": bool(exact),
            "subject": bool(subject_match),
            "relation": bool(relation_match),
            "object": bool(value),
            "time": bool(time_match),
            "attribution": bool(attribution_match),
            "location": bool(claim.get("location_entity_id")),
        }
        for obligation in required:
            (satisfied if tests.get(obligation, False) else violated).add(obligation)
        contradiction = max(contradiction, float(str(claim.get("polarity", "")) == "conflicting"))

    required_count = max(1, len(required))
    unresolved = len(required - satisfied)
    competing = sum(
        1
        for item in state.claims
        if str(item.get("claim_id", "")) in state.active_claim_ids
        and str(item.get("claim_id", "")) != claim_id
    )
    values = (
        1.0,
        float(shape in {"definition", "person", "entity"}),
        float(shape == "date"),
        float(shape == "quantity"),
        float(shape == "quotation"),
        float(shape == "list"),
        float(shape == "comparison"),
        float(bool(state.active_claim_ids)),
        float(bool(state.selected_claim_ids)),
        float(bool(state.bound_claim_ids)),
        float(bool(state.derived_values)),
        float(bool(state.plan_values)),
        float(state.verification_passed),
        min(1.0, len(state.selected_claim_ids) / 6.0),
        min(1.0, len(state.bound_claim_ids) / 6.0),
        min(1.0, state.total_actions / 12.0),
        min(1.0, state.operation_counts.get(action.operation_id, 0) / 8.0),
        float(bool(action.arguments)),
        min(1.0, len(required) / 9.0),
        len(satisfied) / required_count,
        len(violated) / required_count,
        unresolved / required_count,
        len(satisfied) / required_count,
        min(1.0, competing / 32.0),
        contradiction,
        subject_match,
        subject_conflict,
        subject_confidence,
        relation_match,
        shape_match,
        time_match,
        attribution_match,
        exact,
        context,
        position,
        occurrence_inverse,
        confidence,
        confidence_contrast,
    )
    if len(values) != len(ADAPTIVE_FEATURE_NAMES):
        raise AssertionError("adaptive policy feature schema mismatch")
    return values


def quantized_action_features(state: MicroState, action: MicroAction) -> tuple[int, ...]:
    return tuple(round(value * FEATURE_SCALE) for value in adaptive_action_features(state, action))


def _arguments_key(action: MicroAction) -> str:
    return json.dumps(action.arguments, separators=(",", ":"), sort_keys=True)


class AdaptiveMaskedPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = ADAPTIVE_POLICY_SCHEMA
    feature_names: tuple[str, ...] = ADAPTIVE_FEATURE_NAMES
    operation_ids: tuple[int, ...]
    weights: tuple[tuple[float, ...], ...]
    training_epochs: int = Field(ge=0)
    training_algorithm: str = "cog_structured_perceptron"
    roll_in_examples: int = Field(default=0, ge=0)

    @property
    def parameter_count(self) -> int:
        return len(self.operation_ids) * len(self.feature_names)

    def select(self, state: MicroState, *, argument_cap: int = 64) -> MicroAction | None:
        actions = legal_actions(state, argument_cap=argument_cap)
        if not actions:
            return None
        rows = dict(zip(self.operation_ids, self.weights, strict=True))

        def key(item: tuple[int, MicroAction]) -> tuple[float, int, int, str]:
            index, action = item
            score = sum(
                weight * feature
                for weight, feature in zip(
                    rows[action.operation_id], adaptive_action_features(state, action), strict=True
                )
            )
            return score, -index, -action.operation_id, _arguments_key(action)

        return max(enumerate(actions), key=key)[1]


class QuantizedAdaptivePolicy(BaseModel):
    """No-runtime-dependency int8 weights and fixed-point integer activations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = QUANTIZED_POLICY_SCHEMA
    feature_names: tuple[str, ...] = ADAPTIVE_FEATURE_NAMES
    operation_ids: tuple[int, ...]
    weights_int8: tuple[tuple[int, ...], ...]
    feature_scale: int = FEATURE_SCALE
    weight_scale: float = Field(gt=0.0)
    source_training_algorithm: str
    roll_in_examples: int = Field(default=0, ge=0)

    @property
    def parameter_count(self) -> int:
        return len(self.operation_ids) * len(self.feature_names)

    @property
    def parameter_bytes(self) -> int:
        return self.parameter_count

    @property
    def macs_per_full_decision(self) -> int:
        return self.parameter_count

    def select(self, state: MicroState, *, argument_cap: int = 64) -> MicroAction | None:
        actions = legal_actions(state, argument_cap=argument_cap)
        if not actions:
            return None
        rows = dict(zip(self.operation_ids, self.weights_int8, strict=True))

        def key(item: tuple[int, MicroAction]) -> tuple[int, int, int, str]:
            index, action = item
            score = sum(
                weight * feature
                for weight, feature in zip(
                    rows[action.operation_id], quantized_action_features(state, action), strict=True
                )
            )
            return score, -index, -action.operation_id, _arguments_key(action)

        return max(enumerate(actions), key=key)[1]


def fit_adaptive_policy(
    examples: Sequence[tuple[MicroState, MicroAction]],
    *,
    epochs: int = 24,
    roll_in_examples: int = 0,
) -> AdaptiveMaskedPolicy:
    operation_ids = tuple(operation.operation_id for operation in MICRO_OPERATIONS)
    operation_index = {operation_id: index for index, operation_id in enumerate(operation_ids)}
    weights = [[0.0] * len(ADAPTIVE_FEATURE_NAMES) for _ in operation_ids]
    # Training revisits the same closed development states for every epoch.
    # Precomputing the bounded legal views avoids repeatedly scanning evidence
    # text and makes the qualification cost linear in unique decision records.
    prepared: list[
        tuple[
            MicroAction,
            tuple[tuple[MicroAction, tuple[float, ...]], ...],
        ]
    ] = []
    for state, target in examples:
        legal = legal_actions(state, argument_cap=64)
        if target not in legal:
            raise ValueError("certified target action is absent from legal action mask")
        prepared.append(
            (
                target,
                tuple((action, adaptive_action_features(state, action)) for action in legal),
            )
        )
    for _epoch in range(epochs):
        for target, prepared_actions in prepared:

            def score(item: tuple[MicroAction, tuple[float, ...]]) -> float:
                action, features = item
                return sum(
                    weight * feature
                    for weight, feature in zip(
                        weights[operation_index[action.operation_id]],
                        features,
                        strict=True,
                    )
                )

            predicted_item = max(
                enumerate(prepared_actions),
                key=lambda item: (score(item[1]), -item[0], -item[1][0].operation_id),
            )[1]
            predicted, predicted_features = predicted_item
            if predicted != target:
                target_features = next(
                    features for action, features in prepared_actions if action == target
                )
                target_row = weights[operation_index[target.operation_id]]
                predicted_row = weights[operation_index[predicted.operation_id]]
                for index, value in enumerate(target_features):
                    target_row[index] += value
                for index, value in enumerate(predicted_features):
                    predicted_row[index] -= value
    return AdaptiveMaskedPolicy(
        operation_ids=operation_ids,
        weights=tuple(tuple(round(value, 8) for value in row) for row in weights),
        training_epochs=epochs,
        roll_in_examples=roll_in_examples,
    )


def quantize_adaptive_policy(policy: AdaptiveMaskedPolicy) -> QuantizedAdaptivePolicy:
    maximum = max((abs(value) for row in policy.weights for value in row), default=0.0)
    scale = maximum / 127.0 if maximum else 1.0
    weights = tuple(
        tuple(max(-127, min(127, round(value / scale))) for value in row)
        for row in policy.weights
    )
    return QuantizedAdaptivePolicy(
        operation_ids=policy.operation_ids,
        weights_int8=weights,
        weight_scale=scale,
        source_training_algorithm=policy.training_algorithm,
        roll_in_examples=policy.roll_in_examples,
    )


def finite_adaptive_weights(policy: AdaptiveMaskedPolicy) -> bool:
    return all(math.isfinite(value) for row in policy.weights for value in row)
