"""Sparse specialist descriptors, activation accounting, and shared nano kernels."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .five_c import BoundaryAction, BoundaryRequest, FiveCSubstrate


class SpecialistKind(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    LEARNED = "LEARNED"
    SHARED_LEARNED = "SHARED_LEARNED"
    SENSOR = "SENSOR"
    ACTUATOR = "ACTUATOR"
    TOOL = "TOOL"
    HYBRID = "HYBRID"


class ActivationState(StrEnum):
    COLD = "COLD"
    WARM = "WARM"
    HOT = "HOT"


class ProvenanceBehavior(StrEnum):
    PRESERVE = "PRESERVE"
    APPEND_DERIVATION = "APPEND_DERIVATION"
    OBSERVATION_SOURCE = "OBSERVATION_SOURCE"
    ACTUATION_RECEIPT = "ACTUATION_RECEIPT"


@dataclass(frozen=True)
class SpecialistDescriptor:
    specialist_id: str
    capability: str
    input_schema: str
    output_schema: str
    parameter_family_id: str | None
    instance_calibration_state: tuple[int, ...]
    activation_cost_ops: int
    ram_requirement_bytes: int
    storage_requirement_bytes: int
    expected_latency_us: int
    allowed_tools: frozenset[str]
    allowed_actions: frozenset[str]
    five_c_constraint_ids: frozenset[str]
    provenance_behavior: ProvenanceBehavior
    kind: SpecialistKind

    def __post_init__(self) -> None:
        text_fields = (
            self.specialist_id,
            self.capability,
            self.input_schema,
            self.output_schema,
        )
        if any(not item for item in text_fields):
            raise ValueError("specialist identity and schemas cannot be empty")
        if any(
            item < 0
            for item in (
                self.activation_cost_ops,
                self.ram_requirement_bytes,
                self.storage_requirement_bytes,
                self.expected_latency_us,
            )
        ):
            raise ValueError("specialist resource costs cannot be negative")
        if (
            self.kind in {SpecialistKind.LEARNED, SpecialistKind.SHARED_LEARNED}
            and self.parameter_family_id is None
        ):
            raise ValueError("learned specialists require a parameter family")


@dataclass(frozen=True)
class SpecialistActivation:
    specialist_id: str
    state: ActivationState


@dataclass
class SpecialistRegistry:
    substrate: FiveCSubstrate
    ram_budget_bytes: int
    descriptors: dict[str, SpecialistDescriptor] = field(default_factory=dict)
    states: dict[str, ActivationState] = field(default_factory=dict)

    def register(self, descriptor: SpecialistDescriptor) -> None:
        if descriptor.specialist_id in self.descriptors:
            raise ValueError("specialist ID is already registered")
        root_ids = {item.constraint_id for item in self.substrate.constraints}
        if not descriptor.five_c_constraint_ids.issubset(root_ids):
            raise ValueError("specialist refers to an unknown 5C constraint")
        self.descriptors[descriptor.specialist_id] = descriptor
        self.states[descriptor.specialist_id] = ActivationState.COLD

    @property
    def resident_ram_bytes(self) -> int:
        return sum(
            descriptor.ram_requirement_bytes
            for identifier, descriptor in self.descriptors.items()
            if self.states[identifier] is not ActivationState.COLD
        )

    def transition(self, specialist_id: str, state: ActivationState) -> SpecialistActivation:
        descriptor = self.descriptors[specialist_id]
        if state is not ActivationState.COLD:
            projected = self.resident_ram_bytes
            if self.states[specialist_id] is ActivationState.COLD:
                projected += descriptor.ram_requirement_bytes
            if projected > self.ram_budget_bytes:
                raise MemoryError("specialist activation exceeds resident RAM budget")
            decision = self.substrate.authorize(
                BoundaryRequest(
                    action=BoundaryAction.ACTIVATE_COMPONENT,
                    subject=specialist_id,
                    capability=descriptor.capability,
                    resource_amount=projected,
                )
            )
            if not decision.allowed:
                raise PermissionError(decision.reason)
        self.states[specialist_id] = state
        return SpecialistActivation(specialist_id, state)

    def prune(self, specialist_id: str) -> None:
        decision = self.substrate.authorize(
            BoundaryRequest(
                action=BoundaryAction.PRUNE_OPTIONAL_SPECIALIST,
                subject=specialist_id,
            )
        )
        if not decision.allowed:
            raise PermissionError(decision.reason)
        self.descriptors.pop(specialist_id)
        self.states.pop(specialist_id)


@dataclass(frozen=True)
class SharedNanoKernel:
    """One deterministic int kernel shared by N calibrated instances.

    Learned residuals may contribute inside the hard envelope.  The final clamp
    is deterministic and therefore cannot be overridden by learned weights.
    """

    parameter_family_id: str
    weights_q7: tuple[int, ...]
    bias: int
    hard_min: int
    hard_max: int

    def __post_init__(self) -> None:
        if not self.weights_q7 or any(not -128 <= value <= 127 for value in self.weights_q7):
            raise ValueError("shared kernel weights must be nonempty int8")
        if self.hard_min > self.hard_max:
            raise ValueError("invalid hard output range")

    def evaluate(
        self,
        state: tuple[int, ...],
        calibration: tuple[int, ...],
        context: int,
    ) -> int:
        if len(state) != len(self.weights_q7) or len(calibration) != len(state):
            raise ValueError("state/calibration width mismatch")
        value = self.bias + context + sum(
            weight * (input_value + calibration_value)
            for weight, input_value, calibration_value in zip(
                self.weights_q7, state, calibration, strict=True
            )
        )
        return min(self.hard_max, max(self.hard_min, value))
