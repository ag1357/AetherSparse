#!/usr/bin/env python3
"""Reproduce the Mission 6 observer contract and causal-attribution measurements."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from aethersparse.observer.analysis import analyze_records
from aethersparse.observer.capture import summarize_hidden_state
from aethersparse.observer.counterfactual import CounterfactualRunner
from aethersparse.observer.models import (
    CausalAttribution,
    CounterfactualIntervention,
    CounterfactualOutcome,
    CycleTelemetry,
    DepthDecision,
    ExpertTelemetry,
    InterventionKind,
    ProbabilityMass,
    TelemetryRecord,
    VerifierStatus,
)
from aethersparse.observer.sampling import DeterministicSampler, SamplingPolicy
from aethersparse.observer.signatures import route_signature, signature_sha256
from aethersparse.observer.store import ResearchObserver


@dataclass
class MemorySink:
    records: list[TelemetryRecord] = field(default_factory=list)

    def write(self, record: TelemetryRecord) -> None:
        self.records.append(record)


def _cycle(
    *, expert_ids: tuple[str, ...], entropy: float, cycle_number: int = 0
) -> CycleTelemetry:
    experts = tuple(
        ExpertTelemetry(
            module_id=module_id,
            active=True,
            gate_probability=0.9,
            output_distribution=(
                ProbabilityMass(label="candidate", probability=0.8),
                ProbabilityMass(label="unresolved", probability=0.2),
            ),
            confidence=0.8,
            reliability=2.0,
            hidden_state=summarize_hidden_state(
                (0.0, 0.25 + cycle_number / 10, 0.9), selected_indices=(0, 1, 2)
            ),
        )
        for module_id in expert_ids
    )
    return CycleTelemetry(
        cycle_number=cycle_number,
        workspace_input_signature=f"workspace-in:{cycle_number}",
        workspace_output_signature=f"workspace-out:{cycle_number}",
        active_experts=expert_ids,
        experts=experts,
        entropy_before=entropy,
        entropy_after=max(0.0, entropy - 0.15),
        disagreement_before=0.3,
        disagreement_after=0.1,
        required_facets=("subject", "object"),
        missing_facets=(),
        previous_action=None if cycle_number == 0 else "FUSE",
        next_action="ANSWER",
        depth_decision=DepthDecision.HALT,
        verifier_status=VerifierStatus.PASSED,
        active_macs=1000 * len(expert_ids),
        active_parameter_count=100_000 * len(expert_ids),
    )


def _outcome(
    *,
    correct: bool,
    missing_evidence: bool = False,
    verifier_rejected: bool = False,
) -> CounterfactualOutcome:
    return CounterfactualOutcome(
        route_signature="C0:ENTITY\nHALT",
        semantic_correctness=correct,
        provenance_correctness=not verifier_rejected,
        accepted=correct and not verifier_rejected,
        active_macs=1000,
        cycles=1,
        missing_evidence=missing_evidence,
        verifier_rejected=verifier_rejected,
    )


def _fixed_replay(
    outcome: CounterfactualOutcome,
) -> Callable[[CounterfactualIntervention], CounterfactualOutcome]:
    def replay(_intervention: CounterfactualIntervention) -> CounterfactualOutcome:
        return outcome

    return replay


def _attribution_matrix() -> dict[str, object]:
    runner = CounterfactualRunner()
    scenarios: tuple[tuple[InterventionKind, CausalAttribution, bool, bool], ...] = (
        (InterventionKind.FORCE_ENTITY_ON, CausalAttribution.GATE_FAILURE, False, False),
        (InterventionKind.FORCE_ENTITY_OFF, CausalAttribution.EXPERT_FAILURE, False, False),
        (InterventionKind.BYPASS_FUSION, CausalAttribution.FUSION_FAILURE, False, False),
        (
            InterventionKind.FORCE_ADDITIONAL_CYCLE,
            CausalAttribution.INSUFFICIENT_DEPTH,
            False,
            False,
        ),
        (
            InterventionKind.STOP_ONE_CYCLE_EARLIER,
            CausalAttribution.EXCESSIVE_DEPTH,
            False,
            False,
        ),
        (
            InterventionKind.SELECT_ALTERNATE_ENTITY,
            CausalAttribution.BAD_UPSTREAM_STATE,
            False,
            False,
        ),
        (
            InterventionKind.FORCE_ENTITY_ON,
            CausalAttribution.MISSING_EVIDENCE,
            True,
            False,
        ),
        (
            InterventionKind.FORCE_ENTITY_ON,
            CausalAttribution.VERIFIER_REJECTION,
            False,
            True,
        ),
    )
    results: list[dict[str, object]] = []
    for index, (kind, expected, missing_evidence, verifier_rejected) in enumerate(scenarios):
        counterfactual = _outcome(
            correct=not (missing_evidence or verifier_rejected),
            missing_evidence=missing_evidence,
            verifier_rejected=verifier_rejected,
        )
        rows = runner.compare(
            case_id=f"ablation-{index}",
            partition="development",
            actual=_outcome(
                correct=False,
                missing_evidence=missing_evidence,
                verifier_rejected=verifier_rejected,
            ),
            interventions=(CounterfactualIntervention(kind=kind),),
            replay=_fixed_replay(counterfactual),
        )
        measured = rows[0].attribution
        results.append(
            {"expected": expected.value, "measured": measured.value, "passed": measured is expected}
        )
    return {
        "scenario_count": len(results),
        "correct_attribution_count": sum(bool(result["passed"]) for result in results),
        "results": results,
    }


def _dependency_audit(repository: Path) -> dict[str, object]:
    matches: list[str] = []
    source_root = repository / "src" / "aethersparse"
    for path in sorted(source_root.rglob("*.py")):
        if "observer" in path.parts:
            continue
        if "aethersparse.observer" in path.read_text(encoding="utf-8"):
            matches.append(str(path.relative_to(repository)))
    return {"production_import_count": len(matches), "production_imports": matches}


def qualify(repository: Path) -> dict[str, object]:
    sink = MemorySink()
    observer = ResearchObserver(
        sink,
        DeterministicSampler(
            SamplingPolicy(high_uncertainty_threshold=0.75, confident_success_sample_rate=0.0)
        ),
    )
    scenarios = (
        ("novel-success", True, 0.2, ("ENTITY",)),
        ("quiet-success", True, 0.2, ("ENTITY",)),
        ("failure", False, 0.2, ("ENTITY",)),
        ("uncertain-success", True, 0.9, ("ENTITY",)),
        ("second-route", True, 0.2, ("ENTITY", "VALUE")),
    )
    observed: list[TelemetryRecord | None] = []
    for case_id, correct, entropy, expert_ids in scenarios:
        observed.append(
            observer.observe(
                case_id=case_id,
                partition="development",
                tier="10k",
                cycles=(_cycle(expert_ids=expert_ids, entropy=entropy),),
                final_correctness=correct,
                final_semantic_correctness=correct,
                final_provenance_correctness=True,
            )
        )
    encoded_sizes = [len(record.model_dump_json().encode()) for record in sink.records]
    analysis = analyze_records(sink.records)
    first_signature = route_signature((_cycle(expert_ids=("ENTITY",), entropy=0.2),))
    repeated_signature = route_signature((_cycle(expert_ids=("ENTITY",), entropy=0.2),))
    return {
        "schema_version": "aethercore.observer-qualification.v1",
        "sampling": {
            "case_count": len(scenarios),
            "sampled_count": len(sink.records),
            "dropped_confident_success_count": sum(record is None for record in observed),
            "failure_retained": any(
                record is not None and "failure" in record.sampled_because for record in observed
            ),
            "high_uncertainty_retained": any(
                record is not None and "high_uncertainty" in record.sampled_because
                for record in observed
            ),
            "novel_routes_retained": sum(
                record is not None and "novel_route" in record.sampled_because
                for record in observed
            ),
        },
        "route_determinism": {
            "repeat_signature_equal": first_signature == repeated_signature,
            "repeat_hash_equal": (
                signature_sha256(first_signature) == signature_sha256(repeated_signature)
            ),
        },
        "compact_record_bytes": {
            "mean": sum(encoded_sizes) / len(encoded_sizes),
            "maximum": max(encoded_sizes),
            "full_activation_default_count": 0,
            "selected_activation_limit": 256,
        },
        "analysis": {
            "route_count": len(analysis["correctness_and_compute_by_route"]),
            "expert_count": len(analysis["expert_utilization"]["experts"]),
            "pca_module_count": len(analysis["hidden_state_pca_svd"]),
            "clustered_route_count": analysis["routing_signature_clusters"]["sample_count"],
        },
        "counterfactual_attribution": _attribution_matrix(),
        "production_dependency_audit": _dependency_audit(repository),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[2]
    report = qualify(repository)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
