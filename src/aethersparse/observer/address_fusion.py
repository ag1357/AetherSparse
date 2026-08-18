"""Training-only observer adapter for completed Semantic Address v2 fusion."""

from __future__ import annotations

from aethersparse.controller.address_fusion import AddressBelief
from aethersparse.observer.models import ExpertTelemetry, ProbabilityMass


def address_belief_telemetry(
    belief: AddressBelief, *, module_id: str = "semantic-address-v2.fusion"
) -> ExpertTelemetry:
    """Project a completed belief into the existing immutable observer schema."""

    return ExpertTelemetry(
        module_id=module_id,
        active=True,
        gate_probability=1.0,
        output_distribution=tuple(
            ProbabilityMass(label=label, probability=probability)
            for label, probability in zip(
                belief.distribution.labels,
                belief.distribution.probabilities,
                strict=True,
            )
        ),
        confidence=belief.distribution.top_probability,
        reliability=1.0 - belief.distribution.probability("__unresolved__"),
    )
