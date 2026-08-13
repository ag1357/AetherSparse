"""Split-safe controlled causal replay records and attribution."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Literal, cast

from aethersparse.observer.models import (
    CausalAttribution,
    CounterfactualIntervention,
    CounterfactualOutcome,
    CounterfactualRecord,
    InterventionKind,
)

ReplayFunction = Callable[[CounterfactualIntervention], CounterfactualOutcome]


def attribute_counterfactual(
    actual: CounterfactualOutcome,
    counterfactual: CounterfactualOutcome,
    intervention: CounterfactualIntervention,
) -> tuple[CausalAttribution, tuple[str, ...]]:
    """Attribute a controlled delta without treating correlation as reward."""

    improved = counterfactual.semantic_correctness and not actual.semantic_correctness
    if actual.missing_evidence and counterfactual.missing_evidence:
        return CausalAttribution.MISSING_EVIDENCE, ("evidence absent on both paths",)
    if actual.verifier_rejected and counterfactual.verifier_rejected:
        return CausalAttribution.VERIFIER_REJECTION, ("verifier rejected both paths",)
    if not improved:
        return CausalAttribution.NO_CAUSAL_IMPROVEMENT, (
            "intervention did not change an incorrect semantic outcome to correct",
        )

    kind = intervention.kind
    if kind in {InterventionKind.FORCE_ENTITY_ON, InterventionKind.FORCE_VALUE_ON}:
        return CausalAttribution.GATE_FAILURE, ("forcing a gated specialist on repaired outcome",)
    if kind in {
        InterventionKind.FORCE_ENTITY_OFF,
        InterventionKind.FORCE_VALUE_OFF,
        InterventionKind.REPLACE_EXPERT_WITH_TRAINING_ORACLE,
    }:
        return CausalAttribution.EXPERT_FAILURE, ("expert intervention repaired outcome",)
    if kind in {InterventionKind.BYPASS_FUSION, InterventionKind.FORCE_FUSION_INPUT}:
        return CausalAttribution.FUSION_FAILURE, (
            "controlled fusion intervention repaired outcome",
        )
    if kind is InterventionKind.FORCE_ADDITIONAL_CYCLE:
        return CausalAttribution.INSUFFICIENT_DEPTH, ("one additional cycle repaired outcome",)
    if kind is InterventionKind.STOP_ONE_CYCLE_EARLIER:
        return CausalAttribution.EXCESSIVE_DEPTH, ("stopping one cycle earlier repaired outcome",)
    return CausalAttribution.BAD_UPSTREAM_STATE, (
        "alternate symbolic hypothesis repaired outcome",
    )


class CounterfactualRunner:
    """Runs interventions only where labels are permitted for development work."""

    allowed_partitions = frozenset({"development", "tuning"})

    def compare(
        self,
        *,
        case_id: str,
        partition: str,
        actual: CounterfactualOutcome,
        interventions: Iterable[CounterfactualIntervention],
        replay: ReplayFunction,
    ) -> tuple[CounterfactualRecord, ...]:
        if partition not in self.allowed_partitions:
            raise ValueError("counterfactual label replay is limited to development/tuning")
        rows: list[CounterfactualRecord] = []
        for intervention in interventions:
            counterfactual = replay(intervention)
            attribution, evidence = attribute_counterfactual(actual, counterfactual, intervention)
            rows.append(
                CounterfactualRecord(
                    case_id=case_id,
                    partition=cast(Literal["development", "tuning"], partition),
                    actual=actual,
                    intervention=intervention,
                    counterfactual=counterfactual,
                    correctness_delta=(
                        int(counterfactual.semantic_correctness)
                        - int(actual.semantic_correctness)
                    ),
                    mac_delta=counterfactual.active_macs - actual.active_macs,
                    cycle_delta=counterfactual.cycles - actual.cycles,
                    attribution=attribution,
                    evidence=evidence,
                )
            )
        return tuple(rows)
