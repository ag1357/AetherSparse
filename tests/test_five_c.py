from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from aethersparse.five_c import (
    BoundaryAction,
    BoundaryRequest,
    ContextualPolicyRecommendation,
    default_five_c,
)


@pytest.mark.parametrize(
    "action",
    [
        BoundaryAction.BYPASS_VERIFIER,
        BoundaryAction.REWRITE_EVIDENCE,
        BoundaryAction.REWRITE_ROOT,
        BoundaryAction.PRUNE_ROOT,
    ],
)
def test_controller_cannot_bypass_or_rewrite_root(action: BoundaryAction) -> None:
    root = default_five_c()
    before = root.digest
    decision = root.authorize(BoundaryRequest(action=action, subject="controller"))
    assert not decision.allowed
    assert decision.violated_constraint_ids
    assert root.verify_integrity(before)


def test_contextual_policy_cannot_override_root_denial() -> None:
    root = default_five_c()
    request = BoundaryRequest(action=BoundaryAction.BYPASS_VERIFIER, subject="verifier")
    contextual = ContextualPolicyRecommendation(
        action=BoundaryAction.BYPASS_VERIFIER,
        confidence_q15=32767,
        rationale_code="learned-policy-request",
    )
    assert not root.authorize(request, contextual).allowed


def test_self_generated_component_requires_entire_update_chain() -> None:
    root = default_five_c()
    incomplete = BoundaryRequest(
        action=BoundaryAction.INTEGRATE_SELF_GENERATED,
        subject="generated.policy",
        signed_update=True,
        sandboxed=True,
        tested=True,
        rollback_available=True,
    )
    assert not root.authorize(incomplete).allowed
    complete = BoundaryRequest(
        action=BoundaryAction.INTEGRATE_SELF_GENERATED,
        subject="generated.policy",
        externally_authorized=True,
        signed_update=True,
        sandboxed=True,
        tested=True,
        rollback_available=True,
    )
    assert root.authorize(complete).allowed


def test_limits_are_deterministic_and_fail_closed() -> None:
    root = default_five_c()
    assert root.authorize(
        BoundaryRequest(
            action=BoundaryAction.PHYSICAL_COMMAND,
            subject="joint_4",
            capability="actuator",
            physical_value=32767,
        )
    ).allowed
    assert not root.authorize(
        BoundaryRequest(
            action=BoundaryAction.PHYSICAL_COMMAND,
            subject="joint_4",
            capability="actuator",
            physical_value=32768,
        )
    ).allowed


def test_sandbox_agent_tools_are_explicitly_permitted_but_unknown_tools_fail_closed() -> None:
    root = default_five_c()
    for tool in ("CREATE_SANDBOX", "WRITE_PATCH", "BUILD", "RUN_TESTS", "REVERT"):
        assert root.authorize(
            BoundaryRequest(
                action=BoundaryAction.USE_TOOL,
                subject="sandbox-agent",
                capability="sandbox_tool",
                tool=tool,
            )
        ).allowed
    assert not root.authorize(
        BoundaryRequest(
            action=BoundaryAction.USE_TOOL,
            subject="sandbox-agent",
            capability="sandbox_tool",
            tool="UNBOUNDED_SHELL",
        )
    ).allowed
    assert not root.authorize(
        BoundaryRequest(
            action=BoundaryAction.EXECUTE,
            subject="unknown",
            capability="unregistered-capability",
        )
    ).allowed


def test_root_objects_are_frozen_and_signed_update_requires_rollback() -> None:
    root = default_five_c()
    with pytest.raises(FrozenInstanceError):
        root.constraints[0].subject = "rewritten"  # type: ignore[misc]
    with pytest.raises(PermissionError):
        root.externally_signed_update(
            root.constraints,
            externally_authorized=True,
            signed_update=True,
            rollback_available=False,
        )
    updated = root.externally_signed_update(
        root.constraints,
        externally_authorized=True,
        signed_update=True,
        rollback_available=True,
    )
    assert updated.digest == root.digest
