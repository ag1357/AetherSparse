"""Deterministic observer sampling with mandatory failure/novelty capture."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from pydantic import Field

from aethersparse.observer.models import ObserverModel


class SamplingPolicy(ObserverModel):
    high_uncertainty_threshold: float = Field(default=0.75, ge=0.0)
    confident_success_sample_rate: float = Field(default=0.01, ge=0.0, le=1.0)
    deterministic_salt: str = "aethercore-observer-v1"


class SamplingDecision(ObserverModel):
    sampled: bool
    reasons: tuple[str, ...]
    deterministic_fraction: float = Field(ge=0.0, lt=1.0)


def deterministic_fraction(case_id: str, salt: str) -> float:
    digest = hashlib.sha256(f"{salt}\0{case_id}".encode()).digest()
    bucket = int.from_bytes(digest[:8], "big")
    return bucket / 2**64


@dataclass
class DeterministicSampler:
    """Stateful sampler: the first occurrence of every route is retained."""

    policy: SamplingPolicy = field(default_factory=SamplingPolicy)
    _seen_routes: set[str] = field(default_factory=set, init=False, repr=False)

    def decide(
        self,
        *,
        case_id: str,
        route_sha256: str,
        final_correctness: bool,
        maximum_uncertainty: float,
    ) -> SamplingDecision:
        fraction = deterministic_fraction(case_id, self.policy.deterministic_salt)
        reasons: list[str] = []
        if not final_correctness:
            reasons.append("failure")
        if maximum_uncertainty >= self.policy.high_uncertainty_threshold:
            reasons.append("high_uncertainty")
        if route_sha256 not in self._seen_routes:
            reasons.append("novel_route")
        self._seen_routes.add(route_sha256)
        if not reasons and fraction < self.policy.confident_success_sample_rate:
            reasons.append("confident_success_sample")
        return SamplingDecision(
            sampled=bool(reasons), reasons=tuple(reasons), deterministic_fraction=fraction
        )
