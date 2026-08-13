"""Typed probabilistic shared workspace with symbolic candidate pointers.

The latent state may express interpretation uncertainty. Exact identifiers and
source-bound values remain labels in explicit categorical distributions; they
are never synthesized from the latent vector.
"""

from __future__ import annotations

import math
from enum import StrEnum

from pydantic import Field, model_validator

from aethersparse.controller.models import FrozenModel


class BeliefSlot(StrEnum):
    ENTITY = "entity"
    RELATION = "relation"
    ANSWER_SHAPE = "answer_shape"
    VALUE = "value"


class VerifierState(StrEnum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    REJECTED = "rejected"


class CategoricalBelief(FrozenModel):
    """A normalized distribution over exact symbolic candidate labels."""

    labels: tuple[str, ...] = Field(min_length=1, max_length=256)
    probabilities: tuple[float, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def normalized_and_aligned(self) -> CategoricalBelief:
        if len(self.labels) != len(self.probabilities):
            raise ValueError("belief labels and probabilities must align")
        if len(set(self.labels)) != len(self.labels):
            raise ValueError("belief labels must be unique")
        if any(not math.isfinite(value) or value < 0.0 for value in self.probabilities):
            raise ValueError("belief probabilities must be finite and non-negative")
        if not math.isclose(sum(self.probabilities), 1.0, rel_tol=0.0, abs_tol=1e-8):
            raise ValueError("belief probabilities must sum to one")
        return self

    @classmethod
    def normalized(
        cls, labels: tuple[str, ...], weights: tuple[float, ...]
    ) -> CategoricalBelief:
        if len(labels) != len(weights) or not labels:
            raise ValueError("labels and weights must be non-empty and aligned")
        if any(not math.isfinite(value) or value < 0.0 for value in weights):
            raise ValueError("weights must be finite and non-negative")
        total = sum(weights)
        probabilities = (
            tuple(value / total for value in weights)
            if total > 0.0
            else tuple(1.0 / len(weights) for _ in weights)
        )
        return cls(labels=labels, probabilities=probabilities)

    def probability(self, label: str) -> float:
        try:
            return self.probabilities[self.labels.index(label)]
        except ValueError:
            return 0.0

    @property
    def entropy_nats(self) -> float:
        return -sum(value * math.log(value) for value in self.probabilities if value > 0.0)

    @property
    def normalized_entropy(self) -> float:
        return self.entropy_nats / math.log(len(self.labels)) if len(self.labels) > 1 else 0.0

    @property
    def top_label(self) -> str:
        return self.labels[max(range(len(self.labels)), key=self.probabilities.__getitem__)]

    @property
    def top_probability(self) -> float:
        return max(self.probabilities)


class ComputeBudget(FrozenModel):
    active_macs_remaining: int = Field(ge=0)
    read_operations_remaining: int = Field(ge=0)
    cycles_remaining: int = Field(ge=0, le=64)

    def spend(self, *, macs: int, read_operations: int, cycles: int = 0) -> ComputeBudget:
        if (
            macs < 0
            or read_operations < 0
            or cycles < 0
            or macs > self.active_macs_remaining
            or read_operations > self.read_operations_remaining
            or cycles > self.cycles_remaining
        ):
            raise ValueError("specialist activation exceeds the remaining compute budget")
        return ComputeBudget(
            active_macs_remaining=self.active_macs_remaining - macs,
            read_operations_remaining=self.read_operations_remaining - read_operations,
            cycles_remaining=self.cycles_remaining - cycles,
        )


class ExpertUpdate(FrozenModel):
    """One expert's uncertain update; candidate labels are exact pointers."""

    expert_id: str
    target: BeliefSlot
    distribution: CategoricalBelief
    reliability_precision: float = Field(ge=0.0, le=1_000_000.0)
    gate_probability: float = Field(ge=0.0, le=1.0)
    latent_delta: tuple[float, ...] = Field(default=(), max_length=1024)
    requested_next_specialists: tuple[str, ...] = ()
    active_parameters: int = Field(default=0, ge=0)
    active_macs: int = Field(default=0, ge=0)
    read_operations: int = Field(default=0, ge=0)


class SharedWorkspace(FrozenModel):
    """Fixed v11 workspace schema; all uncertainty remains inspectable."""

    latent_h: tuple[float, ...] = Field(default=(), max_length=1024)
    entity_distribution: CategoricalBelief | None = None
    relation_distribution: CategoricalBelief | None = None
    answer_shape_distribution: CategoricalBelief | None = None
    value_distribution: CategoricalBelief | None = None
    evidence_sufficiency: float = Field(ge=0.0, le=1.0)
    missing_facets: tuple[str, ...] = ()
    discourse_state: tuple[tuple[str, str], ...] = ()
    expert_disagreement: float = Field(default=0.0, ge=0.0, le=1.0)
    verifier_state: VerifierState = VerifierState.NOT_RUN
    cycle_count: int = Field(default=0, ge=0, le=64)
    compute_budget: ComputeBudget

    def distribution_for(self, slot: BeliefSlot) -> CategoricalBelief | None:
        return {
            BeliefSlot.ENTITY: self.entity_distribution,
            BeliefSlot.RELATION: self.relation_distribution,
            BeliefSlot.ANSWER_SHAPE: self.answer_shape_distribution,
            BeliefSlot.VALUE: self.value_distribution,
        }[slot]

    def with_distribution(
        self,
        slot: BeliefSlot,
        belief: CategoricalBelief,
        *,
        disagreement: float,
        updates: tuple[ExpertUpdate, ...],
    ) -> SharedWorkspace:
        field = {
            BeliefSlot.ENTITY: "entity_distribution",
            BeliefSlot.RELATION: "relation_distribution",
            BeliefSlot.ANSWER_SHAPE: "answer_shape_distribution",
            BeliefSlot.VALUE: "value_distribution",
        }[slot]
        latent_size = max([len(self.latent_h), *(len(item.latent_delta) for item in updates)])
        latent = tuple(
            (self.latent_h[index] if index < len(self.latent_h) else 0.0)
            + sum(
                item.gate_probability
                * (item.latent_delta[index] if index < len(item.latent_delta) else 0.0)
                for item in updates
            )
            for index in range(latent_size)
        )
        budget = self.compute_budget.spend(
            macs=sum(item.active_macs for item in updates),
            read_operations=sum(item.read_operations for item in updates),
        )
        return self.model_copy(
            update={
                field: belief,
                "latent_h": latent,
                "expert_disagreement": disagreement,
                "compute_budget": budget,
            }
        )
