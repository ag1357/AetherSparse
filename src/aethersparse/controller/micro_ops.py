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

from aethersparse.controller.replay import ReplayCase

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
    verification_passed: bool = False
    terminal: str | None = None
    answer_values: tuple[str, ...] = ()
    operation_counts: dict[int, int] = Field(default_factory=dict)
    read_actions: int = 0
    total_actions: int = 0


def state_from_replay(case: ReplayCase) -> MicroState:
    if not case.replay_complete:
        raise ValueError(f"replay case {case.case_id} incomplete: {case.incompleteness_reasons}")
    decision = max(case.decisions, key=lambda item: len(item.structured_claims))
    return MicroState(
        case_id=case.case_id,
        frame=decision.query_frame,
        claims=decision.structured_claims,
        source_spans=decision.source_spans,
    )


def _claim_id(claim: dict[str, Any]) -> str:
    value = claim.get("claim_id")
    return str(value) if value is not None else ""


def _claim_value(claim: dict[str, Any]) -> str:
    for key in ("quantity_value", "quotation", "object_value"):
        value = claim.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _claim_map(state: MicroState) -> dict[str, dict[str, Any]]:
    return {_claim_id(claim): claim for claim in state.claims if _claim_id(claim)}


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
    for spec in legal_operation_specs(state):
        args: list[dict[str, str]] = [{}]
        if spec.name in {MicroOpName.SELECT_CLAIM, MicroOpName.REJECT_CLAIM}:
            args = [{"claim_id": item} for item in state.active_claim_ids[:argument_cap]]
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
            [_claim_value(claims[item]) for item in state.active_claim_ids]
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
                else _value_kind(_claim_value(claims[item])) == allowed
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
        left = _numeric(_claim_value(claims[state.bound_claim_ids[0]]))
        right = _numeric(_claim_value(claims[state.bound_claim_ids[1]]))
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
        updates.update(plan_values=(_claim_value(claims[claim_id]),), plan_claim_ids=(claim_id,))
    elif name is MicroOpName.BUILD_LIST_PLAN:
        updates.update(
            plan_values=tuple(_claim_value(claims[item]) for item in state.bound_claim_ids),
            plan_claim_ids=state.bound_claim_ids,
        )
    elif name is MicroOpName.BUILD_COMPARISON_PLAN:
        updates.update(plan_values=state.derived_values, plan_claim_ids=state.bound_claim_ids)
    elif name is MicroOpName.BUILD_VERIFICATION_PLAN:
        updates["plan_source_ids"] = _unique(
            [
                str(source_id)
                for item in state.plan_claim_ids
                for source_id in claims[item].get("source_span_ids", ())
            ]
        )
    elif name is MicroOpName.VERIFY_PLAN:
        known_spans = {str(span.get("span_id", "")) for span in state.source_spans}
        source_ids = _unique(
            [
                str(source_id)
                for item in state.plan_claim_ids
                for source_id in claims[item].get("source_span_ids", ())
            ]
        )
        values_exist = all(item and item in claims for item in state.plan_claim_ids)
        source_bound = bool(source_ids) and set(source_ids) <= known_spans
        updates["plan_source_ids"] = source_ids
        updates["verification_passed"] = values_exist and source_bound and bool(state.plan_values)
    elif name is MicroOpName.ANSWER:
        updates.update(terminal="ANSWER", answer_values=state.plan_values)
    elif name in {
        MicroOpName.CLARIFY,
        MicroOpName.ABSTAIN,
        MicroOpName.INCORRECT_PREMISE,
        MicroOpName.CONFLICTING_EVIDENCE,
        MicroOpName.OUT_OF_CORPUS,
    }:
        updates["terminal"] = name.value
    return state.model_copy(update=updates)
