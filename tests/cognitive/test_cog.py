from __future__ import annotations

import pytest
from pydantic import ValidationError

from aethersparse.cognitive.graph import (
    add_evidence,
    add_obligation,
    can_halt_success,
    compact_view,
    expand_frontier,
    mark_goal_satisfied,
    record_progress,
    recovery_actions,
    transition_obligation,
    update_frontier,
    verify_invariant,
)
from aethersparse.cognitive.models import (
    CognitiveObligationGraph,
    Evidence,
    FrontierItem,
    FrontierStatus,
    Goal,
    GoalType,
    Hypothesis,
    Invariant,
    InvariantStatus,
    Obligation,
    ObligationStatus,
    Provenance,
    ProvenanceKind,
    RecoveryAction,
    UnresolvedVariable,
)


def _source() -> Provenance:
    return Provenance(kind=ProvenanceKind.USER_INPUT, source_id="turn-1")


def _graph() -> CognitiveObligationGraph:
    source = _source()
    return CognitiveObligationGraph(
        cog_id="cog:test",
        goals=(
            Goal(
                goal_id="goal",
                goal_type=GoalType.QUESTION_ANSWERING,
                description="Answer the question",
                provenance=source,
            ),
        ),
        obligations=(
            Obligation(
                obligation_id="subject",
                goal_id="goal",
                kind="IDENTIFY_SUBJECT",
                description="Identify subject",
                provenance=source,
            ),
            Obligation(
                obligation_id="verify",
                goal_id="goal",
                kind="VERIFY",
                description="Verify answer",
                provenance=source,
                depends_on=("subject",),
            ),
        ),
        invariants=(
            Invariant(
                invariant_id="provenance",
                kind="PROVENANCE",
                description="Keep provenance",
                provenance=source,
            ),
        ),
        unresolved=(
            UnresolvedVariable(
                variable_id="subject-var",
                kind="ENTITY",
                description="subject",
                required_by_obligation_ids=("subject",),
            ),
        ),
        frontier=(
            FrontierItem(
                frontier_id="claims",
                kind="SEARCH",
                target="subject",
                obligation_ids=("subject",),
            ),
        ),
    )


def test_cog_is_bounded_and_referentially_closed() -> None:
    graph = _graph()
    assert graph.schema_version == "aethercore.cog.v1"
    with pytest.raises(ValidationError, match="unknown goal"):
        CognitiveObligationGraph(
            cog_id="bad",
            obligations=(
                Obligation(
                    obligation_id="bad",
                    goal_id="missing",
                    kind="BAD",
                    description="bad reference",
                    provenance=_source(),
                ),
            ),
        )
    with pytest.raises(ValidationError, match="at most 8 items"):
        CognitiveObligationGraph(
            cog_id="too-many",
            goals=tuple(
                Goal(
                    goal_id=f"g{index}",
                    goal_type=GoalType.GENERAL,
                    description="bounded",
                    provenance=_source(),
                )
                for index in range(9)
            ),
        )


def test_canonical_serialization_and_compact_view_are_deterministic() -> None:
    graph = _graph()
    assert graph.canonical_bytes() == graph.canonical_bytes()
    view = compact_view(graph)
    assert view.mandatory_open == 2
    assert view.completion_permille == 0
    assert view.halt_success_legal == 0
    assert len(view.packed_u16()) == 19


def test_halt_success_is_forbidden_until_obligations_and_invariants_pass() -> None:
    graph = _graph()
    with pytest.raises(ValueError, match="HALT_SUCCESS forbidden"):
        mark_goal_satisfied(graph, "goal")
    graph = transition_obligation(graph, "subject", ObligationStatus.SATISFIED)
    graph = transition_obligation(graph, "verify", ObligationStatus.SATISFIED)
    assert can_halt_success(graph)
    done = mark_goal_satisfied(graph, "goal")
    assert done.goals[0].status.value == "SATISFIED"
    violated = graph.model_copy(
        update={
            "invariants": (
                graph.invariants[0].model_copy(update={"status": InvariantStatus.VIOLATED}),
            )
        }
    )
    assert not can_halt_success(violated)


def test_obligation_dependencies_and_generic_graph_operations_fail_closed() -> None:
    graph = _graph()
    with pytest.raises(ValueError, match="dependencies remain unresolved"):
        transition_obligation(graph, "verify", ObligationStatus.SATISFIED)
    added = add_obligation(
        graph,
        Obligation(
            obligation_id="report",
            goal_id="goal",
            kind="REPORT_RESULT",
            description="Report the verified result",
            provenance=_source(),
            depends_on=("verify",),
        ),
    )
    assert added.obligations[-1].obligation_id == "report"
    with pytest.raises(ValueError, match="already exists"):
        add_obligation(added, added.obligations[-1])
    violated = verify_invariant(graph, "provenance", passed=False, evidence_ids=("e1",))
    assert violated.invariants[0].status is InvariantStatus.VIOLATED
    assert violated.invariants[0].violation_evidence_ids == ("e1",)
    restored = verify_invariant(violated, "provenance", passed=True)
    assert restored.invariants[0].status is InvariantStatus.ACTIVE
    assert restored.invariants[0].violation_evidence_ids == ()


def test_verifier_required_invariant_requires_accepted_runtime_state() -> None:
    source = _source()
    graph = CognitiveObligationGraph(
        cog_id="cog:verified",
        goals=(
            Goal(
                goal_id="goal",
                goal_type=GoalType.QUESTION_ANSWERING,
                description="answer",
                provenance=source,
            ),
        ),
        obligations=(
            Obligation(
                obligation_id="verify",
                goal_id="goal",
                kind="VERIFY_EVIDENCE",
                description="verify",
                status=ObligationStatus.SATISFIED,
                provenance=source,
            ),
        ),
        invariants=(
            Invariant(
                invariant_id="exact-verifier",
                kind="VERIFIER_REQUIRED",
                description="verifier must accept",
                provenance=source,
            ),
        ),
    )
    assert not can_halt_success(graph)
    accepted = graph.model_copy(
        update={"progress": graph.progress.model_copy(update={"verifier_state": "ACCEPTED"})}
    )
    assert can_halt_success(accepted)


def test_evidence_is_append_only_and_obligation_can_be_reopened() -> None:
    graph = transition_obligation(
        _graph(), "subject", ObligationStatus.SATISFIED, satisfied_by=("e1",)
    )
    evidence = Evidence(
        evidence_id="e1",
        subject="Alan Turing",
        predicate="birthplace",
        value="Maida Vale",
        provenance=Provenance(kind=ProvenanceKind.CORPUS_EVIDENCE, source_id="span:1"),
    )
    graph = add_evidence(graph, evidence)
    with pytest.raises(ValueError, match="cannot be rewritten"):
        add_evidence(graph, evidence.model_copy(update={"value": "London"}))
    reopened = transition_obligation(graph, "subject", ObligationStatus.OPEN)
    assert reopened.obligations[0].satisfied_by == ()


def test_frontier_can_expand_suspend_resume_prune_and_complete() -> None:
    graph = expand_frontier(
        _graph(), FrontierItem(frontier_id="source-2", kind="SOURCE", target="source:2")
    )
    for status in (
        FrontierStatus.SUSPENDED,
        FrontierStatus.OPEN,
        FrontierStatus.PRUNED,
        FrontierStatus.COMPLETE,
    ):
        graph = update_frontier(graph, "source-2", status)
        assert graph.frontier[-1].status is status


def test_progress_requires_material_change_and_exposes_bounded_recovery() -> None:
    graph = _graph().model_copy(
        update={
            "hypotheses": (
                Hypothesis(
                    hypothesis_id="h1",
                    kind="ENTITY",
                    interpretation="Mercury planet",
                    confidence_milli=500,
                    provenance=_source(),
                ),
                Hypothesis(
                    hypothesis_id="h2",
                    kind="ENTITY",
                    interpretation="Mercury element",
                    confidence_milli=500,
                    provenance=_source(),
                ),
            )
        }
    )
    for _ in range(3):
        graph = record_progress(
            graph,
            graph,
            action="INSPECT_CLAIM",
            error_signature="wrong-relation",
        )
    assert graph.progress.stagnant_steps == 3
    assert graph.progress.repeated_action_count == 2
    assert graph.progress.repeated_error_count == 2
    assert recovery_actions(graph) == (
        RecoveryAction.REASSESS_HYPOTHESIS,
        RecoveryAction.TRY_ALTERNATIVE,
        RecoveryAction.EXPAND_FRONTIER,
        RecoveryAction.ROLLBACK,
        RecoveryAction.ASK_CLARIFICATION,
        RecoveryAction.ABSTAIN_BLOCKED,
    )
    progressed = transition_obligation(graph, "subject", ObligationStatus.SATISFIED)
    progressed = record_progress(graph, progressed, action="SATISFY_OBLIGATION")
    assert progressed.progress.stagnant_steps == 0
    assert progressed.progress.obligations_completed == 1
