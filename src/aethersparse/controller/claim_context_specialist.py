"""Sparse passage-context specialist layered over the frozen V14 controller.

The specialist is deliberately narrow: it may only discriminate between legal
``SELECT_CLAIM`` arguments after the V14 policy has selected that operation.
It cannot change the operation grammar, retrieve candidates, bypass the legal
mask, or alter verifier behavior.  Features describe bounded source layout and
relation context; they contain no case identity, accepted answer, or target ID.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aethersparse.controller.adaptive_policy import QuantizedAdaptivePolicy
from aethersparse.controller.micro_ops import MicroAction, MicroState, legal_actions

SELECT_CLAIM_OPERATION = 43
CONTEXT_FEATURE_SCALE = 256
CONTEXT_SPECIALIST_SCHEMA = "aethercore.claim-context-specialist.int8.v1"

CONTEXT_FEATURE_NAMES = (
    "bias",
    "claim_confidence",
    "value_length",
    "value_occurrence_count",
    "value_first_position",
    "value_last_position",
    "span_value_only",
    "inside_reference",
    "metadata_date_context",
    "citation_needed_context",
    "narrative_relation_context",
    "list_entry_context",
    "parenthetical_birth_death_context",
    "table_row_context",
    "wiki_markup_fraction",
    "explicit_quote_delimiters",
    "quote_after_colon",
    "definitional_form",
)

_METADATA_DATE = re.compile(
    r"(?:access[-_ ]?date|archive[-_ ]?date|retrieved|url|date)\s*[=:]\s*$",
    re.IGNORECASE,
)
_NARRATIVE_RELATION = re.compile(
    r"(?:released?|release\s+on|founded?|opened?|began|ended|occurred|in\s+the\s+year)\W*$",
    re.IGNORECASE,
)
_DEFINITION = re.compile(
    r"^(?:an?|the)\s+(?:object|person|place|process|device|system|term|concept)\b|"
    r"\b(?:used\s+for|refers\s+to|is\s+an?)\b",
    re.IGNORECASE,
)


def _claim_value(claim: dict[str, Any], shape: str) -> str:
    keys = ("quotation", "object_value") if shape == "quotation" else (
        "object_value",
        "quantity_value",
        "quotation",
    )
    return next((str(claim[key]) for key in keys if claim.get(key)), "")


def _source_text(state: MicroState, claim: dict[str, Any]) -> str:
    span_text = {
        str(span.get("span_id", "")): str(span.get("text", ""))
        for span in state.source_spans
    }
    return "\n".join(
        span_text[source_id]
        for source_id in claim.get("source_span_ids", ())
        if source_id in span_text
    )


def claim_context_features(state: MicroState, action: MicroAction) -> tuple[float, ...]:
    """Return generic bounded passage-context features for one claim argument."""

    if action.operation_id != SELECT_CLAIM_OPERATION:
        return (0.0,) * len(CONTEXT_FEATURE_NAMES)
    claim_id = action.arguments.get("claim_id", "")
    claim = next(
        (item for item in state.claims if str(item.get("claim_id", "")) == claim_id),
        None,
    )
    if claim is None:
        return (0.0,) * len(CONTEXT_FEATURE_NAMES)
    shape = str(state.frame.get("answer_shape", ""))
    value = _claim_value(claim, shape)
    text = _source_text(state, claim)
    folded_text = text.casefold()
    folded_value = value.casefold()
    positions: list[int] = []
    cursor = 0
    while folded_value and (found := folded_text.find(folded_value, cursor)) >= 0:
        positions.append(found)
        cursor = found + max(1, len(folded_value))
    first = positions[0] if positions else 0
    last = positions[-1] if positions else 0
    length = max(1, len(text))
    before = text[max(0, first - 48) : first]
    after = text[first + len(value) : first + len(value) + 48]
    reference_open = text.rfind("<ref", 0, first)
    reference_close = text.rfind("</ref>", 0, first)
    inside_reference = reference_open > reference_close
    local = text[max(0, first - 96) : first + len(value) + 96]
    markup_chars = sum(character in "[]{}<>|='" for character in local)
    stripped = " ".join(text.strip().split())
    quote_after_colon = bool(
        re.search(r"[:]\s*[\"']\s*$", before)
        and re.match(r"\s*[\"']", after)
    )
    values = (
        1.0,
        max(0.0, min(1.0, float(claim.get("confidence") or 0.0))),
        min(1.0, len(value) / 160.0),
        min(1.0, len(positions) / 4.0),
        1.0 - min(1.0, first / length),
        min(1.0, last / length),
        float(bool(value) and stripped.casefold() == folded_value.strip()),
        float(inside_reference),
        float(bool(_METADATA_DATE.search(before))),
        float("citation needed" in local.casefold()),
        float(bool(_NARRATIVE_RELATION.search(before))),
        float(bool(re.search(r"(?:^|\n)\s*[*|!]", before[-16:])) or "&ndash;" in after),
        float(bool(re.search(r"\([bd]\.?\s*$", before, re.IGNORECASE))),
        float("|-" in before or "scope=\"row\"" in before.casefold()),
        min(1.0, markup_chars / max(1, len(local))),
        float(
            bool(re.search(r"[\"']\s*$", before))
            and bool(re.match(r"\s*[\"']", after))
        ),
        float(quote_after_colon),
        float(bool(_DEFINITION.search(value.strip()))),
    )
    if len(values) != len(CONTEXT_FEATURE_NAMES):
        raise AssertionError("claim-context feature schema mismatch")
    return values


def quantized_claim_context_features(
    state: MicroState, action: MicroAction
) -> tuple[int, ...]:
    return tuple(
        round(value * CONTEXT_FEATURE_SCALE)
        for value in claim_context_features(state, action)
    )


def _head_for(state: MicroState) -> str:
    shape = str(state.frame.get("answer_shape", ""))
    return shape if shape in {"date", "quotation"} else "other"


class QuantizedClaimContextSpecialist(BaseModel):
    """Three sparse int8 heads sharing one generic passage feature family."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = CONTEXT_SPECIALIST_SCHEMA
    feature_names: tuple[str, ...] = CONTEXT_FEATURE_NAMES
    head_names: tuple[str, ...] = ("date", "quotation", "other")
    weights_int8: tuple[tuple[int, ...], ...]
    weight_scale: float = Field(gt=0.0)
    training_epochs: int = Field(ge=0)
    fit_partitions: tuple[str, ...] = ("development",)

    @property
    def parameter_count(self) -> int:
        return len(self.head_names) * len(self.feature_names)

    @property
    def parameter_bytes(self) -> int:
        return self.parameter_count

    def select_claim(self, state: MicroState, actions: Sequence[MicroAction]) -> MicroAction:
        candidates = tuple(
            action for action in actions if action.operation_id == SELECT_CLAIM_OPERATION
        )
        if not candidates:
            raise ValueError("claim specialist requires legal SELECT_CLAIM candidates")
        row = self.weights_int8[self.head_names.index(_head_for(state))]

        def key(item: tuple[int, MicroAction]) -> tuple[int, int, str]:
            index, action = item
            score = sum(
                weight * feature
                for weight, feature in zip(
                    row, quantized_claim_context_features(state, action), strict=True
                )
            )
            return score, -index, action.arguments.get("claim_id", "")

        return max(enumerate(candidates), key=key)[1]

class SparseContextPolicy(BaseModel):
    """Frozen V14 operation policy plus a narrow legal-argument specialist."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    base: QuantizedAdaptivePolicy
    specialist: QuantizedClaimContextSpecialist

    @property
    def stored_parameter_count(self) -> int:
        return self.base.parameter_count + self.specialist.parameter_count

    @property
    def active_parameter_count(self) -> int:
        return self.base.parameter_count + len(self.specialist.feature_names)

    def select(self, state: MicroState, *, argument_cap: int = 64) -> MicroAction | None:
        base_action = self.base.select(state, argument_cap=argument_cap)
        if base_action is None or base_action.operation_id != SELECT_CLAIM_OPERATION:
            return base_action
        legal = legal_actions(state, argument_cap=argument_cap)
        claims = tuple(action for action in legal if action.operation_id == SELECT_CLAIM_OPERATION)
        if len(claims) < 2:
            return base_action
        return self.specialist.select_claim(state, claims)


def fit_claim_context_specialist(
    examples: Sequence[tuple[MicroState, MicroAction]], *, epochs: int
) -> QuantizedClaimContextSpecialist:
    """Fit only on certified development ``SELECT_CLAIM`` decisions."""

    head_names = ("date", "quotation", "other")
    rows = [[0.0] * len(CONTEXT_FEATURE_NAMES) for _ in head_names]
    prepared: list[tuple[int, MicroAction, tuple[tuple[MicroAction, tuple[float, ...]], ...]]] = []
    for state, target in examples:
        if target.operation_id != SELECT_CLAIM_OPERATION:
            continue
        actions = tuple(
            action
            for action in legal_actions(state, argument_cap=64)
            if action.operation_id == SELECT_CLAIM_OPERATION
        )
        if target not in actions:
            raise ValueError("certified claim target is outside the legal mask")
        prepared.append(
            (
                head_names.index(_head_for(state)),
                target,
                tuple((action, claim_context_features(state, action)) for action in actions),
            )
        )
    for _epoch in range(epochs):
        for head, target, candidates in prepared:
            row = rows[head]

            def score(
                item: tuple[MicroAction, tuple[float, ...]], _row: list[float] = row
            ) -> float:
                return sum(
                    weight * feature
                    for weight, feature in zip(_row, item[1], strict=True)
                )

            predicted, predicted_features = max(
                enumerate(candidates),
                key=lambda item: (score(item[1]), -item[0]),
            )[1]
            if predicted != target:
                target_features = next(
                    features for action, features in candidates if action == target
                )
                for index, value in enumerate(target_features):
                    row[index] += value
                    row[index] -= predicted_features[index]
    maximum = max((abs(value) for row in rows for value in row), default=0.0)
    scale = maximum / 127.0 if maximum else 1.0
    quantized = tuple(
        tuple(max(-127, min(127, round(value / scale))) for value in row) for row in rows
    )
    if any(not math.isfinite(value) for row in rows for value in row):
        raise ValueError("non-finite specialist weight")
    return QuantizedClaimContextSpecialist(
        weights_int8=quantized,
        weight_scale=scale,
        training_epochs=epochs,
    )
