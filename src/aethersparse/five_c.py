"""5C root-constraint substrate.

5C is deliberately not a learned policy.  It is the immutable, fail-closed
boundary beneath deliberation.  Contextual ethical/social interpretation may
recommend an action, but it cannot weaken a root decision.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType


class ConstraintClass(StrEnum):
    ROOT_INVARIANT = "ROOT_INVARIANT"
    CAPABILITY_BOUNDARY = "CAPABILITY_BOUNDARY"
    PERMISSION_RULE = "PERMISSION_RULE"
    VERIFIER_INTEGRITY = "VERIFIER_INTEGRITY"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    PHYSICAL_HARD_LIMIT = "PHYSICAL_HARD_LIMIT"
    SELF_MODIFICATION_BOUNDARY = "SELF_MODIFICATION_BOUNDARY"
    ROLLBACK_REQUIREMENT = "ROLLBACK_REQUIREMENT"
    FAIL_CLOSED = "FAIL_CLOSED"


class BoundaryAction(StrEnum):
    EXECUTE = "EXECUTE"
    USE_TOOL = "USE_TOOL"
    ALLOCATE_RESOURCE = "ALLOCATE_RESOURCE"
    PHYSICAL_COMMAND = "PHYSICAL_COMMAND"
    ACTIVATE_COMPONENT = "ACTIVATE_COMPONENT"
    INTEGRATE_SELF_GENERATED = "INTEGRATE_SELF_GENERATED"
    REWRITE_EVIDENCE = "REWRITE_EVIDENCE"
    BYPASS_VERIFIER = "BYPASS_VERIFIER"
    REWRITE_ROOT = "REWRITE_ROOT"
    PRUNE_ROOT = "PRUNE_ROOT"
    PRUNE_OPTIONAL_SPECIALIST = "PRUNE_OPTIONAL_SPECIALIST"


@dataclass(frozen=True)
class RootConstraint:
    constraint_id: str
    kind: ConstraintClass
    subject: str
    allowed_capabilities: frozenset[str] = frozenset()
    allowed_tools: frozenset[str] = frozenset()
    resource_limit: int | None = None
    physical_min: int | None = None
    physical_max: int | None = None
    immutable: bool = True

    def canonical(self) -> dict[str, object]:
        return {
            "allowed_capabilities": sorted(self.allowed_capabilities),
            "allowed_tools": sorted(self.allowed_tools),
            "constraint_id": self.constraint_id,
            "immutable": self.immutable,
            "kind": self.kind,
            "physical_max": self.physical_max,
            "physical_min": self.physical_min,
            "resource_limit": self.resource_limit,
            "subject": self.subject,
        }


@dataclass(frozen=True)
class BoundaryRequest:
    action: BoundaryAction
    subject: str
    capability: str | None = None
    tool: str | None = None
    resource_amount: int | None = None
    physical_value: int | None = None
    externally_authorized: bool = False
    signed_update: bool = False
    sandboxed: bool = False
    tested: bool = False
    rollback_available: bool = False


@dataclass(frozen=True)
class BoundaryDecision:
    allowed: bool
    reason: str
    violated_constraint_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextualPolicyRecommendation:
    """Advisory result above 5C; it has no authority to override a denial."""

    action: BoundaryAction
    confidence_q15: int
    rationale_code: str


@dataclass(frozen=True)
class FiveCControllerView:
    schema: str
    root_digest: str
    constraint_count: int
    violation_count: int


@dataclass(frozen=True)
class FiveCSubstrate:
    """Immutable root constraints and an append-only violation accounting view."""

    constraints: tuple[RootConstraint, ...]
    violation_count: int = 0
    _by_id: Mapping[str, RootConstraint] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        identifiers = [item.constraint_id for item in self.constraints]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("5C constraint IDs must be unique")
        required = set(ConstraintClass)
        present = {item.kind for item in self.constraints}
        if not required.issubset(present):
            missing = ", ".join(sorted(item.value for item in required - present))
            raise ValueError(f"5C root substrate is incomplete: {missing}")
        object.__setattr__(self, "_by_id", MappingProxyType({
            item.constraint_id: item for item in self.constraints
        }))

    @property
    def digest(self) -> str:
        payload = json.dumps(
            [
                item.canonical()
                for item in sorted(self.constraints, key=lambda item: item.constraint_id)
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def controller_view(self) -> FiveCControllerView:
        return FiveCControllerView(
            schema="aethercore.5c-controller-view.v1",
            root_digest=self.digest,
            constraint_count=len(self.constraints),
            violation_count=self.violation_count,
        )

    def authorize(
        self,
        request: BoundaryRequest,
        contextual: ContextualPolicyRecommendation | None = None,
    ) -> BoundaryDecision:
        """Evaluate root constraints first; contextual advice can only narrow allowance."""

        violated: list[str] = []
        by_kind = {item.kind: item for item in self.constraints}
        immutable_actions = {
            BoundaryAction.REWRITE_EVIDENCE: ConstraintClass.ROOT_INVARIANT,
            BoundaryAction.BYPASS_VERIFIER: ConstraintClass.VERIFIER_INTEGRITY,
            BoundaryAction.REWRITE_ROOT: ConstraintClass.SELF_MODIFICATION_BOUNDARY,
            BoundaryAction.PRUNE_ROOT: ConstraintClass.SELF_MODIFICATION_BOUNDARY,
        }
        if request.action in immutable_actions:
            violated.append(by_kind[immutable_actions[request.action]].constraint_id)

        if request.action is BoundaryAction.INTEGRATE_SELF_GENERATED and not (
            request.externally_authorized
            and request.signed_update
            and request.sandboxed
            and request.tested
            and request.rollback_available
        ):
            violated.extend(
                [
                    by_kind[ConstraintClass.SELF_MODIFICATION_BOUNDARY].constraint_id,
                    by_kind[ConstraintClass.ROLLBACK_REQUIREMENT].constraint_id,
                ]
            )

        if request.capability is not None:
            capability = by_kind[ConstraintClass.CAPABILITY_BOUNDARY]
            if request.capability not in capability.allowed_capabilities:
                violated.append(capability.constraint_id)
        if request.tool is not None:
            permission = by_kind[ConstraintClass.PERMISSION_RULE]
            if request.tool not in permission.allowed_tools:
                violated.append(permission.constraint_id)
        if request.resource_amount is not None:
            resource = by_kind[ConstraintClass.RESOURCE_LIMIT]
            if (
                request.resource_amount < 0
                or resource.resource_limit is None
                or request.resource_amount > resource.resource_limit
            ):
                violated.append(resource.constraint_id)
        if request.physical_value is not None:
            physical = by_kind[ConstraintClass.PHYSICAL_HARD_LIMIT]
            if (
                physical.physical_min is None
                or physical.physical_max is None
                or not physical.physical_min <= request.physical_value <= physical.physical_max
            ):
                violated.append(physical.constraint_id)

        violated = list(dict.fromkeys(violated))
        if violated:
            return BoundaryDecision(False, "ROOT_CONSTRAINT_DENIED", tuple(violated))
        if contextual is not None and contextual.action is not request.action:
            return BoundaryDecision(False, "CONTEXTUAL_POLICY_DID_NOT_AUTHORIZE_ACTION")
        return BoundaryDecision(True, "ROOT_CONSTRAINTS_SATISFIED")

    def verify_integrity(self, expected_digest: str) -> bool:
        return self.digest == expected_digest

    def externally_signed_update(
        self,
        constraints: tuple[RootConstraint, ...],
        *,
        externally_authorized: bool,
        signed_update: bool,
        rollback_available: bool,
    ) -> FiveCSubstrate:
        """Only the external update path can replace root state."""

        if not externally_authorized or not signed_update or not rollback_available:
            raise PermissionError(
                "root update requires external signature, authority, and rollback"
            )
        return FiveCSubstrate(constraints=constraints, violation_count=self.violation_count)


def default_five_c() -> FiveCSubstrate:
    """Return the minimal complete V14 root set used by host and edge runtimes."""

    return FiveCSubstrate(
        constraints=(
            RootConstraint("5c.root.provenance", ConstraintClass.ROOT_INVARIANT, "evidence"),
            RootConstraint(
                "5c.capability.default",
                ConstraintClass.CAPABILITY_BOUNDARY,
                "controller",
                allowed_capabilities=frozenset(
                    {"qa", "retrieval", "verification", "sandbox_tool", "sensor", "actuator"}
                ),
            ),
            RootConstraint(
                "5c.permission.default",
                ConstraintClass.PERMISSION_RULE,
                "tools",
                allowed_tools=frozenset(
                    {
                        "SEARCH_KNOWLEDGE",
                        "SEARCH_SOURCE",
                        "READ_FILE",
                        "LIST_TREE",
                        "CREATE_SANDBOX",
                        "CREATE_BRANCH_OR_WORKTREE",
                        "WRITE_PATCH",
                        "APPLY_PATCH",
                        "BUILD",
                        "RUN_TESTS",
                        "INSPECT_FAILURE",
                        "REVERT",
                        "REPORT_RESULT",
                        "REQUEST_INTEGRATION",
                    }
                ),
            ),
            RootConstraint(
                "5c.verifier.integrity", ConstraintClass.VERIFIER_INTEGRITY, "verifier"
            ),
            RootConstraint(
                "5c.resource.resident_bytes",
                ConstraintClass.RESOURCE_LIMIT,
                "resident_bytes",
                resource_limit=4 * 1024 * 1024,
            ),
            RootConstraint(
                "5c.physical.command_q15",
                ConstraintClass.PHYSICAL_HARD_LIMIT,
                "actuator_command_q15",
                physical_min=-32768,
                physical_max=32767,
            ),
            RootConstraint(
                "5c.selfmod.boundary",
                ConstraintClass.SELF_MODIFICATION_BOUNDARY,
                "active_runtime",
            ),
            RootConstraint(
                "5c.rollback.required", ConstraintClass.ROLLBACK_REQUIREMENT, "updates"
            ),
            RootConstraint("5c.fail.closed", ConstraintClass.FAIL_CLOSED, "unknown_state"),
        )
    )
