from __future__ import annotations

import pytest

from aethersparse.agent.contracts import (
    AnswerKind,
    AnswerValue,
    ClarificationChoice,
    EvidenceHandle,
    PendingClarification,
    VerifiedAnswerPlan,
)
from aethersparse.agent.realization import GroundedAnswerRealizer, GroundingError


def _evidence() -> tuple[EvidenceHandle, ...]:
    return (
        EvidenceHandle(
            handle_id="e1",
            source_namespace="encyclopedia",
            canonical_object_id="alan",
            source_version="1",
            source_locator="doc:1#20-90",
            exact_text="Alan Turing was born in Maida Vale on 23 June 1912.",
            supported_values=("Maida Vale", "23 June 1912", "Alan Turing"),
        ),
        EvidenceHandle(
            handle_id="e2",
            source_namespace="manuals",
            canonical_object_id="capacity",
            source_version="1",
            source_locator="doc:2#0-40",
            exact_text="Capacity is 256 GB. Ada, Grace. A is 2; B is 3. exact words",
            supported_values=("256 GB", "Ada", "Grace", "2", "3", "exact words"),
        ),
    )


@pytest.mark.parametrize(
    ("kind", "values", "extra"),
    [
        (
            AnswerKind.FACTUAL_VALUE,
            (AnswerValue(text="Maida Vale", evidence_handle_ids=("e1",)),),
            {},
        ),
        (AnswerKind.ENTITY, (AnswerValue(text="Alan Turing", evidence_handle_ids=("e1",)),), {}),
        (AnswerKind.DATE, (AnswerValue(text="23 June 1912", evidence_handle_ids=("e1",)),), {}),
        (AnswerKind.QUANTITY, (AnswerValue(text="256 GB", evidence_handle_ids=("e2",)),), {}),
        (
            AnswerKind.LIST,
            (
                AnswerValue(text="Ada", evidence_handle_ids=("e2",)),
                AnswerValue(text="Grace", evidence_handle_ids=("e2",)),
            ),
            {},
        ),
        (
            AnswerKind.COMPARISON,
            (
                AnswerValue(text="2", evidence_handle_ids=("e2",)),
                AnswerValue(text="3", evidence_handle_ids=("e2",)),
            ),
            {"comparison_labels": ("A", "B")},
        ),
        (AnswerKind.QUOTATION, (AnswerValue(text="exact words", evidence_handle_ids=("e2",)),), {}),
    ],
)
def test_all_grounded_shapes(
    kind: AnswerKind, values: tuple[AnswerValue, ...], extra: dict[str, object]
) -> None:
    plan = VerifiedAnswerPlan(
        plan_id=f"plan-{kind}",
        kind=kind,
        subject="Subject",
        relation="is",
        values=values,
        verifier_status="ACCEPTED",
        **extra,
    )
    answer = GroundedAnswerRealizer().realize(plan, _evidence())
    assert answer.grounded is True
    assert all(value.text in answer.text for value in values)
    assert answer.evidence_handle_ids


def test_clarification_prompt_and_verifier_grounding_fail_closed() -> None:
    clarification = PendingClarification(
        question="Which one?",
        choices=(
            ClarificationChoice(choice_id="1", entity_id="a", label="Alpha"),
            ClarificationChoice(choice_id="2", entity_id="b", label="Beta"),
        ),
        original_query="Alpha?",
    )
    plan = VerifiedAnswerPlan(
        plan_id="clarify",
        kind=AnswerKind.CLARIFICATION,
        clarification=clarification,
        verifier_status="ACCEPTED",
    )
    assert "1: Alpha" in GroundedAnswerRealizer().realize(plan, ()).text

    unsupported = VerifiedAnswerPlan(
        plan_id="bad",
        kind=AnswerKind.FACTUAL_VALUE,
        values=(AnswerValue(text="invented", evidence_handle_ids=("e1",)),),
        verifier_status="ACCEPTED",
    )
    with pytest.raises(GroundingError):
        GroundedAnswerRealizer().realize(unsupported, _evidence())
    with pytest.raises(GroundingError):
        GroundedAnswerRealizer().realize(
            unsupported.model_copy(update={"verifier_status": "REJECTED"}), _evidence()
        )
