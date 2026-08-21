"""Fail-closed immutable COG transitions and deterministic progress accounting."""

from __future__ import annotations

from collections.abc import Sequence

from aethersparse.cognitive.models import (
    CognitiveObligationGraph,
    CompactCOGView,
    Evidence,
    FrontierItem,
    FrontierStatus,
    GoalStatus,
    Hypothesis,
    InvariantStatus,
    Obligation,
    ObligationStatus,
    ProgressState,
    RecoveryAction,
)

_VERIFIER_CODES = {"NOT_RUN": 0, "PENDING": 1, "ACCEPTED": 2, "REJECTED": 3}


def can_halt_success(cog: CognitiveObligationGraph) -> bool:
    mandatory = [item for item in cog.obligations if item.mandatory]
    verifier_required = any(item.kind == "VERIFIER_REQUIRED" for item in cog.invariants)
    verifier_ready = not verifier_required or cog.progress.verifier_state == "ACCEPTED"
    goals_eligible = bool(cog.goals) and all(
        item.status in {GoalStatus.OPEN, GoalStatus.SATISFIED} for item in cog.goals
    )
    return (
        goals_eligible
        and all(item.status is ObligationStatus.SATISFIED for item in mandatory)
        and all(item.status is not InvariantStatus.VIOLATED for item in cog.invariants)
        and verifier_ready
    )


def compact_view(cog: CognitiveObligationGraph) -> CompactCOGView:
    mandatory = [item for item in cog.obligations if item.mandatory]
    satisfied = sum(item.status is ObligationStatus.SATISFIED for item in mandatory)
    completion = 1000 if not mandatory else (1000 * satisfied) // len(mandatory)
    active_hypotheses = sum(item.active for item in cog.hypotheses)
    return CompactCOGView(
        open_goals=sum(item.status is GoalStatus.OPEN for item in cog.goals),
        mandatory_open=sum(item.status is ObligationStatus.OPEN for item in mandatory),
        mandatory_satisfied=satisfied,
        blocked_or_failed=sum(
            item.status in {ObligationStatus.BLOCKED, ObligationStatus.FAILED}
            for item in cog.obligations
        ),
        invariant_violations=sum(
            item.status is InvariantStatus.VIOLATED for item in cog.invariants
        ),
        active_hypotheses=active_hypotheses,
        competing_hypotheses=int(active_hypotheses > 1),
        contradictions=sum(item.contradiction for item in cog.hypotheses),
        evidence_count=len(cog.evidence),
        unresolved_count=len(cog.unresolved),
        open_frontier=sum(item.status is FrontierStatus.OPEN for item in cog.frontier),
        observed_state_count=len(cog.observed_state),
        completion_permille=completion,
        stagnant_steps=min(cog.progress.stagnant_steps, 255),
        repeated_error_count=min(cog.progress.repeated_error_count, 255),
        repeated_action_count=min(cog.progress.repeated_action_count, 255),
        verifier_state_code=_VERIFIER_CODES.get(cog.progress.verifier_state, 255),
        halt_success_legal=int(can_halt_success(cog)),
    )


def _replace_by_id(
    values: Sequence[Obligation], replacement: Obligation
) -> tuple[Obligation, ...]:
    return tuple(
        replacement if item.obligation_id == replacement.obligation_id else item
        for item in values
    )


def transition_obligation(
    cog: CognitiveObligationGraph,
    obligation_id: str,
    status: ObligationStatus,
    *,
    satisfied_by: tuple[str, ...] = (),
) -> CognitiveObligationGraph:
    try:
        current = next(item for item in cog.obligations if item.obligation_id == obligation_id)
    except StopIteration as error:
        raise KeyError(obligation_id) from error
    if status is ObligationStatus.SATISFIED:
        by_id = {item.obligation_id: item for item in cog.obligations}
        incomplete = tuple(
            dependency
            for dependency in current.depends_on
            if by_id[dependency].status is not ObligationStatus.SATISFIED
        )
        if incomplete:
            raise ValueError(
                "obligation dependencies remain unresolved: " + ", ".join(incomplete)
            )
    if current.status is ObligationStatus.SATISFIED and status is ObligationStatus.OPEN:
        # Reopening is explicit, but prior support must not be presented as current proof.
        satisfied_by = ()
    replacement = current.model_copy(update={"status": status, "satisfied_by": satisfied_by})
    return cog.model_copy(update={"obligations": _replace_by_id(cog.obligations, replacement)})


def add_obligation(
    cog: CognitiveObligationGraph, obligation: Obligation
) -> CognitiveObligationGraph:
    """Add one bounded obligation without weakening graph referential closure."""

    if any(item.obligation_id == obligation.obligation_id for item in cog.obligations):
        raise ValueError("obligation ID already exists")
    return cog.model_copy(update={"obligations": (*cog.obligations, obligation)})


def verify_invariant(
    cog: CognitiveObligationGraph,
    invariant_id: str,
    *,
    passed: bool,
    evidence_ids: tuple[str, ...] = (),
) -> CognitiveObligationGraph:
    """Record deterministic invariant verification; failure is an explicit violation."""

    if not any(item.invariant_id == invariant_id for item in cog.invariants):
        raise KeyError(invariant_id)
    status = InvariantStatus.ACTIVE if passed else InvariantStatus.VIOLATED
    invariants = tuple(
        item.model_copy(
            update={"status": status, "violation_evidence_ids": () if passed else evidence_ids}
        )
        if item.invariant_id == invariant_id
        else item
        for item in cog.invariants
    )
    return cog.model_copy(update={"invariants": invariants})


def add_evidence(cog: CognitiveObligationGraph, evidence: Evidence) -> CognitiveObligationGraph:
    if any(item.evidence_id == evidence.evidence_id for item in cog.evidence):
        raise ValueError("evidence ID already exists; immutable evidence cannot be rewritten")
    return cog.model_copy(update={"evidence": (*cog.evidence, evidence)})


def add_hypothesis(
    cog: CognitiveObligationGraph, hypothesis: Hypothesis
) -> CognitiveObligationGraph:
    if any(item.hypothesis_id == hypothesis.hypothesis_id for item in cog.hypotheses):
        raise ValueError("hypothesis ID already exists")
    return cog.model_copy(update={"hypotheses": (*cog.hypotheses, hypothesis)})


def update_frontier(
    cog: CognitiveObligationGraph, frontier_id: str, status: FrontierStatus
) -> CognitiveObligationGraph:
    if not any(item.frontier_id == frontier_id for item in cog.frontier):
        raise KeyError(frontier_id)
    values = tuple(
        item.model_copy(update={"status": status}) if item.frontier_id == frontier_id else item
        for item in cog.frontier
    )
    return cog.model_copy(update={"frontier": values})


def expand_frontier(
    cog: CognitiveObligationGraph, item: FrontierItem
) -> CognitiveObligationGraph:
    if any(current.frontier_id == item.frontier_id for current in cog.frontier):
        raise ValueError("frontier ID already exists")
    return cog.model_copy(update={"frontier": (*cog.frontier, item)})


def mark_goal_satisfied(cog: CognitiveObligationGraph, goal_id: str) -> CognitiveObligationGraph:
    if not can_halt_success(cog):
        raise ValueError("HALT_SUCCESS forbidden while obligations or invariants are unresolved")
    if not any(item.goal_id == goal_id for item in cog.goals):
        raise KeyError(goal_id)
    goals = tuple(
        item.model_copy(update={"status": GoalStatus.SATISFIED})
        if item.goal_id == goal_id
        else item
        for item in cog.goals
    )
    return cog.model_copy(update={"goals": goals})


def record_progress(
    before: CognitiveObligationGraph,
    after: CognitiveObligationGraph,
    *,
    action: str,
    error_signature: str | None = None,
    verifier_state: str | None = None,
    rollback: bool = False,
) -> CognitiveObligationGraph:
    """Account for material graph progress; three empty repeated steps stagnate."""

    before_satisfied = sum(
        item.status is ObligationStatus.SATISFIED for item in before.obligations
    )
    after_satisfied = sum(
        item.status is ObligationStatus.SATISFIED for item in after.obligations
    )
    obligation_delta = max(0, after_satisfied - before_satisfied)
    evidence_delta = max(0, len(after.evidence) - len(before.evidence))
    hypothesis_delta = max(0, len(after.hypotheses) - len(before.hypotheses))
    frontier_delta = max(0, len(after.frontier) - len(before.frontier))
    material_progress = any((obligation_delta, evidence_delta, hypothesis_delta, frontier_delta))
    prior = before.progress
    repeat_action = prior.last_action == action
    repeat_error = error_signature is not None and prior.last_error_signature == error_signature
    stagnant = 0 if material_progress else prior.stagnant_steps + 1
    updated = ProgressState(
        step_count=prior.step_count + 1,
        obligations_completed=prior.obligations_completed + obligation_delta,
        evidence_added=prior.evidence_added + evidence_delta,
        hypotheses_added=prior.hypotheses_added + hypothesis_delta,
        frontier_expansions=prior.frontier_expansions + frontier_delta,
        rollback_count=prior.rollback_count + int(rollback),
        stagnant_steps=stagnant,
        repeated_error_count=prior.repeated_error_count + int(repeat_error),
        repeated_action_count=prior.repeated_action_count + int(repeat_action),
        last_error_signature=error_signature,
        last_action=action,
        verifier_state=verifier_state or prior.verifier_state,
        recent_actions=(*prior.recent_actions, action)[-8:],
        recent_error_signatures=(
            (*prior.recent_error_signatures, error_signature)[-8:]
            if error_signature is not None
            else prior.recent_error_signatures
        ),
    )
    return after.model_copy(update={"progress": updated})


def recovery_actions(cog: CognitiveObligationGraph) -> tuple[RecoveryAction, ...]:
    if cog.progress.stagnant_steps < 3:
        return ()
    actions: list[RecoveryAction] = []
    if sum(item.active for item in cog.hypotheses) > 1:
        actions.extend((RecoveryAction.REASSESS_HYPOTHESIS, RecoveryAction.TRY_ALTERNATIVE))
    if any(item.status is FrontierStatus.OPEN for item in cog.frontier):
        actions.append(RecoveryAction.EXPAND_FRONTIER)
    if cog.progress.rollback_count == 0:
        actions.append(RecoveryAction.ROLLBACK)
    if cog.unresolved:
        actions.append(RecoveryAction.ASK_CLARIFICATION)
    actions.append(RecoveryAction.ABSTAIN_BLOCKED)
    return tuple(dict.fromkeys(actions))
