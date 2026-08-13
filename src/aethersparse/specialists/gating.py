"""Adaptive specialist routing and depth control from pre-outcome signals."""

from __future__ import annotations

import hashlib
import json
import math
from enum import StrEnum

from pydantic import Field, model_validator

from aethersparse.controller.models import FrozenModel
from aethersparse.specialists.workspace import SharedWorkspace, VerifierState


class GateFeatureVector(FrozenModel):
    """Normalized signals available before final correctness is known."""

    belief_entropy: float = Field(ge=0.0, le=1.0)
    expert_disagreement: float = Field(ge=0.0, le=1.0)
    entity_ambiguity: float = Field(ge=0.0, le=1.0)
    missing_facet_fraction: float = Field(ge=0.0, le=1.0)
    query_complexity: float = Field(ge=0.0, le=1.0)
    composition_required: float = Field(ge=0.0, le=1.0)
    discourse_ambiguity: float = Field(ge=0.0, le=1.0)
    verifier_rejected: float = Field(ge=0.0, le=1.0)
    novel_route: float = Field(ge=0.0, le=1.0)
    cycle_fraction: float = Field(ge=0.0, le=1.0)
    compute_budget_fraction: float = Field(ge=0.0, le=1.0)

    def dense(self) -> tuple[float, ...]:
        return tuple(float(value) for value in self.model_dump().values())


class LinearGateParameters(FrozenModel):
    """Fitted linear expected-benefit model for one specialist."""

    expert_id: str
    weights: tuple[float, ...]
    bias: float = 0.0
    calibration_temperature: float = Field(default=1.0, gt=0.0)
    parameter_count: int = Field(default=12, ge=0)

    @model_validator(mode="after")
    def feature_width_matches(self) -> LinearGateParameters:
        expected = len(GateFeatureVector.model_fields)
        if len(self.weights) != expected:
            raise ValueError(f"linear gate requires exactly {expected} feature weights")
        return self

    def expected_gain(self, features: GateFeatureVector) -> float:
        logit = self.bias + sum(
            weight * value for weight, value in zip(self.weights, features.dense(), strict=True)
        )
        scaled = max(-60.0, min(60.0, logit / self.calibration_temperature))
        return 1.0 / (1.0 + math.exp(-scaled))


class HaltReason(StrEnum):
    EXPECTED_GAIN_BELOW_GATE = "expected_gain_below_gate"
    CYCLE_BOUND_REACHED = "cycle_bound_reached"
    COMPUTE_BUDGET_EXHAUSTED = "compute_budget_exhausted"
    ROUTE_SELECTED = "route_selected"


class SpecialistProposal(FrozenModel):
    expert_id: str
    expected_correctness_gain: float = Field(ge=0.0, le=1.0)
    active_parameters: int = Field(ge=0)
    active_macs: int = Field(ge=0)
    read_operations: int = Field(default=0, ge=0)
    dependencies: tuple[str, ...] = ()


class DepthBounds(FrozenModel):
    max_cycles: int = Field(default=6, ge=1, le=6)
    max_parallel_specialists: int = Field(default=3, ge=1, le=3)
    max_total_active_macs: int = Field(gt=0)
    max_total_read_operations: int = Field(ge=0)
    minimum_expected_gain: float = Field(default=0.01, ge=0.0, le=1.0)


class RouteDecision(FrozenModel):
    parallel_groups: tuple[tuple[str, ...], ...]
    expected_gain_by_expert: tuple[tuple[str, float], ...]
    active_parameters: int = Field(ge=0)
    active_macs: int = Field(ge=0)
    read_operations: int = Field(ge=0)
    halt: bool
    halt_reason: HaltReason
    route_signature: str
    route_sha256: str


class AdaptiveDepthController:
    """Bounded lexicographic router: benefit first, compute only breaks ties."""

    def __init__(self, bounds: DepthBounds) -> None:
        self.bounds = bounds

    def route(
        self,
        workspace: SharedWorkspace,
        proposals: tuple[SpecialistProposal, ...],
    ) -> RouteDecision:
        if (
            workspace.cycle_count >= self.bounds.max_cycles
            or workspace.compute_budget.cycles_remaining == 0
        ):
            return self._halt(HaltReason.CYCLE_BOUND_REACHED, proposals)
        mac_budget = min(
            workspace.compute_budget.active_macs_remaining,
            self.bounds.max_total_active_macs,
        )
        read_budget = min(
            workspace.compute_budget.read_operations_remaining,
            self.bounds.max_total_read_operations,
        )
        eligible = tuple(
            proposal
            for proposal in proposals
            if proposal.expected_correctness_gain >= self.bounds.minimum_expected_gain
        )
        if not eligible:
            return self._halt(HaltReason.EXPECTED_GAIN_BELOW_GATE, proposals)
        by_id = {proposal.expert_id: proposal for proposal in eligible}
        pending = set(by_id)
        completed: set[str] = set()
        groups: list[tuple[str, ...]] = []
        selected: list[SpecialistProposal] = []
        macs = 0
        reads = 0
        while pending and len(groups) < min(
            self.bounds.max_cycles - workspace.cycle_count,
            workspace.compute_budget.cycles_remaining,
        ):
            available = [
                by_id[expert_id]
                for expert_id in pending
                if set(by_id[expert_id].dependencies).issubset(completed)
            ]
            available.sort(
                key=lambda item: (
                    -item.expected_correctness_gain,
                    item.active_macs,
                    item.read_operations,
                    item.expert_id,
                )
            )
            group: list[SpecialistProposal] = []
            for proposal in available:
                if len(group) >= self.bounds.max_parallel_specialists:
                    break
                if macs + proposal.active_macs > mac_budget:
                    continue
                if reads + proposal.read_operations > read_budget:
                    continue
                group.append(proposal)
                macs += proposal.active_macs
                reads += proposal.read_operations
            if not group:
                break
            group_ids = tuple(item.expert_id for item in group)
            groups.append(group_ids)
            selected.extend(group)
            completed.update(group_ids)
            pending.difference_update(group_ids)
        if not selected:
            return self._halt(HaltReason.COMPUTE_BUDGET_EXHAUSTED, proposals)
        signature, digest = route_signature(tuple(groups))
        return RouteDecision(
            parallel_groups=tuple(groups),
            expected_gain_by_expert=tuple(
                (item.expert_id, item.expected_correctness_gain) for item in selected
            ),
            active_parameters=sum(item.active_parameters for item in selected),
            active_macs=macs,
            read_operations=reads,
            halt=False,
            halt_reason=HaltReason.ROUTE_SELECTED,
            route_signature=signature,
            route_sha256=digest,
        )

    def _halt(
        self, reason: HaltReason, proposals: tuple[SpecialistProposal, ...]
    ) -> RouteDecision:
        signature, digest = route_signature(())
        return RouteDecision(
            parallel_groups=(),
            expected_gain_by_expert=tuple(
                (item.expert_id, item.expected_correctness_gain) for item in proposals
            ),
            active_parameters=0,
            active_macs=0,
            read_operations=0,
            halt=True,
            halt_reason=reason,
            route_signature=signature,
            route_sha256=digest,
        )


def route_signature(groups: tuple[tuple[str, ...], ...]) -> tuple[str, str]:
    lines = [f"C{cycle}:" + ",".join(group) for cycle, group in enumerate(groups)]
    lines.append("HALT")
    signature = "\n".join(lines)
    payload = json.dumps(groups, separators=(",", ":"), sort_keys=False).encode()
    return signature, hashlib.sha256(payload).hexdigest()


class CounterfactualDepthRecord(FrozenModel):
    """Training-only outcome record for expected value of computation."""

    case_id: str
    partition: str
    halt_supported_correct: bool
    plus_one_supported_correct: bool
    plus_two_supported_correct: bool | None = None
    improving_specialist: str | None = None
    incremental_active_macs: int = Field(ge=0)
    incremental_read_operations: int = Field(ge=0)

    @model_validator(mode="after")
    def training_partition_only(self) -> CounterfactualDepthRecord:
        if self.partition not in {"development", "tuning"}:
            raise ValueError("counterfactual depth labels are training/calibration only")
        return self

    @property
    def correctness_gain(self) -> int:
        best_deeper = self.plus_one_supported_correct or bool(self.plus_two_supported_correct)
        return int(best_deeper) - int(self.halt_supported_correct)


def features_from_workspace(
    workspace: SharedWorkspace,
    *,
    entity_ambiguity: float,
    query_complexity: float,
    composition_required: bool,
    discourse_ambiguity: float,
    novel_route: bool,
    max_cycles: int,
    initial_macs: int,
) -> GateFeatureVector:
    beliefs = tuple(
        belief
        for belief in (
            workspace.entity_distribution,
            workspace.relation_distribution,
            workspace.answer_shape_distribution,
            workspace.value_distribution,
        )
        if belief is not None
    )
    entropy = max((belief.normalized_entropy for belief in beliefs), default=1.0)
    return GateFeatureVector(
        belief_entropy=entropy,
        expert_disagreement=workspace.expert_disagreement,
        entity_ambiguity=entity_ambiguity,
        missing_facet_fraction=min(1.0, len(workspace.missing_facets) / 12.0),
        query_complexity=query_complexity,
        composition_required=float(composition_required),
        discourse_ambiguity=discourse_ambiguity,
        verifier_rejected=float(workspace.verifier_state == VerifierState.REJECTED),
        novel_route=float(novel_route),
        cycle_fraction=min(1.0, workspace.cycle_count / max_cycles),
        compute_budget_fraction=min(
            1.0, workspace.compute_budget.active_macs_remaining / max(1, initial_macs)
        ),
    )
