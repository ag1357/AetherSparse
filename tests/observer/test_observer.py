from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aethersparse.observer.analysis import (
    analyze_records,
    counterfactual_analysis,
    hidden_state_clustering,
    pca_svd,
)
from aethersparse.observer.capture import summarize_hidden_state
from aethersparse.observer.counterfactual import CounterfactualRunner
from aethersparse.observer.models import (
    ActivationCost,
    ArchitectureModule,
    ArchitectureRegistry,
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
from aethersparse.observer.proposals import make_proposal
from aethersparse.observer.registry import load_registry, write_registry
from aethersparse.observer.sampling import DeterministicSampler, SamplingPolicy
from aethersparse.observer.signatures import route_signature, signature_sha256
from aethersparse.observer.store import JsonlObserverSink, ResearchObserver, load_jsonl


def _expert(
    module_id: str,
    *,
    confidence: float = 0.8,
    hidden: tuple[float, ...] = (0.0, 0.2, 0.9),
) -> ExpertTelemetry:
    return ExpertTelemetry(
        module_id=module_id,
        active=True,
        gate_probability=0.9,
        output_distribution=(
            ProbabilityMass(label="candidate:a", probability=confidence),
            ProbabilityMass(label="unresolved", probability=1.0 - confidence),
        ),
        confidence=confidence,
        reliability=2.0,
        hidden_state=summarize_hidden_state(hidden, selected_indices=(0, 1, 2)),
    )


def _cycles(*expert_ids: str, depth: int = 1, entropy: float = 0.2) -> tuple[CycleTelemetry, ...]:
    experts = tuple(_expert(module_id) for module_id in expert_ids)
    return tuple(
        CycleTelemetry(
            cycle_number=index,
            workspace_input_signature=f"input-{index}",
            workspace_output_signature=f"output-{index}",
            active_experts=expert_ids,
            experts=experts,
            entropy_before=entropy,
            entropy_after=max(0.0, entropy - 0.1),
            disagreement_before=0.3,
            disagreement_after=0.1,
            required_facets=("subject", "object"),
            missing_facets=() if index == depth - 1 else ("object",),
            previous_action=None if index == 0 else "FUSE",
            next_action="ANSWER" if index == depth - 1 else "FUSE",
            depth_decision=DepthDecision.HALT if index == depth - 1 else DepthDecision.CONTINUE,
            verifier_status=VerifierStatus.PASSED if index == depth - 1 else VerifierStatus.NOT_RUN,
            active_macs=100 * len(expert_ids),
            active_parameter_count=1_000 * len(expert_ids),
        )
        for index in range(depth)
    )


def _record(
    case_id: str,
    *,
    correct: bool,
    expert_ids: tuple[str, ...] = ("entity",),
    depth: int = 1,
    tier: str = "10k",
) -> TelemetryRecord:
    cycles = _cycles(*expert_ids, depth=depth)
    signature = route_signature(cycles)
    return TelemetryRecord(
        case_id=case_id,
        partition="development",
        tier=tier,
        cycles=cycles,
        final_correctness=correct,
        final_semantic_correctness=correct,
        final_provenance_correctness=True,
        route_signature=signature,
        route_sha256=signature_sha256(signature),
        maximum_uncertainty=0.2,
        sampled_because=("failure",) if not correct else ("novel_route",),
    )


def test_hidden_capture_is_compact_and_distribution_is_validated() -> None:
    summary = summarize_hidden_state((0.0, 1.0, -1.0, 0.5))
    assert summary.dimension == 4
    assert summary.selected_activation == ()
    assert summary.dead_unit_fraction == 0.25
    assert summary.saturation_fraction == 0.5
    with pytest.raises(ValidationError):
        ExpertTelemetry(
            module_id="bad",
            active=True,
            gate_probability=1.0,
            output_distribution=(ProbabilityMass(label="x", probability=0.5),),
            confidence=0.5,
            reliability=1.0,
        )


def test_route_signature_is_order_stable_and_content_addressed() -> None:
    left = _cycles("value", "entity")
    right = _cycles("entity", "value")
    assert route_signature(left) == route_signature(right)
    assert len(signature_sha256(route_signature(left))) == 64


def test_telemetry_record_rejects_a_route_that_does_not_describe_cycles() -> None:
    payload = _record("bad-route", correct=True).model_dump()
    payload["route_signature"] = "C0:VALUE\nHALT:halt:passed"
    with pytest.raises(ValidationError, match="route signature"):
        TelemetryRecord.model_validate(payload)


def test_sampling_retains_mandatory_classes_and_drops_unsampled_success() -> None:
    sampler = DeterministicSampler(
        SamplingPolicy(high_uncertainty_threshold=0.7, confident_success_sample_rate=0.0)
    )
    first = sampler.decide(
        case_id="first", route_sha256="a" * 64, final_correctness=True, maximum_uncertainty=0.1
    )
    assert first.reasons == ("novel_route",)
    quiet = sampler.decide(
        case_id="quiet", route_sha256="a" * 64, final_correctness=True, maximum_uncertainty=0.1
    )
    assert not quiet.sampled
    failure = sampler.decide(
        case_id="failure", route_sha256="a" * 64, final_correctness=False, maximum_uncertainty=0.1
    )
    uncertain = sampler.decide(
        case_id="uncertain", route_sha256="a" * 64, final_correctness=True, maximum_uncertainty=0.9
    )
    assert "failure" in failure.reasons
    assert "high_uncertainty" in uncertain.reasons


def test_observer_writes_only_sampled_records(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.jsonl"
    observer = ResearchObserver(
        JsonlObserverSink(path),
        DeterministicSampler(SamplingPolicy(confident_success_sample_rate=0.0)),
    )
    first = observer.observe(
        case_id="case-1",
        partition="development",
        tier="10k",
        cycles=_cycles("entity"),
        final_correctness=True,
        final_semantic_correctness=True,
        final_provenance_correctness=True,
    )
    second = observer.observe(
        case_id="case-2",
        partition="development",
        tier="10k",
        cycles=_cycles("entity"),
        final_correctness=True,
        final_semantic_correctness=True,
        final_provenance_correctness=True,
    )
    assert first is not None and second is None
    assert load_jsonl(path) == (first,)


def test_analysis_covers_routes_experts_depth_calibration_and_hidden_state() -> None:
    records = (
        _record("a", correct=True, expert_ids=("entity",), depth=1, tier="10k"),
        _record("b", correct=False, expert_ids=("entity", "value"), depth=2, tier="50k"),
        _record("c", correct=True, expert_ids=("entity", "value"), depth=2, tier="397k"),
    )
    report = analyze_records(records)
    assert report["case_count"] == 3
    assert report["depth_distribution"]["counts"] == {"1": 1, "2": 2}
    assert report["expert_utilization"]["cases_active"] == {"entity": 3, "value": 2}
    assert len(report["correctness_and_compute_by_route"]) == 2
    assert "entity" in report["hidden_state_pca_svd"]
    assert report["uncertainty_calibration"]["case_count"] == 3
    assert len(report["uncertainty_calibration"]["risk_coverage"]) == 3
    assert report["uncertainty_calibration"]["entropy_vs_correctness"]
    assert report["uncertainty_calibration"]["disagreement_vs_correctness"]


def test_pca_and_clustering_are_deterministic() -> None:
    vectors = ((0.0, 0.0), (0.1, 0.0), (10.0, 10.0), (10.1, 10.0))
    pca = pca_svd(vectors)
    clustered = hidden_state_clustering(vectors, clusters=2)
    assert pca["explained_variance_ratio"][0] > 0.99
    assert clustered["assignments"][:2] == [0, 0]
    assert clustered["assignments"][2:] == [1, 1]


def _outcome(*, correct: bool, route: str = "actual", cycles: int = 1) -> CounterfactualOutcome:
    return CounterfactualOutcome(
        route_signature=route,
        semantic_correctness=correct,
        provenance_correctness=True,
        accepted=correct,
        active_macs=100 * cycles,
        cycles=cycles,
    )


@pytest.mark.parametrize(
    ("kind", "expected"),
    (
        (InterventionKind.FORCE_ENTITY_ON, CausalAttribution.GATE_FAILURE),
        (InterventionKind.FORCE_VALUE_OFF, CausalAttribution.EXPERT_FAILURE),
        (InterventionKind.BYPASS_FUSION, CausalAttribution.FUSION_FAILURE),
        (InterventionKind.FORCE_ADDITIONAL_CYCLE, CausalAttribution.INSUFFICIENT_DEPTH),
        (InterventionKind.STOP_ONE_CYCLE_EARLIER, CausalAttribution.EXCESSIVE_DEPTH),
        (InterventionKind.SELECT_ALTERNATE_ENTITY, CausalAttribution.BAD_UPSTREAM_STATE),
    ),
)
def test_counterfactuals_attribute_known_ablation_failures(
    kind: InterventionKind, expected: CausalAttribution
) -> None:
    intervention = CounterfactualIntervention(kind=kind)
    rows = CounterfactualRunner().compare(
        case_id="case",
        partition="development",
        actual=_outcome(correct=False),
        interventions=(intervention,),
        replay=lambda _: _outcome(correct=True, route="counterfactual", cycles=2),
    )
    assert rows[0].attribution is expected
    assert rows[0].correctness_delta == 1


def test_counterfactual_replay_rejects_sealed_partitions() -> None:
    with pytest.raises(ValueError, match="development/tuning"):
        CounterfactualRunner().compare(
            case_id="sealed",
            partition="evaluation",
            actual=_outcome(correct=False),
            interventions=(),
            replay=lambda _: _outcome(correct=True),
        )


def test_counterfactual_analysis_marks_over_and_under_deep_routes() -> None:
    runner = CounterfactualRunner()
    records = []
    for kind in (
        InterventionKind.FORCE_ADDITIONAL_CYCLE,
        InterventionKind.STOP_ONE_CYCLE_EARLIER,
    ):
        records.extend(
            runner.compare(
                case_id=kind.value,
                partition="tuning",
                actual=_outcome(correct=False),
                interventions=(CounterfactualIntervention(kind=kind),),
                replay=lambda _: _outcome(correct=True, route="counterfactual", cycles=2),
            )
        )
    analysis = counterfactual_analysis(records)
    assert analysis["causal_improvement_count"] == 2
    assert analysis["under_deep_signatures"] == ["actual"]
    assert analysis["over_deep_signatures"] == ["actual"]


def _registry() -> ArchitectureRegistry:
    return ArchitectureRegistry(
        architecture_id="aethercore-v11-observer",
        architecture_version="11.0.0",
        modules=(
            ArchitectureModule(
                module_id="exact-controller",
                module_version="10.0.0",
                purpose="Exact evidence control and provenance verification",
                inputs=("query_frame", "evidence_graph"),
                outputs=("verified_answer",),
                parameter_count=0,
                quantization="none",
                activation_cost=ActivationCost(
                    integer_ops=0, macs=0, memory_bytes=0, scratch_ram_bytes=0
                ),
                supported_state_types=("symbolic",),
                dependencies=(),
                model_hash="none",
                known_failure_clusters=("ENTITY_BINDING_WRONG", "VALUE_NOT_ENUMERATED"),
                status="active",
            ),
        ),
    )


def test_registry_is_versioned_hashed_and_tamper_evident(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    sealed = write_registry(path, _registry())
    assert load_registry(path) == sealed
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["architecture_version"] = "tampered"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_registry(path)


def test_committed_observer_registry_is_sealed() -> None:
    repository = Path(__file__).resolve().parents[2]
    registry = load_registry(
        repository / "config" / "architecture" / "aethercore-v11-observer.registry.json"
    )
    assert registry.architecture_version == "11.0.0-observer.1"
    assert {module.status for module in registry.modules} == {"active", "training_only"}


def test_offline_proposal_names_registry_module_and_cannot_activate_itself() -> None:
    proposal = make_proposal(
        registry=_registry(),
        observed_weakness="entity route under-depth",
        affected_module="exact-controller",
        evidence=("route:abc accuracy=0.4",),
        proposed_intervention="evaluate an additional entity cycle offline",
        expected_benefit="improve supported correctness on the cluster",
        expected_compute_change_macs=10_000,
        expected_storage_change_bytes=2_048,
        tests_required=("isolated training", "regression", "shadow evaluation"),
        candidate_version_id="exact-controller-10.0.1-candidate",
    )
    assert proposal.status == "proposed"
    assert proposal.proposal_id.startswith("proposal:")
    with pytest.raises(ValueError, match="unknown module"):
        make_proposal(
            registry=_registry(),
            observed_weakness="x",
            affected_module="missing",
            evidence=("x",),
            proposed_intervention="x",
            expected_benefit="x",
            expected_compute_change_macs=0,
            expected_storage_change_bytes=0,
            tests_required=("x",),
            candidate_version_id="x",
        )
