from __future__ import annotations

import pytest
from pydantic import ValidationError

from aethersparse.specialists.gating import (
    AdaptiveDepthController,
    CounterfactualDepthRecord,
    DepthBounds,
    GateFeatureVector,
    HaltReason,
    LinearGateParameters,
    SpecialistProposal,
    route_signature,
)
from aethersparse.specialists.p4_cost import (
    P4Assumptions,
    P4OperationCost,
    clock_sensitivity,
    project_p4,
)
from aethersparse.specialists.workspace import ComputeBudget, SharedWorkspace


def _workspace(*, macs: int = 1_000_000, cycle: int = 0) -> SharedWorkspace:
    return SharedWorkspace(
        evidence_sufficiency=0.2,
        cycle_count=cycle,
        compute_budget=ComputeBudget(
            active_macs_remaining=macs,
            read_operations_remaining=4,
            cycles_remaining=6 - cycle,
        ),
    )


def test_linear_gate_has_fixed_width_and_calibrated_probability() -> None:
    features = GateFeatureVector(**{name: 0.5 for name in GateFeatureVector.model_fields})
    parameters = LinearGateParameters(
        expert_id="entity",
        weights=tuple(0.0 for _ in GateFeatureVector.model_fields),
        bias=0.0,
    )
    assert parameters.expected_gain(features) == pytest.approx(0.5)
    with pytest.raises(ValidationError, match="exactly"):
        LinearGateParameters(expert_id="bad", weights=(1.0,))


def test_router_groups_independent_experts_then_dependency() -> None:
    proposals = (
        SpecialistProposal(
            expert_id="entity",
            expected_correctness_gain=0.4,
            active_parameters=250_000,
            active_macs=100_000,
        ),
        SpecialistProposal(
            expert_id="relation",
            expected_correctness_gain=0.3,
            active_parameters=100_000,
            active_macs=50_000,
        ),
        SpecialistProposal(
            expert_id="value",
            expected_correctness_gain=0.2,
            active_parameters=500_000,
            active_macs=200_000,
            dependencies=("entity",),
        ),
    )
    route = AdaptiveDepthController(
        DepthBounds(max_total_active_macs=500_000, max_total_read_operations=4)
    ).route(_workspace(), proposals)
    assert route.parallel_groups == (("entity", "relation"), ("value",))
    assert route.active_macs == 350_000
    assert route.halt is False
    assert route.halt_reason == HaltReason.ROUTE_SELECTED


def test_router_halts_on_gain_cycle_and_compute_bounds() -> None:
    controller = AdaptiveDepthController(
        DepthBounds(
            max_cycles=2,
            max_total_active_macs=1_000,
            max_total_read_operations=0,
            minimum_expected_gain=0.1,
        )
    )
    low = SpecialistProposal(
        expert_id="entity",
        expected_correctness_gain=0.01,
        active_parameters=1,
        active_macs=1,
    )
    assert controller.route(_workspace(), (low,)).halt_reason == HaltReason.EXPECTED_GAIN_BELOW_GATE
    assert (
        controller.route(_workspace(cycle=2), (low,)).halt_reason
        == HaltReason.CYCLE_BOUND_REACHED
    )
    costly = low.model_copy(update={"expected_correctness_gain": 0.5, "active_macs": 2_000})
    assert (
        controller.route(_workspace(), (costly,)).halt_reason
        == HaltReason.COMPUTE_BUDGET_EXHAUSTED
    )


def test_route_signature_is_deterministic_and_order_sensitive() -> None:
    first = route_signature((("entity", "relation"), ("value",)))
    second = route_signature((("entity", "relation"), ("value",)))
    reordered = route_signature((("relation", "entity"), ("value",)))
    assert first == second
    assert first[1] != reordered[1]


def test_counterfactual_labels_reject_protected_partitions() -> None:
    record = CounterfactualDepthRecord(
        case_id="case:1",
        partition="development",
        halt_supported_correct=False,
        plus_one_supported_correct=True,
        improving_specialist="entity",
        incremental_active_macs=10,
        incremental_read_operations=0,
    )
    assert record.correctness_gain == 1
    with pytest.raises(ValidationError, match="training/calibration"):
        record.model_copy(update={"partition": "evaluation"}).model_validate(
            {**record.model_dump(), "partition": "evaluation"}
        )


def test_p4_projection_is_analytical_and_clock_sensitive() -> None:
    costs = (
        P4OperationCost(
            operation_id="entity.int8",
            integer_operations=200_000,
            macs=1_000_000,
            memory_bytes=100_000,
            psram_bytes=100_000,
            flash_bytes=50_000,
            psram_accesses=10,
            flash_accesses=2,
            random_psram_reads=2,
            random_flash_reads=1,
            sequential_reads=1,
            scratch_ram_bytes=64_000,
            model_bytes=250_000,
        ),
    )
    assumptions = P4Assumptions(
        clock_mhz=200,
        integer_ops_per_cycle=1.0,
        macs_per_cycle=1.0,
        psram_bandwidth_mb_s=20.0,
        flash_bandwidth_mb_s=10.0,
        psram_random_access_us=10.0,
        flash_random_access_us=100.0,
    )
    projection = project_p4(costs, assumptions)
    assert projection.evidence_class == "analytical_projection_not_hardware_measurement"
    assert projection.compute_ms == pytest.approx(6.0)
    assert projection.virtual_latency_ms == pytest.approx(16.12)
    clocks = clock_sensitivity(
        costs,
        integer_ops_per_cycle=1.0,
        macs_per_cycle=1.0,
        psram_bandwidth_mb_s=20.0,
        flash_bandwidth_mb_s=10.0,
        psram_random_access_us=10.0,
        flash_random_access_us=100.0,
    )
    assert tuple(item.clock_mhz for item in clocks) == (200, 300, 400)
    assert (
        clocks[0].virtual_latency_ms
        > clocks[1].virtual_latency_ms
        > clocks[2].virtual_latency_ms
    )
