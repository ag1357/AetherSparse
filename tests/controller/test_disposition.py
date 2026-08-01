from __future__ import annotations

from aethersparse.controller.disposition import choose_disposition
from aethersparse.controller.framing import QueryFramer
from aethersparse.controller.models import (
    AnswerSelection,
    AnswerShape,
    ControllerDisposition,
    EvidenceGraph,
    VerificationReport,
)


def _graph(*, contradictions: tuple[tuple[str, str], ...] = ()) -> EvidenceGraph:
    return EvidenceGraph(
        query_id="q",
        entities=(),
        claims=(),
        source_spans=(),
        source_families=(),
        contradictions=contradictions,
        required_facets=(),
        missing_facets=(),
    )


def _selection() -> AnswerSelection:
    return AnswerSelection(
        answer_text="answer",
        answer_shape=AnswerShape.DEFINITION,
        selected_claim_ids=("claim",),
        selected_source_span_ids=("span",),
        confidence=1.0,
    )


def _verification(passed: bool) -> VerificationReport:
    return VerificationReport(passed=passed, findings=(), bound_surface_count=1)


def test_disposition_precedence_is_seven_way_and_fail_closed() -> None:
    frame = (
        QueryFramer()
        .frame("What is Ada Lovelace?")
        .model_copy(update={"entity_mentions": (), "uncertainty": 0.0})
    )
    assert (
        choose_disposition(frame, _graph(), _selection(), _verification(False))[0]
        is ControllerDisposition.VERIFICATION_FAILURE
    )
    assert (
        choose_disposition(frame, _graph(contradictions=(("a", "b"),)), None, None)[0]
        is ControllerDisposition.CONFLICTING_EVIDENCE
    )
    assert (
        choose_disposition(frame, _graph(), None, None, premise_status="REFUTED")[0]
        is ControllerDisposition.INCORRECT_PREMISE
    )
    assert choose_disposition(frame, _graph(), None, None)[0] is ControllerDisposition.ABSTAIN
    assert (
        choose_disposition(frame, _graph(), _selection(), _verification(True))[0]
        is ControllerDisposition.ANSWER
    )


def test_unknown_and_ambiguity_are_not_collapsed_together() -> None:
    unknown = QueryFramer().frame("What is Qzzyxx?")
    # Before a registry marks coverage, unanswered copyable text is an abstention.
    assert choose_disposition(unknown, _graph(), None, None)[0] is ControllerDisposition.ABSTAIN
    assert (
        choose_disposition(unknown, _graph(), _selection(), _verification(True))[0]
        is ControllerDisposition.ABSTAIN
    )
    assert (
        choose_disposition(unknown, _graph(), None, None, corpus_coverage=False)[0]
        is ControllerDisposition.ABSTAIN
    )
    explicit_external = QueryFramer().frame(
        "Find the official biography of OffCorpus-deadbeef, which is not in this corpus."
    )
    assert (
        choose_disposition(
            explicit_external,
            _graph(),
            None,
            None,
            corpus_coverage=False,
        )[0]
        is ControllerDisposition.OUT_OF_CORPUS
    )
    incomplete = QueryFramer().frame("What about")
    assert choose_disposition(incomplete, _graph(), None, None)[0] is ControllerDisposition.CLARIFY
