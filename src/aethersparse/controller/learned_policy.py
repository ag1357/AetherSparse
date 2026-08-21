"""Small split-safe learned policy for exact AetherCore micro-operations.

The controller is a structured multiclass perceptron.  It scores only actions
emitted by :func:`legal_actions`, so inference cannot bypass the typed runtime.
World facts are never parameters: claim features describe compatibility with
the current frame and exact evidence, not benchmark answers.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aethersparse.controller.micro_ops import (
    MICRO_OPERATIONS,
    MicroAction,
    MicroState,
    legal_actions,
)

POLICY_SCHEMA_VERSION = "aethercore.masked-linear-policy.v1"

FEATURE_NAMES = (
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
    "claim_subject_matches_frame",
    "claim_relation_matches_frame",
    "claim_shape_matches_frame",
    "claim_has_exact_source_surface",
    "claim_confidence",
    "claim_reverse_ordinal",
    "claim_source_count",
    "entity_argument_enumerated",
    "source_argument_known",
)


class PolicyDecisionRecord(BaseModel):
    """Closed, compact projection of one certified trajectory transition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query_session_identity: str
    semantic_address_candidates: tuple[str, ...]
    exact_evidence_handles: tuple[str, ...]
    unresolved_state: tuple[str, ...]
    workspace_before_sha256: str
    workspace_after_sha256: str
    workspace_summary: dict[str, Any]
    legal_action_mask: tuple[int, ...]
    selected_operation: int
    operation_arguments: dict[str, str]
    verifier_disposition: dict[str, Any]
    trajectory_identity: str
    split_identity: str


class MaskedLinearPolicy(BaseModel):
    """A tiny integer-friendly linear policy with a typed legal action mask."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = POLICY_SCHEMA_VERSION
    feature_names: tuple[str, ...] = FEATURE_NAMES
    operation_ids: tuple[int, ...]
    weights: tuple[tuple[float, ...], ...]
    training_split: str = "development"
    training_epochs: int = Field(ge=0)
    training_algorithm: str = "structured_perceptron"

    @property
    def parameter_count(self) -> int:
        return len(self.operation_ids) * len(self.feature_names)

    def select(self, state: MicroState, *, argument_cap: int = 64) -> MicroAction | None:
        actions = legal_actions(state, argument_cap=argument_cap)
        if not actions:
            return None
        rows = {
            operation_id: row
            for operation_id, row in zip(self.operation_ids, self.weights, strict=True)
        }

        def key(item: tuple[int, MicroAction]) -> tuple[float, int, int, str]:
            index, action = item
            row = rows[action.operation_id]
            score = sum(
                weight * feature
                for weight, feature in zip(row, action_features(state, action), strict=True)
            )
            # Stable final keys are part of the frozen inference contract.
            return (score, -index, -action.operation_id, _arguments_key(action.arguments))

        return max(enumerate(actions), key=key)[1]


def _strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item))
    return ()


def _arguments_key(arguments: dict[str, str]) -> str:
    return json.dumps(arguments, separators=(",", ":"), sort_keys=True)


def _claim_value(claim: dict[str, Any], shape: str) -> str:
    keys = ("quotation", "object_value") if shape == "quotation" else (
        "object_value",
        "quantity_value",
        "quotation",
    )
    return next((str(claim[key]) for key in keys if claim.get(key)), "")


def _value_shape(value: str) -> str:
    lowered = value.strip().lower()
    if len(lowered) >= 4 and lowered[:4].isdigit():
        return "date"
    if any(char.isdigit() for char in lowered):
        return "quantity"
    return "text"


def action_features(state: MicroState, action: MicroAction) -> tuple[float, ...]:
    """Return bounded state/action features containing no answer labels."""

    shape = str(state.frame.get("answer_shape", ""))
    frame_entities = set(_strings(state.frame.get("candidate_entity_ids")))
    frame_relations = set(_strings(state.frame.get("requested_relation_families")))
    span_text = {
        str(span.get("span_id", "")): str(span.get("text", "")) for span in state.source_spans
    }
    claim_ids = tuple(str(claim.get("claim_id", "")) for claim in state.claims)
    claim_id = action.arguments.get("claim_id", "")
    claim = next(
        (item for item in state.claims if str(item.get("claim_id", "")) == claim_id), None
    )
    subject_match = relation_match = shape_match = exact_surface = confidence = 0.0
    reverse_ordinal = source_count = 0.0
    if claim is not None:
        subject_match = float(
            not frame_entities
            or bool(
                frame_entities
                & {
                    str(claim.get("subject_entity_id", "")),
                    str(claim.get("object_entity_id", "")),
                }
            )
        )
        relation_match = float(
            not frame_relations or str(claim.get("relation_family", "")) in frame_relations
        )
        value = _claim_value(claim, shape)
        claim_shape = str(claim.get("answer_shape", ""))
        expected_kind = "quantity" if shape in {"quantity", "comparison"} else shape
        shape_match = float(claim_shape == shape or _value_shape(value) == expected_kind)
        source_ids = _strings(claim.get("source_span_ids"))
        exact_surface = float(
            bool(value) and any(value in span_text.get(item, "") for item in source_ids)
        )
        raw_confidence = claim.get("confidence", 0.0)
        confidence = max(0.0, min(1.0, float(raw_confidence or 0.0)))
        ordinal = claim_ids.index(claim_id) if claim_id in claim_ids else len(claim_ids)
        reverse_ordinal = 1.0 - ordinal / max(1, len(claim_ids))
        source_count = min(1.0, len(source_ids) / 4.0)
    entity_id = action.arguments.get("entity_id", "")
    source_id = action.arguments.get("source_id", "")
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
        subject_match,
        relation_match,
        shape_match,
        exact_surface,
        confidence,
        reverse_ordinal,
        source_count,
        float(bool(entity_id) and entity_id in state.enumerated_entity_ids),
        float(
            bool(source_id)
            and source_id in {str(span.get("span_id", "")) for span in state.source_spans}
        ),
    )
    if len(values) != len(FEATURE_NAMES):
        raise AssertionError("policy feature schema mismatch")
    return values


def fit_masked_linear_policy(
    examples: Sequence[tuple[MicroState, MicroAction]],
    *,
    epochs: int = 24,
    averaged: bool = False,
) -> MaskedLinearPolicy:
    """Fit a deterministic structured perceptron on development examples only."""

    operation_ids = tuple(operation.operation_id for operation in MICRO_OPERATIONS)
    operation_index = {operation_id: index for index, operation_id in enumerate(operation_ids)}
    weights = [[0.0] * len(FEATURE_NAMES) for _ in operation_ids]
    totals = [[0.0] * len(FEATURE_NAMES) for _ in operation_ids]
    averaging_steps = 0
    if not examples:
        return MaskedLinearPolicy(
            operation_ids=operation_ids,
            weights=tuple(tuple(row) for row in weights),
            training_epochs=0,
            training_algorithm=(
                "averaged_structured_perceptron" if averaged else "structured_perceptron"
            ),
        )
    for _epoch in range(epochs):
        for state, target in examples:
            actions = legal_actions(state, argument_cap=64)
            if target not in actions:
                raise ValueError("certified target action is absent from legal action mask")

            def score(action: MicroAction, state: MicroState = state) -> float:
                row = weights[operation_index[action.operation_id]]
                return sum(
                    weight * feature
                    for weight, feature in zip(row, action_features(state, action), strict=True)
                )

            predicted = max(
                enumerate(actions),
                key=lambda item: (score(item[1]), -item[0], -item[1].operation_id),
            )[1]
            if predicted == target:
                pass
            else:
                target_features = action_features(state, target)
                predicted_features = action_features(state, predicted)
                target_row = weights[operation_index[target.operation_id]]
                predicted_row = weights[operation_index[predicted.operation_id]]
                for index, value in enumerate(target_features):
                    target_row[index] += value
                for index, value in enumerate(predicted_features):
                    predicted_row[index] -= value
            if averaged:
                averaging_steps += 1
                for row_index, row in enumerate(weights):
                    for feature_index, value in enumerate(row):
                        totals[row_index][feature_index] += value
    selected_weights = (
        [
            [value / averaging_steps for value in row]
            for row in totals
        ]
        if averaged and averaging_steps
        else weights
    )
    return MaskedLinearPolicy(
        operation_ids=operation_ids,
        weights=tuple(tuple(round(value, 8) for value in row) for row in selected_weights),
        training_epochs=epochs,
        training_algorithm=(
            "averaged_structured_perceptron" if averaged else "structured_perceptron"
        ),
    )


def state_sha256(state: MicroState) -> str:
    payload = json.dumps(
        state.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def workspace_summary(state: MicroState) -> dict[str, Any]:
    return {
        "active_claims": len(state.active_claim_ids),
        "selected_claims": len(state.selected_claim_ids),
        "bound_claims": len(state.bound_claim_ids),
        "derived_values": len(state.derived_values),
        "plan_values": len(state.plan_values),
        "verification_passed": state.verification_passed,
        "terminal": state.terminal,
        "total_actions": state.total_actions,
    }


def legal_mask(actions: Iterable[MicroAction]) -> tuple[int, ...]:
    """Operation-level mask used by the compact compiled-policy interface."""

    return tuple(sorted({action.operation_id for action in actions}))


def finite_weights(policy: MaskedLinearPolicy) -> bool:
    return all(math.isfinite(value) for row in policy.weights for value in row)
