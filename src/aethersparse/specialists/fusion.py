"""Calibratable belief fusion and explicit expert-disagreement metrics."""

from __future__ import annotations

import math
from enum import StrEnum

from pydantic import Field

from aethersparse.controller.models import FrozenModel
from aethersparse.specialists.workspace import (
    BeliefSlot,
    CategoricalBelief,
    ExpertUpdate,
    SharedWorkspace,
)

_EPSILON = 1e-12
_UNRESOLVED = "__unresolved__"


class FusionMethod(StrEnum):
    WEIGHTED_LOGIT = "weighted_logit"
    TEMPERATURE_PRODUCT = "temperature_product"
    PRECISION_RESIDUAL = "precision_residual"
    LEARNED = "learned"
    PARTICLE_TOP_K = "particle_top_k"


class LearnedFusionParameters(FrozenModel):
    """Fitted scalar gate coefficients; defaults are neutral, not selected weights."""

    prior_weight: float = 1.0
    reliability_weight: float = 0.0
    entropy_weight: float = 0.0
    disagreement_weight: float = 0.0
    bias: float = 0.0


class Disagreement(FrozenModel):
    js_divergence: float = Field(ge=0.0, le=1.0)
    top1_disagreement: float = Field(ge=0.0, le=1.0)
    rank_disagreement: float = Field(ge=0.0, le=1.0)
    entropy_difference: float = Field(ge=0.0, le=1.0)
    confidence_contradiction: float = Field(ge=0.0, le=1.0)
    aggregate: float = Field(ge=0.0, le=1.0)


class FusionOutcome(FrozenModel):
    method: FusionMethod
    target: BeliefSlot
    prior: CategoricalBelief
    posterior: CategoricalBelief
    disagreement: Disagreement
    workspace: SharedWorkspace
    active_experts: tuple[str, ...]


def _labels(prior: CategoricalBelief, updates: tuple[ExpertUpdate, ...]) -> tuple[str, ...]:
    ordered = list(prior.labels)
    for update in updates:
        ordered.extend(label for label in update.distribution.labels if label not in ordered)
    return tuple(ordered)


def _aligned(belief: CategoricalBelief, labels: tuple[str, ...]) -> tuple[float, ...]:
    values = tuple(max(belief.probability(label), _EPSILON) for label in labels)
    total = sum(values)
    return tuple(value / total for value in values)


def _softmax(logits: tuple[float, ...]) -> tuple[float, ...]:
    maximum = max(logits)
    exponents = tuple(math.exp(value - maximum) for value in logits)
    total = sum(exponents)
    return tuple(value / total for value in exponents)


def _ranks(probabilities: tuple[float, ...]) -> tuple[int, ...]:
    order = sorted(range(len(probabilities)), key=lambda index: (-probabilities[index], index))
    ranks = [0] * len(order)
    for rank, index in enumerate(order):
        ranks[index] = rank
    return tuple(ranks)


def expert_disagreement(updates: tuple[ExpertUpdate, ...]) -> Disagreement:
    if len(updates) < 2:
        return Disagreement(
            js_divergence=0.0,
            top1_disagreement=0.0,
            rank_disagreement=0.0,
            entropy_difference=0.0,
            confidence_contradiction=0.0,
            aggregate=0.0,
        )
    labels = tuple(dict.fromkeys(label for item in updates for label in item.distribution.labels))
    aligned = tuple(_aligned(item.distribution, labels) for item in updates)
    mean = tuple(
        sum(values[index] for values in aligned) / len(aligned)
        for index in range(len(labels))
    )
    js_nats = sum(
        sum(value * math.log(value / mean[index]) for index, value in enumerate(values))
        for values in aligned
    ) / len(aligned)
    js = min(1.0, js_nats / math.log(2.0))
    top_indices = {max(range(len(values)), key=values.__getitem__) for values in aligned}
    top1 = (len(top_indices) - 1) / (len(updates) - 1)
    ranks = tuple(_ranks(values) for values in aligned)
    pair_distances: list[float] = []
    denominator = max(1, len(labels) - 1)
    for left in range(len(ranks)):
        for right in range(left + 1, len(ranks)):
            pair_distances.append(
                sum(abs(a - b) for a, b in zip(ranks[left], ranks[right], strict=True))
                / (len(labels) * denominator)
            )
    rank = min(1.0, sum(pair_distances) / len(pair_distances))
    entropies = tuple(
        CategoricalBelief(labels=labels, probabilities=values).normalized_entropy
        for values in aligned
    )
    entropy_difference = min(1.0, max(entropies) - min(entropies))
    confidence_contradiction = 0.0
    for left in range(len(aligned)):
        left_top = max(range(len(labels)), key=aligned[left].__getitem__)
        for right in range(left + 1, len(aligned)):
            right_top = max(range(len(labels)), key=aligned[right].__getitem__)
            if left_top != right_top:
                confidence_contradiction = max(
                    confidence_contradiction,
                    min(aligned[left][left_top], aligned[right][right_top]),
                )
    aggregate = min(1.0, max(js, top1, rank, entropy_difference, confidence_contradiction))
    return Disagreement(
        js_divergence=js,
        top1_disagreement=top1,
        rank_disagreement=rank,
        entropy_difference=entropy_difference,
        confidence_contradiction=confidence_contradiction,
        aggregate=aggregate,
    )


class BeliefFusion:
    """Fuse one belief slot without inventing a candidate label."""

    def __init__(
        self,
        method: FusionMethod,
        *,
        temperature: float = 1.0,
        particle_top_k: int = 8,
        learned: LearnedFusionParameters | None = None,
    ) -> None:
        if temperature <= 0.0 or not math.isfinite(temperature):
            raise ValueError("temperature must be finite and positive")
        if particle_top_k < 1:
            raise ValueError("particle_top_k must be positive")
        self.method = method
        self.temperature = temperature
        self.particle_top_k = particle_top_k
        self.learned = learned or LearnedFusionParameters()

    def fuse(
        self,
        workspace: SharedWorkspace,
        target: BeliefSlot,
        updates: tuple[ExpertUpdate, ...],
    ) -> FusionOutcome:
        prior = workspace.distribution_for(target)
        if prior is None:
            raise ValueError(f"workspace has no prior for {target}")
        if not updates:
            raise ValueError("at least one expert update is required")
        if any(item.target != target for item in updates):
            raise ValueError("all expert updates must target the requested belief slot")
        labels = _labels(prior, updates)
        prior_values = _aligned(prior, labels)
        expert_values = tuple(_aligned(item.distribution, labels) for item in updates)
        disagreement = expert_disagreement(updates)
        posterior_values = self._posterior(
            prior_values, expert_values, updates, disagreement.aggregate
        )
        posterior = CategoricalBelief(labels=labels, probabilities=posterior_values)
        if self.method == FusionMethod.PARTICLE_TOP_K and len(labels) > self.particle_top_k:
            posterior = self._bounded_particles(posterior)
        next_workspace = workspace.with_distribution(
            target,
            posterior,
            disagreement=disagreement.aggregate,
            updates=updates,
        )
        return FusionOutcome(
            method=self.method,
            target=target,
            prior=prior,
            posterior=posterior,
            disagreement=disagreement,
            workspace=next_workspace,
            active_experts=tuple(item.expert_id for item in updates),
        )

    def _posterior(
        self,
        prior: tuple[float, ...],
        experts: tuple[tuple[float, ...], ...],
        updates: tuple[ExpertUpdate, ...],
        disagreement: float,
    ) -> tuple[float, ...]:
        if self.method == FusionMethod.WEIGHTED_LOGIT:
            logits = tuple(
                math.log(prior[index])
                + sum(
                    item.gate_probability * math.log(values[index])
                    for item, values in zip(updates, experts, strict=True)
                )
                for index in range(len(prior))
            )
        elif self.method in {FusionMethod.TEMPERATURE_PRODUCT, FusionMethod.PARTICLE_TOP_K}:
            logits = tuple(
                math.log(prior[index])
                + sum(
                    item.gate_probability * math.log(values[index]) / self.temperature
                    for item, values in zip(updates, experts, strict=True)
                )
                for index in range(len(prior))
            )
        elif self.method == FusionMethod.PRECISION_RESIDUAL:
            prior_precision = 1.0
            denominator = prior_precision + sum(
                item.gate_probability * item.reliability_precision for item in updates
            )
            logits = tuple(
                (
                    prior_precision * math.log(prior[index])
                    + sum(
                        item.gate_probability
                        * item.reliability_precision
                        * math.log(values[index])
                        for item, values in zip(updates, experts, strict=True)
                    )
                )
                / denominator
                for index in range(len(prior))
            )
        elif self.method == FusionMethod.LEARNED:
            logits = tuple(
                self.learned.prior_weight * math.log(prior[index])
                + sum(
                    self._learned_gate(item, values, disagreement) * math.log(values[index])
                    for item, values in zip(updates, experts, strict=True)
                )
                for index in range(len(prior))
            )
        else:  # pragma: no cover - StrEnum exhaustiveness guard
            raise ValueError(f"unsupported fusion method: {self.method}")
        return _softmax(logits)

    def _learned_gate(
        self,
        update: ExpertUpdate,
        probabilities: tuple[float, ...],
        disagreement: float,
    ) -> float:
        entropy = CategoricalBelief(
            labels=tuple(str(index) for index in range(len(probabilities))),
            probabilities=probabilities,
        ).normalized_entropy
        logit = (
            self.learned.bias
            + self.learned.reliability_weight * update.reliability_precision
            + self.learned.entropy_weight * entropy
            + self.learned.disagreement_weight * disagreement
        )
        return update.gate_probability / (1.0 + math.exp(-max(-60.0, min(60.0, logit))))

    def _bounded_particles(self, belief: CategoricalBelief) -> CategoricalBelief:
        order = sorted(
            range(len(belief.labels)),
            key=lambda index: (-belief.probabilities[index], belief.labels[index]),
        )
        retained = order[: self.particle_top_k]
        tail = sum(belief.probabilities[index] for index in order[self.particle_top_k :])
        labels = tuple(belief.labels[index] for index in retained)
        probabilities = tuple(belief.probabilities[index] for index in retained)
        if tail > 0.0:
            labels += (_UNRESOLVED,)
            probabilities += (tail,)
        return CategoricalBelief.normalized(labels, probabilities)
