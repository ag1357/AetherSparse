"""Exact, typed cognitive micro-operations for AetherCore qualification.

This registry is separate from the v09 high-level diagnostic operator table.
IDs are stable and operations can only transform objects already present in a
replay state (or exact deterministic derivatives such as counts/comparisons).
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from aethersparse.controller.answering import make_answer_plan, realize_plan
from aethersparse.controller.models import (
    AnswerSelection,
    AnswerShape,
    EvidenceGraph,
    ExactSourceSpan,
    QueryFrame,
    StructuredClaim,
)
from aethersparse.controller.replay import ReplayCase
from aethersparse.controller.verification import verify_realization

CostClass = Literal["read-bearing", "compute-only", "free"]


class MicroOpName(StrEnum):
    ENUMERATE_CLAIMS = "ENUMERATE_CLAIMS"
    ENUMERATE_VALUES = "ENUMERATE_VALUES"
    ENUMERATE_ENTITIES = "ENUMERATE_ENTITIES"
    ENUMERATE_RELATIONS = "ENUMERATE_RELATIONS"
    FILTER_SUBJECT = "FILTER_SUBJECT"
    FILTER_RELATION = "FILTER_RELATION"
    FILTER_ANSWER_SHAPE = "FILTER_ANSWER_SHAPE"
    FILTER_VALUE_KIND = "FILTER_VALUE_KIND"
    FILTER_TIME_SCOPE = "FILTER_TIME_SCOPE"
    FILTER_ATTRIBUTION = "FILTER_ATTRIBUTION"
    FILTER_SOURCE = "FILTER_SOURCE"
    SELECT_CLAIM = "SELECT_CLAIM"
    REJECT_CLAIM = "REJECT_CLAIM"
    SELECT_SOURCE = "SELECT_SOURCE"
    SELECT_ENTITY = "SELECT_ENTITY"
    BIND_LIST_SLOT = "BIND_LIST_SLOT"
    PAIR_COMPARISON_VALUES = "PAIR_COMPARISON_VALUES"
    JOIN_BY_ENTITY = "JOIN_BY_ENTITY"
    JOIN_BY_EVENT = "JOIN_BY_EVENT"
    ORDER_TEMPORAL = "ORDER_TEMPORAL"
    COMPARE_VALUES = "COMPARE_VALUES"
    COUNT_VALUES = "COUNT_VALUES"
    NORMALIZE_QUANTITY = "NORMALIZE_QUANTITY"
    BUILD_DIRECT_PLAN = "BUILD_DIRECT_PLAN"
    BUILD_LIST_PLAN = "BUILD_LIST_PLAN"
    BUILD_COMPARISON_PLAN = "BUILD_COMPARISON_PLAN"
    BUILD_VERIFICATION_PLAN = "BUILD_VERIFICATION_PLAN"
    VERIFY_PLAN = "VERIFY_PLAN"
    ANSWER = "ANSWER"
    CLARIFY = "CLARIFY"
    ABSTAIN = "ABSTAIN"
    INCORRECT_PREMISE = "INCORRECT_PREMISE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    OUT_OF_CORPUS = "OUT_OF_CORPUS"


class MicroOpSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: int = Field(ge=0, le=255)
    name: MicroOpName
    preconditions: tuple[str, ...]
    argument_types: tuple[str, ...]
    input_state_slots: tuple[str, ...]
    output_state_slots: tuple[str, ...]
    cost_class: CostClass
    side_effects: tuple[str, ...]
    maximum_repeat_count: int = Field(ge=1, le=64)


def _spec(
    operation_id: int,
    name: MicroOpName,
    preconditions: tuple[str, ...],
    arguments: tuple[str, ...],
    inputs: tuple[str, ...],
    outputs: tuple[str, ...],
    *,
    cost: CostClass = "compute-only",
    repeats: int = 1,
) -> MicroOpSpec:
    return MicroOpSpec(
        operation_id=operation_id,
        name=name,
        preconditions=preconditions,
        argument_types=arguments,
        input_state_slots=inputs,
        output_state_slots=outputs,
        cost_class=cost,
        side_effects=tuple(f"state.{slot}" for slot in outputs),
        maximum_repeat_count=repeats,
    )


MICRO_OPERATIONS: tuple[MicroOpSpec, ...] = (
    _spec(32, MicroOpName.ENUMERATE_CLAIMS, ("claims",), (), ("claims",), ("active_claim_ids",)),
    _spec(
        33,
        MicroOpName.ENUMERATE_VALUES,
        ("active_claim_ids",),
        (),
        ("claims",),
        ("enumerated_values",),
    ),
    _spec(
        34, MicroOpName.ENUMERATE_ENTITIES, ("claims",), (), ("claims",), ("enumerated_entity_ids",)
    ),
    _spec(
        35, MicroOpName.ENUMERATE_RELATIONS, ("claims",), (), ("claims",), ("enumerated_relations",)
    ),
    _spec(
        36,
        MicroOpName.FILTER_SUBJECT,
        ("active_claim_ids", "frame_entities"),
        (),
        ("claims", "frame"),
        ("active_claim_ids",),
    ),
    _spec(
        37,
        MicroOpName.FILTER_RELATION,
        ("active_claim_ids", "frame_relations"),
        (),
        ("claims", "frame"),
        ("active_claim_ids",),
    ),
    _spec(
        38,
        MicroOpName.FILTER_ANSWER_SHAPE,
        ("active_claim_ids", "answer_shape"),
        (),
        ("claims", "frame"),
        ("active_claim_ids",),
    ),
    _spec(
        39,
        MicroOpName.FILTER_VALUE_KIND,
        ("active_claim_ids", "answer_shape"),
        (),
        ("claims", "frame"),
        ("active_claim_ids",),
    ),
    _spec(
        40,
        MicroOpName.FILTER_TIME_SCOPE,
        ("active_claim_ids", "time_constraints"),
        (),
        ("claims", "frame"),
        ("active_claim_ids",),
    ),
    _spec(
        41,
        MicroOpName.FILTER_ATTRIBUTION,
        ("active_claim_ids", "attribution_constraints"),
        (),
        ("claims", "frame"),
        ("active_claim_ids",),
    ),
    _spec(
        42,
        MicroOpName.FILTER_SOURCE,
        ("active_claim_ids", "source_spans"),
        ("source_id:str",),
        ("claims", "source_spans"),
        ("active_claim_ids",),
    ),
    _spec(
        43,
        MicroOpName.SELECT_CLAIM,
        ("active_claim_ids",),
        ("claim_id:str",),
        ("claims",),
        ("selected_claim_ids",),
        repeats=8,
    ),
    _spec(
        44,
        MicroOpName.REJECT_CLAIM,
        ("active_claim_ids",),
        ("claim_id:str",),
        ("claims",),
        ("rejected_claim_ids", "active_claim_ids"),
        repeats=16,
    ),
    _spec(
        45,
        MicroOpName.SELECT_SOURCE,
        ("source_spans",),
        ("source_id:str",),
        ("source_spans",),
        ("selected_source_ids",),
        repeats=8,
    ),
    _spec(
        46,
        MicroOpName.SELECT_ENTITY,
        ("enumerated_entity_ids",),
        ("entity_id:str",),
        ("claims",),
        ("selected_entity_ids",),
        repeats=4,
    ),
    _spec(
        47,
        MicroOpName.BIND_LIST_SLOT,
        ("selected_claim_ids",),
        ("claim_id:str",),
        ("claims",),
        ("bound_claim_ids",),
        repeats=16,
    ),
    _spec(
        48,
        MicroOpName.PAIR_COMPARISON_VALUES,
        ("selected_claim_ids",),
        (),
        ("claims",),
        ("bound_claim_ids",),
    ),
    _spec(
        49,
        MicroOpName.JOIN_BY_ENTITY,
        ("active_claim_ids",),
        ("entity_id:str",),
        ("claims",),
        ("active_claim_ids",),
    ),
    _spec(
        50,
        MicroOpName.JOIN_BY_EVENT,
        ("active_claim_ids",),
        ("event:str",),
        ("claims",),
        ("active_claim_ids",),
    ),
    _spec(
        51,
        MicroOpName.ORDER_TEMPORAL,
        ("active_claim_ids",),
        (),
        ("claims",),
        ("active_claim_ids",),
    ),
    _spec(
        52, MicroOpName.COMPARE_VALUES, ("bound_claim_ids",), (), ("claims",), ("derived_values",)
    ),
    _spec(
        53, MicroOpName.COUNT_VALUES, ("active_claim_ids",), (), ("claims",), ("derived_values",)
    ),
    _spec(
        54,
        MicroOpName.NORMALIZE_QUANTITY,
        ("selected_claim_ids",),
        (),
        ("claims",),
        ("derived_values",),
    ),
    _spec(
        55,
        MicroOpName.BUILD_DIRECT_PLAN,
        ("selected_claim_ids",),
        (),
        ("claims",),
        ("plan_values", "plan_claim_ids"),
    ),
    _spec(
        56,
        MicroOpName.BUILD_LIST_PLAN,
        ("bound_claim_ids",),
        (),
        ("claims",),
        ("plan_values", "plan_claim_ids"),
    ),
    _spec(
        57,
        MicroOpName.BUILD_COMPARISON_PLAN,
        ("bound_claim_ids", "derived_values"),
        (),
        ("claims",),
        ("plan_values", "plan_claim_ids"),
    ),
    _spec(
        58,
        MicroOpName.BUILD_VERIFICATION_PLAN,
        ("plan_values",),
        (),
        ("claims", "source_spans"),
        ("plan_source_ids",),
    ),
    _spec(
        59,
        MicroOpName.VERIFY_PLAN,
        ("plan_values", "plan_claim_ids"),
        (),
        ("claims", "source_spans"),
        ("verification_passed",),
    ),
    _spec(
        60,
        MicroOpName.ANSWER,
        ("verification_passed",),
        (),
        ("plan_values",),
        ("terminal", "answer_values"),
        cost="free",
    ),
    _spec(
        61, MicroOpName.CLARIFY, ("clarification_need",), (), ("frame",), ("terminal",), cost="free"
    ),
    _spec(62, MicroOpName.ABSTAIN, (), (), ("frame",), ("terminal",), cost="free"),
    _spec(
        63,
        MicroOpName.INCORRECT_PREMISE,
        ("premise_refuted",),
        (),
        ("frame",),
        ("terminal",),
        cost="free",
    ),
    _spec(
        64,
        MicroOpName.CONFLICTING_EVIDENCE,
        ("contradictions",),
        (),
        ("claims",),
        ("terminal",),
        cost="free",
    ),
    _spec(
        65,
        MicroOpName.OUT_OF_CORPUS,
        ("out_of_corpus",),
        (),
        ("frame",),
        ("terminal",),
        cost="free",
    ),
)

MICRO_OPS_BY_ID = {operation.operation_id: operation for operation in MICRO_OPERATIONS}
MICRO_OPS_BY_NAME = {operation.name: operation for operation in MICRO_OPERATIONS}
if len(MICRO_OPS_BY_ID) != len(MICRO_OPERATIONS):
    raise AssertionError("micro-operation id collision")

# Disabled schema reservations.  They intentionally do not appear in the legal registry.
RESERVED_DISABLED_MICRO_OP_IDS = {
    "PERSONAL_MEMORY_LOOKUP": 96,
    "MEMORY_WRITE_USER_ASSERTED": 97,
}


class MicroAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: int
    arguments: dict[str, str] = Field(default_factory=dict)


class MicroState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    frame: dict[str, Any]
    claims: tuple[dict[str, Any], ...]
    source_spans: tuple[dict[str, Any], ...]
    active_claim_ids: tuple[str, ...] = ()
    enumerated_values: tuple[str, ...] = ()
    enumerated_entity_ids: tuple[str, ...] = ()
    enumerated_relations: tuple[str, ...] = ()
    selected_claim_ids: tuple[str, ...] = ()
    rejected_claim_ids: tuple[str, ...] = ()
    selected_source_ids: tuple[str, ...] = ()
    selected_entity_ids: tuple[str, ...] = ()
    bound_claim_ids: tuple[str, ...] = ()
    derived_values: tuple[str, ...] = ()
    plan_values: tuple[str, ...] = ()
    plan_claim_ids: tuple[str, ...] = ()
    plan_source_ids: tuple[str, ...] = ()
    plan_shape: str | None = None
    plan_answer_text: str | None = None
    verification_passed: bool = False
    terminal: str | None = None
    answer_values: tuple[str, ...] = ()
    operation_counts: dict[int, int] = Field(default_factory=dict)
    read_actions: int = 0
    total_actions: int = 0


def state_from_replay(case: ReplayCase) -> MicroState:
    if not case.replay_complete:
        raise ValueError(f"replay case {case.case_id} incomplete: {case.incompleteness_reasons}")
    # Choose the latest richest replay checkpoint.  The old max(claim-count)
    # tie-break selected step zero for claimless dispositions and silently
    # discarded the query frame present at later decisions.
    decision = max(
        case.decisions,
        key=lambda item: (
            len(item.structured_claims),
            len(item.source_spans),
            bool(item.query_frame),
            item.step_index,
        ),
    )
    return MicroState(
        case_id=case.case_id,
        frame=decision.query_frame,
        claims=decision.structured_claims,
        source_spans=decision.source_spans,
    )


def _claim_id(claim: dict[str, Any]) -> str:
    value = claim.get("claim_id")
    return str(value) if value is not None else ""


def _claim_value(claim: dict[str, Any], shape: str | None = None) -> str:
    keys = (
        ("quotation", "object_value")
        if shape == "quotation"
        else ("object_value", "quantity_value", "quotation")
    )
    for key in keys:
        value = claim.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _claim_map(state: MicroState) -> dict[str, dict[str, Any]]:
    return {_claim_id(claim): claim for claim in state.claims if _claim_id(claim)}


def _claim_can_pass_static_verifier(state: MicroState, claim: dict[str, Any]) -> bool:
    entities = set(_frame_strings(state.frame, "candidate_entity_ids"))
    if entities and not entities.intersection(
        {
            str(claim.get("subject_entity_id", "")),
            str(claim.get("object_entity_id", "")),
        }
    ):
        return False
    relations = set(_frame_strings(state.frame, "requested_relation_families"))
    if relations and str(claim.get("relation_family", "")) not in relations:
        return False
    shape = str(state.frame.get("answer_shape", ""))
    surface = _claim_value(claim, shape)
    spans = {str(span.get("span_id", "")): span for span in state.source_spans}
    return bool(surface) and any(
        surface in str(spans[source_id].get("text", ""))
        for source_id in claim.get("source_span_ids", ())
        if source_id in spans
    )


def verifier_eligible_claim_values(state: MicroState) -> tuple[str, ...]:
    """Exact copied surfaces that are not statically doomed by the verifier."""

    shape = str(state.frame.get("answer_shape", ""))
    return _unique(
        [
            _claim_value(claim, shape)
            for claim in state.claims
            if _claim_can_pass_static_verifier(state, claim)
        ]
    )


def _list_target(state: MicroState) -> int:
    mention_count = len(state.frame.get("entity_mentions", ()))
    return min(6, max(2, mention_count - 1 if mention_count > 2 else mention_count))


def static_verifier_answer_possible(state: MicroState) -> bool:
    """Whether any claim tuple can satisfy static exact-verifier constraints."""

    eligible = [claim for claim in state.claims if _claim_can_pass_static_verifier(state, claim)]
    shape = str(state.frame.get("answer_shape", ""))
    if shape not in {"list", "comparison"}:
        return bool(eligible)
    subjects = {str(claim.get("subject_entity_id", "")) for claim in eligible}
    if shape == "comparison":
        relations: dict[str, set[str]] = {}
        for claim in eligible:
            relations.setdefault(str(claim.get("relation_family", "")), set()).add(
                str(claim.get("subject_entity_id", ""))
            )
        return any(len(items) >= 2 for items in relations.values())
    span_families = {
        str(span.get("span_id", "")): str(span.get("source_family", ""))
        for span in state.source_spans
    }
    families = {
        tuple(
            sorted(
                {
                    span_families.get(str(source_id), "")
                    for source_id in claim.get("source_span_ids", ())
                }
            )
        )
        for claim in eligible
    }
    return len(subjects) >= _list_target(state) and len(families) >= _list_target(state)


def _unique(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _frame_strings(frame: dict[str, Any], key: str) -> tuple[str, ...]:
    value = frame.get(key, ())
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return ()


def _slot(state: MicroState, name: str) -> bool:
    if name == "frame_entities":
        return bool(_frame_strings(state.frame, "candidate_entity_ids"))
    if name == "frame_relations":
        return bool(_frame_strings(state.frame, "requested_relation_families"))
    if name == "answer_shape":
        return bool(state.frame.get("answer_shape"))
    if name == "time_constraints":
        return bool(_frame_strings(state.frame, "temporal_constraints"))
    if name == "attribution_constraints":
        return bool(_frame_strings(state.frame, "attribution_constraints"))
    if name == "clarification_need":
        return bool(state.frame.get("clarification_need"))
    if name == "premise_refuted":
        return state.frame.get("premise_status") == "REFUTED"
    if name == "contradictions":
        return bool(state.frame.get("contradictions"))
    if name == "out_of_corpus":
        return bool(state.frame.get("out_of_corpus"))
    value = getattr(state, name, None)
    return bool(value)


def legal_operation_specs(state: MicroState) -> tuple[MicroOpSpec, ...]:
    if state.terminal is not None:
        return ()
    legal = []
    for spec in MICRO_OPERATIONS:
        if state.operation_counts.get(spec.operation_id, 0) >= spec.maximum_repeat_count:
            continue
        if all(_slot(state, precondition) for precondition in spec.preconditions):
            legal.append(spec)
    return tuple(legal)


def legal_actions(state: MicroState, *, argument_cap: int = 16) -> tuple[MicroAction, ...]:
    """Enumerate typed legal actions without consulting benchmark gold."""

    actions: list[MicroAction] = []
    claims = _claim_map(state)
    span_ids = tuple(str(span.get("span_id", "")) for span in state.source_spans)
    specs = legal_operation_specs(state)
    if state.verification_passed:
        specs = tuple(spec for spec in specs if spec.name is MicroOpName.ANSWER)
    elif state.plan_values:
        specs = tuple(spec for spec in specs if spec.name is MicroOpName.VERIFY_PLAN)
    shape = str(state.frame.get("answer_shape", ""))
    list_target = _list_target(state)
    if not state.plan_values and shape == "comparison" and state.selected_claim_ids:
        if len(state.selected_claim_ids) < 2:
            wanted = {MicroOpName.SELECT_CLAIM}
        elif not state.bound_claim_ids:
            wanted = {MicroOpName.PAIR_COMPARISON_VALUES}
        elif not state.derived_values:
            wanted = {MicroOpName.COMPARE_VALUES}
        else:
            wanted = {MicroOpName.BUILD_COMPARISON_PLAN}
        specs = tuple(spec for spec in specs if spec.name in wanted)
    elif not state.plan_values and shape == "list" and state.selected_claim_ids:
        if len(state.selected_claim_ids) < list_target:
            wanted = {MicroOpName.SELECT_CLAIM}
        elif len(state.bound_claim_ids) < len(state.selected_claim_ids):
            wanted = {MicroOpName.BIND_LIST_SLOT}
        else:
            wanted = {MicroOpName.BUILD_LIST_PLAN}
        specs = tuple(spec for spec in specs if spec.name in wanted)
    elif not state.plan_values and state.selected_claim_ids and shape not in {"list", "comparison"}:
        specs = tuple(spec for spec in specs if spec.name is MicroOpName.BUILD_DIRECT_PLAN)
    for spec in specs:
        args: list[dict[str, str]] = [{}]
        if spec.name in {MicroOpName.SELECT_CLAIM, MicroOpName.REJECT_CLAIM}:
            ids = state.active_claim_ids
            if spec.name is MicroOpName.SELECT_CLAIM:
                ids = tuple(
                    item
                    for item in ids
                    if item not in state.selected_claim_ids
                    and _claim_can_pass_static_verifier(state, claims[item])
                )
                limit = 6 if shape == "list" else 2 if shape == "comparison" else 1
                if shape in {"list", "comparison"} and state.selected_claim_ids:
                    selected = [claims[item] for item in state.selected_claim_ids]
                    selected_subjects = {
                        str(claim.get("subject_entity_id", "")) for claim in selected
                    }
                    ids = tuple(
                        item
                        for item in ids
                        if str(claims[item].get("subject_entity_id", "")) not in selected_subjects
                    )
                    if shape == "comparison":
                        relation = str(selected[0].get("relation_family", ""))
                        ids = tuple(
                            item
                            for item in ids
                            if str(claims[item].get("relation_family", "")) == relation
                        )
                    if shape == "list":
                        span_families = {
                            str(span.get("span_id", "")): str(span.get("source_family", ""))
                            for span in state.source_spans
                        }

                        def families(
                            claim_id: str,
                            span_families: dict[str, str] = span_families,
                            claims: dict[str, dict[str, Any]] = claims,
                        ) -> tuple[str, ...]:
                            return tuple(
                                sorted(
                                    {
                                        span_families.get(str(source_id), "")
                                        for source_id in claims[claim_id].get("source_span_ids", ())
                                    }
                                )
                            )

                        selected_families = {
                            families(claim_id) for claim_id in state.selected_claim_ids
                        }
                        ids = tuple(item for item in ids if families(item) not in selected_families)
                if len(state.selected_claim_ids) >= limit:
                    ids = ()
            args = [{"claim_id": item} for item in ids[:argument_cap]]
        elif spec.name is MicroOpName.BIND_LIST_SLOT:
            args = [
                {"claim_id": item}
                for item in state.selected_claim_ids[:argument_cap]
                if item not in state.bound_claim_ids
            ]
        elif spec.name is MicroOpName.SELECT_SOURCE:
            args = [{"source_id": item} for item in span_ids[:argument_cap] if item]
        elif spec.name is MicroOpName.FILTER_SOURCE:
            source_ids = _unique(
                [
                    str(source_id)
                    for claim_id in state.active_claim_ids
                    for source_id in claims.get(claim_id, {}).get("source_span_ids", ())
                ]
            )
            args = [{"source_id": item} for item in source_ids[:argument_cap]]
        elif spec.name in {MicroOpName.SELECT_ENTITY, MicroOpName.JOIN_BY_ENTITY}:
            args = [{"entity_id": item} for item in state.enumerated_entity_ids[:argument_cap]]
        elif spec.name is MicroOpName.JOIN_BY_EVENT:
            events = _unique(
                [str(claims[item].get("occurred_at", "")) for item in state.active_claim_ids]
            )
            args = [{"event": item} for item in events[:argument_cap]]
        # Shape-incompatible plan constructors only create dead branches.
        shape = str(state.frame.get("answer_shape", ""))
        incompatible_plan = (
            (spec.name is MicroOpName.BUILD_DIRECT_PLAN and shape in {"list", "comparison"})
            or (spec.name is MicroOpName.BUILD_LIST_PLAN and shape != "list")
            or (spec.name is MicroOpName.BUILD_COMPARISON_PLAN and shape != "comparison")
        )
        if incompatible_plan:
            args = []
        for arguments in args:
            actions.append(MicroAction(operation_id=spec.operation_id, arguments=arguments))
    return tuple(actions)


def _value_kind(value: str) -> str:
    lowered = value.strip().lower()
    if re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", lowered):
        return "date"
    if re.fullmatch(r"[-+]?\d[\d,.]*(\s*[a-z%²/]+)?", lowered):
        return "quantity"
    return "text"


def _numeric(value: str) -> float | None:
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", value)
    if match is None:
        return None
    return float(match.group().replace(",", ""))


def _apply_count(state: MicroState, operation_id: int, cost: CostClass) -> dict[str, Any]:
    counts = dict(state.operation_counts)
    counts[operation_id] = counts.get(operation_id, 0) + 1
    return {
        "operation_counts": counts,
        "read_actions": state.read_actions + (1 if cost == "read-bearing" else 0),
        "total_actions": state.total_actions + 1,
    }


def execute_action(state: MicroState, action: MicroAction) -> MicroState:
    spec = MICRO_OPS_BY_ID.get(action.operation_id)
    if spec is None or spec not in legal_operation_specs(state):
        raise ValueError(f"illegal micro-operation {action.operation_id}")
    claims = _claim_map(state)
    updates: dict[str, Any] = _apply_count(state, spec.operation_id, spec.cost_class)
    name = spec.name
    if name is MicroOpName.ENUMERATE_CLAIMS:
        updates["active_claim_ids"] = tuple(claims)
    elif name is MicroOpName.ENUMERATE_VALUES:
        updates["enumerated_values"] = _unique(
            [
                _claim_value(claims[item], str(state.frame.get("answer_shape", "")))
                for item in state.active_claim_ids
            ]
        )
    elif name is MicroOpName.ENUMERATE_ENTITIES:
        updates["enumerated_entity_ids"] = _unique(
            [
                str(claim.get(key, ""))
                for claim in state.claims
                for key in (
                    "subject_entity_id",
                    "object_entity_id",
                    "location_entity_id",
                    "speaker_entity_id",
                )
            ]
        )
    elif name is MicroOpName.ENUMERATE_RELATIONS:
        updates["enumerated_relations"] = _unique(
            [str(claim.get("relation_family", "")) for claim in state.claims]
        )
    elif name is MicroOpName.FILTER_SUBJECT:
        wanted = set(_frame_strings(state.frame, "candidate_entity_ids"))
        updates["active_claim_ids"] = tuple(
            item
            for item in state.active_claim_ids
            if claims[item].get("subject_entity_id") in wanted
        )
    elif name is MicroOpName.FILTER_RELATION:
        wanted = set(_frame_strings(state.frame, "requested_relation_families"))
        updates["active_claim_ids"] = tuple(
            item for item in state.active_claim_ids if claims[item].get("relation_family") in wanted
        )
    elif name in {MicroOpName.FILTER_ANSWER_SHAPE, MicroOpName.FILTER_VALUE_KIND}:
        shape = str(state.frame.get("answer_shape", ""))
        allowed = "date" if shape == "date" else "quantity" if shape == "quantity" else "text"
        updates["active_claim_ids"] = tuple(
            item
            for item in state.active_claim_ids
            if (
                str(claims[item].get("answer_shape", "")) == shape
                if name is MicroOpName.FILTER_ANSWER_SHAPE
                else _value_kind(_claim_value(claims[item], shape)) == allowed
            )
        )
    elif name is MicroOpName.FILTER_TIME_SCOPE:
        time_wanted = _frame_strings(state.frame, "temporal_constraints")
        updates["active_claim_ids"] = tuple(
            item
            for item in state.active_claim_ids
            if any(token in str(claims[item].get("occurred_at", "")) for token in time_wanted)
        )
    elif name is MicroOpName.FILTER_ATTRIBUTION:
        wanted = set(_frame_strings(state.frame, "attribution_constraints"))
        updates["active_claim_ids"] = tuple(
            item
            for item in state.active_claim_ids
            if str(claims[item].get("speaker_entity_id", "")) in wanted
        )
    elif name is MicroOpName.FILTER_SOURCE:
        source_wanted = action.arguments.get("source_id", "")
        updates["active_claim_ids"] = tuple(
            item
            for item in state.active_claim_ids
            if source_wanted in claims[item].get("source_span_ids", ())
        )
    elif name is MicroOpName.SELECT_CLAIM:
        claim_id = action.arguments.get("claim_id", "")
        if claim_id not in state.active_claim_ids:
            raise ValueError("claim is not active")
        if claim_id in state.selected_claim_ids:
            raise ValueError("claim is already selected")
        updates["selected_claim_ids"] = _unique([*state.selected_claim_ids, claim_id])
    elif name is MicroOpName.REJECT_CLAIM:
        claim_id = action.arguments.get("claim_id", "")
        if claim_id not in state.active_claim_ids:
            raise ValueError("claim is not active")
        updates["rejected_claim_ids"] = _unique([*state.rejected_claim_ids, claim_id])
        updates["active_claim_ids"] = tuple(
            item for item in state.active_claim_ids if item != claim_id
        )
    elif name is MicroOpName.SELECT_SOURCE:
        source_id = action.arguments.get("source_id", "")
        if source_id not in {str(span.get("span_id", "")) for span in state.source_spans}:
            raise ValueError("unknown source span")
        updates["selected_source_ids"] = _unique([*state.selected_source_ids, source_id])
    elif name is MicroOpName.SELECT_ENTITY:
        entity_id = action.arguments.get("entity_id", "")
        if entity_id not in state.enumerated_entity_ids:
            raise ValueError("entity is not enumerated")
        updates["selected_entity_ids"] = _unique([*state.selected_entity_ids, entity_id])
    elif name is MicroOpName.BIND_LIST_SLOT:
        claim_id = action.arguments.get("claim_id", "")
        if claim_id not in state.selected_claim_ids:
            raise ValueError("list slot requires a selected claim")
        if claim_id in state.bound_claim_ids:
            raise ValueError("list slot is already bound")
        updates["bound_claim_ids"] = _unique([*state.bound_claim_ids, claim_id])
    elif name is MicroOpName.PAIR_COMPARISON_VALUES:
        if len(state.selected_claim_ids) < 2:
            raise ValueError("comparison requires two selected claims")
        updates["bound_claim_ids"] = state.selected_claim_ids[:2]
    elif name is MicroOpName.JOIN_BY_ENTITY:
        entity_id = action.arguments.get("entity_id", "")
        updates["active_claim_ids"] = tuple(
            item
            for item in state.active_claim_ids
            if entity_id
            in {claims[item].get("subject_entity_id"), claims[item].get("object_entity_id")}
        )
    elif name is MicroOpName.JOIN_BY_EVENT:
        event = action.arguments.get("event", "")
        updates["active_claim_ids"] = tuple(
            item
            for item in state.active_claim_ids
            if str(claims[item].get("occurred_at", "")) == event
        )
    elif name is MicroOpName.ORDER_TEMPORAL:
        updates["active_claim_ids"] = tuple(
            sorted(
                state.active_claim_ids,
                key=lambda item: (str(claims[item].get("occurred_at", "")), item),
            )
        )
    elif name is MicroOpName.COMPARE_VALUES:
        if len(state.bound_claim_ids) != 2:
            raise ValueError("comparison requires exactly two bound claims")
        left = _numeric(_claim_value(claims[state.bound_claim_ids[0]], "comparison"))
        right = _numeric(_claim_value(claims[state.bound_claim_ids[1]], "comparison"))
        if left is None or right is None:
            raise ValueError("comparison values are not numeric")
        updates["derived_values"] = ("<" if left < right else ">" if left > right else "=",)
    elif name is MicroOpName.COUNT_VALUES:
        updates["derived_values"] = (
            str(len(_unique([_claim_value(claims[item]) for item in state.active_claim_ids]))),
        )
    elif name is MicroOpName.NORMALIZE_QUANTITY:
        values = [
            _claim_value(claims[item]).replace(",", "").strip() for item in state.selected_claim_ids
        ]
        updates["derived_values"] = _unique(values)
    elif name is MicroOpName.BUILD_DIRECT_PLAN:
        claim_id = state.selected_claim_ids[0]
        shape = str(state.frame.get("answer_shape", ""))
        updates.update(
            plan_values=(_claim_value(claims[claim_id], shape),),
            plan_claim_ids=(claim_id,),
            plan_shape=shape,
        )
    elif name is MicroOpName.BUILD_LIST_PLAN:
        updates.update(
            plan_values=tuple(_claim_value(claims[item]) for item in state.bound_claim_ids),
            plan_claim_ids=state.bound_claim_ids,
            plan_shape="list",
        )
    elif name is MicroOpName.BUILD_COMPARISON_PLAN:
        comparison_values = tuple(
            _claim_value(claims[item], "comparison") for item in state.bound_claim_ids
        )
        updates.update(
            plan_values=comparison_values,
            plan_claim_ids=state.bound_claim_ids,
            plan_shape="comparison",
        )
    elif name is MicroOpName.BUILD_VERIFICATION_PLAN:
        updates["plan_source_ids"] = _unique(
            [
                str(source_id)
                for item in state.plan_claim_ids
                for source_id in claims[item].get("source_span_ids", ())
            ]
        )
    elif name is MicroOpName.VERIFY_PLAN:
        source_ids = _unique(
            [
                str(source_id)
                for item in state.plan_claim_ids
                for source_id in claims[item].get("source_span_ids", ())
            ]
        )
        updates["plan_source_ids"] = source_ids
        try:
            frame = QueryFrame.model_validate(state.frame)
            graph_claims = tuple(StructuredClaim.model_validate(item) for item in state.claims)
            spans = tuple(ExactSourceSpan.model_validate(item) for item in state.source_spans)
            graph = EvidenceGraph(
                query_id=state.case_id,
                entities=frame.candidate_entity_ids,
                claims=graph_claims,
                source_spans=spans,
                source_families=tuple(dict.fromkeys(span.source_family for span in spans)),
                contradictions=(),
                required_facets=frame.required_facets,
                missing_facets=(),
            )
            shape = AnswerShape(state.plan_shape or frame.answer_shape.value)
            if shape is AnswerShape.COMPARISON:
                operator = state.derived_values[0] if state.derived_values else None
                answer_text = f"{state.plan_values[0]} {operator} {state.plan_values[1]}"
            else:
                operator = None
                answer_text = "; ".join(state.plan_values)
            selection = AnswerSelection(
                answer_text=answer_text,
                answer_shape=shape,
                selected_claim_ids=state.plan_claim_ids,
                selected_source_span_ids=source_ids,
                confidence=1.0,
            )
            plan = make_answer_plan(selection, graph)
            realized = realize_plan(plan)
            report = verify_realization(frame, graph, plan, realized)
            updates["verification_passed"] = report.passed
            if report.passed:
                updates["plan_values"] = tuple(claim.surface for claim in plan.planned_claims)
                updates["plan_answer_text"] = realized.text
        except (KeyError, IndexError, TypeError, ValueError):
            updates["verification_passed"] = False
    elif name is MicroOpName.ANSWER:
        terminal_values = (
            (state.plan_answer_text,)
            if state.plan_shape == "comparison" and state.plan_answer_text
            else state.plan_values
        )
        updates.update(terminal="ANSWER", answer_values=terminal_values)
    elif name in {
        MicroOpName.CLARIFY,
        MicroOpName.ABSTAIN,
        MicroOpName.INCORRECT_PREMISE,
        MicroOpName.CONFLICTING_EVIDENCE,
        MicroOpName.OUT_OF_CORPUS,
    }:
        updates["terminal"] = name.value
    return state.model_copy(update=updates)
