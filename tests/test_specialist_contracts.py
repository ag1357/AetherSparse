from __future__ import annotations

import pytest

from aethersparse.five_c import BoundaryAction, BoundaryRequest, default_five_c
from aethersparse.specialist_contracts import (
    ActivationState,
    ProvenanceBehavior,
    SharedNanoKernel,
    SpecialistDescriptor,
    SpecialistKind,
    SpecialistRegistry,
)


def descriptor(identifier: str, calibration: tuple[int, ...]) -> SpecialistDescriptor:
    return SpecialistDescriptor(
        specialist_id=identifier,
        capability="actuator",
        input_schema="aethercore.synthetic-joint-state.v1",
        output_schema="aethercore.synthetic-joint-command.v1",
        parameter_family_id="family:joint-residual:v1",
        instance_calibration_state=calibration,
        activation_cost_ops=7,
        ram_requirement_bytes=128,
        storage_requirement_bytes=0,
        expected_latency_us=25,
        allowed_tools=frozenset(),
        allowed_actions=frozenset({"PREDICT_RESIDUAL"}),
        five_c_constraint_ids=frozenset({"5c.physical.command_q15"}),
        provenance_behavior=ProvenanceBehavior.APPEND_DERIVATION,
        kind=SpecialistKind.SHARED_LEARNED,
    )


def test_n_instances_share_one_parameter_family_with_local_calibration() -> None:
    root = default_five_c()
    registry = SpecialistRegistry(root, ram_budget_bytes=12 * 128)
    instances = [descriptor(f"joint-{index}", (index, -index)) for index in range(12)]
    for item in instances:
        registry.register(item)
        registry.transition(item.specialist_id, ActivationState.WARM)
    registry.transition("joint-4", ActivationState.HOT)
    assert {item.parameter_family_id for item in instances} == {"family:joint-residual:v1"}
    assert registry.resident_ram_bytes == 12 * 128
    assert registry.states["joint-4"] is ActivationState.HOT

    shared = SharedNanoKernel(
        parameter_family_id="family:joint-residual:v1",
        weights_q7=(3, -2),
        bias=4,
        hard_min=-100,
        hard_max=100,
    )
    outputs = [
        shared.evaluate((10, 5), item.instance_calibration_state, context=1)
        for item in instances
    ]
    assert len(set(outputs)) == 12
    assert shared.evaluate((10_000, -10_000), (0, 0), 0) == 100


def test_activation_enforces_capability_root_and_ram_budget() -> None:
    registry = SpecialistRegistry(default_five_c(), ram_budget_bytes=128)
    registry.register(descriptor("joint-0", (0, 0)))
    registry.register(descriptor("joint-1", (0, 0)))
    registry.transition("joint-0", ActivationState.WARM)
    with pytest.raises(MemoryError):
        registry.transition("joint-1", ActivationState.WARM)


def test_optional_specialist_can_prune_but_root_cannot() -> None:
    registry = SpecialistRegistry(default_five_c(), ram_budget_bytes=128)
    registry.register(descriptor("joint-0", (0, 0)))
    registry.prune("joint-0")
    assert not registry.descriptors
    assert not registry.substrate.authorize(
        # The registry has no root-prune API; direct boundary request remains denied.
        BoundaryRequest(action=BoundaryAction.PRUNE_ROOT, subject="5c.verifier.integrity")
    ).allowed
