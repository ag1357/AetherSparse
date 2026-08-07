"""Operator registry for the answer controller (Mission 4 Amendment A1).

Every deterministic operator declares a static schema.  ``operator_id`` is a
stable u8, never reused.  ``typed_preconditions`` names the state slots that
must exist for the operator to be legal; that makes the legal-action set at
any state computable, which is what later makes trajectory search tractable.

This module is metadata only.  It is consumed by the diagnostic tracer
(``aethersparse.controller.trace``) and never alters controller behavior.
"""

from __future__ import annotations

from typing import Literal

from aethersparse.selection.models import FrozenModel

CostClass = Literal["read-bearing", "compute-only", "free"]


class OperatorSpec(FrozenModel):
    """Static schema for one deterministic controller operator."""

    operator_id: int
    """Stable u8, never reused."""
    name: str
    typed_preconditions: tuple[str, ...]
    """State slots that must exist for this op to be legal."""
    input_types: tuple[str, ...]
    """Typed slots consumed."""
    output_types: tuple[str, ...]
    """Typed slots produced."""
    side_effects: tuple[str, ...] = ()
    """State mutations, if any."""
    cost_class: CostClass = "compute-only"

    def model_post_init(self, __context: object, /) -> None:
        if not 0 <= self.operator_id <= 255:
            raise ValueError("operator_id must fit u8")


# State slots used by preconditions (see trace.state_view):
#   query, pool, ranking, frame, entity_bindings, evidence_records, claims,
#   selection, plan, realized, verification, disposition
#
# Phase 3/4 add typed enumeration / binding / composition operators with fresh
# ids.  Never renumber existing rows.

OPERATORS: tuple[OperatorSpec, ...] = (
    OperatorSpec(
        operator_id=1,
        name="candidate_generation",
        typed_preconditions=("query",),
        input_types=("query",),
        output_types=("candidate_pool",),
        side_effects=("state.pool",),
        cost_class="read-bearing",
    ),
    OperatorSpec(
        operator_id=2,
        name="evidence_ranking",
        typed_preconditions=("pool",),
        input_types=("candidate_pool",),
        output_types=("ranked_evidence",),
        side_effects=("state.ranking",),
        cost_class="compute-only",
    ),
    OperatorSpec(
        operator_id=3,
        name="frame_parsing",
        typed_preconditions=("query",),
        input_types=("query",),
        output_types=("query_frame",),
        side_effects=("state.frame",),
        cost_class="free",
    ),
    OperatorSpec(
        operator_id=4,
        name="entity_linking",
        typed_preconditions=("frame",),
        input_types=("query_frame",),
        output_types=("entity_bindings",),
        side_effects=("state.entity_bindings",),
        cost_class="read-bearing",
    ),
    OperatorSpec(
        operator_id=5,
        name="evidence_graph_build",
        typed_preconditions=("frame", "ranking"),
        input_types=("query_frame", "ranked_evidence"),
        output_types=("claims", "spans", "facet_coverage"),
        side_effects=("state.claims",),
        cost_class="compute-only",
    ),
    OperatorSpec(
        operator_id=6,
        name="answer_selection",
        typed_preconditions=("claims",),
        input_types=("claims", "query_frame"),
        output_types=("answer_selection",),
        side_effects=("state.selection",),
        cost_class="compute-only",
    ),
    OperatorSpec(
        operator_id=7,
        name="answer_planning",
        typed_preconditions=("selection",),
        input_types=("answer_selection", "claims"),
        output_types=("answer_plan",),
        side_effects=("state.plan",),
        cost_class="compute-only",
    ),
    OperatorSpec(
        operator_id=8,
        name="realization",
        typed_preconditions=("plan",),
        input_types=("answer_plan",),
        output_types=("realized_answer",),
        side_effects=("state.realized",),
        cost_class="free",
    ),
    OperatorSpec(
        operator_id=9,
        name="verification",
        typed_preconditions=("plan", "realized"),
        input_types=("answer_plan", "realized_answer", "claims"),
        output_types=("verification_report",),
        side_effects=("state.verification",),
        cost_class="compute-only",
    ),
    OperatorSpec(
        operator_id=10,
        name="disposition",
        typed_preconditions=("frame",),
        input_types=("query_frame", "answer_selection", "verification_report"),
        output_types=("disposition",),
        side_effects=("state.disposition",),
        cost_class="free",
    ),
)

OPERATORS_BY_ID: dict[int, OperatorSpec] = {op.operator_id: op for op in OPERATORS}

if len(OPERATORS_BY_ID) != len(OPERATORS):
    raise AssertionError("operator_id collision in registry")
