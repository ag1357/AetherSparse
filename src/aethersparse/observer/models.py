"""Immutable contracts for optional AetherCore research telemetry.

The observer is intentionally a sibling of the production controller.  These
records describe completed computation; they never authorize or influence an
inference decision.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ObserverModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ProbabilityMass(ObserverModel):
    label: str
    probability: float = Field(ge=0.0, le=1.0)


class HiddenStateSummary(ObserverModel):
    """Compact statistics; selected activations are explicitly bounded."""

    dimension: int = Field(ge=0)
    mean: float
    variance: float = Field(ge=0.0)
    l2_norm: float = Field(ge=0.0)
    saturation_fraction: float = Field(ge=0.0, le=1.0)
    dead_unit_fraction: float = Field(ge=0.0, le=1.0)
    selected_activation: tuple[float, ...] = Field(default=(), max_length=256)

    @model_validator(mode="after")
    def selected_vector_is_not_larger_than_state(self) -> HiddenStateSummary:
        if len(self.selected_activation) > self.dimension:
            raise ValueError("selected activation cannot exceed hidden-state dimension")
        return self


class ExpertTelemetry(ObserverModel):
    module_id: str
    active: bool
    gate_probability: float = Field(ge=0.0, le=1.0)
    output_distribution: tuple[ProbabilityMass, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)
    reliability: float = Field(ge=0.0)
    hidden_state: HiddenStateSummary | None = None

    @model_validator(mode="after")
    def distribution_is_normalized(self) -> ExpertTelemetry:
        if self.output_distribution:
            total = sum(item.probability for item in self.output_distribution)
            if abs(total - 1.0) > 1e-6:
                raise ValueError("expert output distribution must sum to one")
            labels = [item.label for item in self.output_distribution]
            if len(set(labels)) != len(labels):
                raise ValueError("expert output labels must be unique")
        return self


class DepthDecision(StrEnum):
    CONTINUE = "continue"
    HALT = "halt"
    FORCE_CONTINUE = "force_continue"
    FORCE_HALT = "force_halt"


class VerifierStatus(StrEnum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    REJECTED = "rejected"


class CycleTelemetry(ObserverModel):
    cycle_number: int = Field(ge=0)
    workspace_input_signature: str
    workspace_output_signature: str
    active_experts: tuple[str, ...]
    experts: tuple[ExpertTelemetry, ...]
    entropy_before: float = Field(ge=0.0)
    entropy_after: float = Field(ge=0.0)
    disagreement_before: float = Field(ge=0.0)
    disagreement_after: float = Field(ge=0.0)
    required_facets: tuple[str, ...] = ()
    missing_facets: tuple[str, ...] = ()
    previous_action: str | None = None
    next_action: str
    depth_decision: DepthDecision
    verifier_status: VerifierStatus
    active_macs: int = Field(ge=0)
    active_parameter_count: int = Field(ge=0)

    @model_validator(mode="after")
    def active_expert_ids_match_payloads(self) -> CycleTelemetry:
        declared = self.active_experts
        module_ids = tuple(expert.module_id for expert in self.experts)
        measured = tuple(expert.module_id for expert in self.experts if expert.active)
        if len(set(declared)) != len(declared):
            raise ValueError("active expert IDs must be unique")
        if len(set(module_ids)) != len(module_ids):
            raise ValueError("expert telemetry module IDs must be unique per cycle")
        if set(declared) != set(measured):
            raise ValueError("active_experts must match active expert telemetry")
        return self


class TelemetryRecord(ObserverModel):
    schema_version: Literal["aethercore.observer.v1"] = "aethercore.observer.v1"
    case_id: str
    partition: str
    tier: str
    cycles: tuple[CycleTelemetry, ...] = Field(min_length=1)
    final_correctness: bool
    final_semantic_correctness: bool
    final_provenance_correctness: bool
    route_signature: str
    route_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    maximum_uncertainty: float = Field(ge=0.0)
    sampled_because: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def cycles_are_contiguous(self) -> TelemetryRecord:
        numbers = tuple(cycle.cycle_number for cycle in self.cycles)
        if numbers != tuple(range(len(self.cycles))):
            raise ValueError("cycle numbers must be contiguous and zero-based")
        lines = []
        for cycle in self.cycles:
            experts = ",".join(sorted(cycle.active_experts)) or "NONE"
            lines.append(f"C{cycle.cycle_number}:{experts}")
        final = self.cycles[-1]
        lines.append(f"HALT:{final.depth_decision.value}:{final.verifier_status.value}")
        expected_signature = "\n".join(lines)
        if self.route_signature != expected_signature:
            raise ValueError("route signature does not describe the recorded cycles")
        expected_hash = hashlib.sha256(self.route_signature.encode()).hexdigest()
        if self.route_sha256 != expected_hash:
            raise ValueError("route signature hash mismatch")
        return self


class InterventionKind(StrEnum):
    FORCE_ENTITY_ON = "force_entity_on"
    FORCE_ENTITY_OFF = "force_entity_off"
    FORCE_VALUE_ON = "force_value_on"
    FORCE_VALUE_OFF = "force_value_off"
    FORCE_ADDITIONAL_CYCLE = "force_additional_cycle"
    STOP_ONE_CYCLE_EARLIER = "stop_one_cycle_earlier"
    REPLACE_EXPERT_WITH_TRAINING_ORACLE = "replace_expert_with_training_oracle"
    SELECT_ALTERNATE_ENTITY = "select_alternate_entity"
    SELECT_ALTERNATE_VALUE = "select_alternate_value"
    BYPASS_FUSION = "bypass_fusion"
    FORCE_FUSION_INPUT = "force_fusion_input"


class CounterfactualIntervention(ObserverModel):
    kind: InterventionKind
    target_module: str | None = None
    arguments: tuple[tuple[str, str], ...] = ()


class CounterfactualOutcome(ObserverModel):
    route_signature: str
    semantic_correctness: bool
    provenance_correctness: bool
    accepted: bool
    active_macs: int = Field(ge=0)
    cycles: int = Field(ge=0)
    missing_evidence: bool = False
    verifier_rejected: bool = False


class CausalAttribution(StrEnum):
    EXPERT_FAILURE = "expert_failure"
    GATE_FAILURE = "gate_failure"
    FUSION_FAILURE = "fusion_failure"
    INSUFFICIENT_DEPTH = "insufficient_depth"
    EXCESSIVE_DEPTH = "excessive_depth"
    BAD_UPSTREAM_STATE = "bad_upstream_state"
    MISSING_EVIDENCE = "missing_evidence"
    VERIFIER_REJECTION = "verifier_rejection"
    NO_CAUSAL_IMPROVEMENT = "no_causal_improvement"


class CounterfactualRecord(ObserverModel):
    schema_version: Literal["aethercore.counterfactual.v1"] = "aethercore.counterfactual.v1"
    case_id: str
    partition: Literal["development", "tuning"]
    actual: CounterfactualOutcome
    intervention: CounterfactualIntervention
    counterfactual: CounterfactualOutcome
    correctness_delta: int = Field(ge=-1, le=1)
    mac_delta: int
    cycle_delta: int
    attribution: CausalAttribution
    evidence: tuple[str, ...]


class ActivationCost(ObserverModel):
    integer_ops: int = Field(ge=0)
    macs: int = Field(ge=0)
    memory_bytes: int = Field(ge=0)
    scratch_ram_bytes: int = Field(ge=0)


class ArchitectureModule(ObserverModel):
    module_id: str
    module_version: str
    purpose: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    parameter_count: int = Field(ge=0)
    quantization: str
    activation_cost: ActivationCost
    supported_state_types: tuple[str, ...]
    dependencies: tuple[str, ...]
    model_hash: str = Field(pattern=r"^(none|[0-9a-f]{64})$")
    calibration_artifact: str | None = None
    known_failure_clusters: tuple[str, ...]
    status: Literal["active", "inactive", "training_only"]


class ArchitectureRegistry(ObserverModel):
    schema_version: Literal["aethercore.architecture-registry.v1"] = (
        "aethercore.architecture-registry.v1"
    )
    architecture_id: str
    architecture_version: str
    modules: tuple[ArchitectureModule, ...]
    registry_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def module_ids_are_unique_and_dependencies_exist(self) -> ArchitectureRegistry:
        ids = [module.module_id for module in self.modules]
        if len(ids) != len(set(ids)):
            raise ValueError("architecture registry module IDs must be unique")
        known = set(ids)
        missing = {
            dependency
            for module in self.modules
            for dependency in module.dependencies
            if dependency not in known
        }
        if missing:
            raise ValueError(f"unknown module dependencies: {sorted(missing)}")
        return self


class OptimizationProposal(ObserverModel):
    schema_version: Literal["aethercore.optimization-proposal.v1"] = (
        "aethercore.optimization-proposal.v1"
    )
    proposal_id: str
    observed_weakness: str
    affected_module: str
    evidence: tuple[str, ...] = Field(min_length=1)
    proposed_intervention: str
    expected_benefit: str
    expected_compute_change_macs: int
    expected_storage_change_bytes: int
    tests_required: tuple[str, ...] = Field(min_length=1)
    candidate_version_id: str
    status: Literal["proposed"] = "proposed"
