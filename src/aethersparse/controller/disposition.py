"""Separately calibrated seven-way disposition controller."""

from __future__ import annotations

from pydantic import Field

from aethersparse.controller.models import (
    AnswerSelection,
    ControllerDisposition,
    EvidenceGraph,
    FrozenModel,
    QueryFrame,
    VerificationReport,
)


class DispositionCalibration(FrozenModel):
    """Independent thresholds; evaluation may tune these without changing retrieval."""

    entity_confidence: float = Field(default=0.82, ge=0.0, le=1.0)
    answer_confidence: float = Field(default=0.72, ge=0.0, le=1.0)
    clarification_uncertainty: float = Field(default=0.75, ge=0.0, le=1.0)


def choose_disposition(
    frame: QueryFrame,
    graph: EvidenceGraph,
    selection: AnswerSelection | None,
    verification: VerificationReport | None,
    *,
    corpus_coverage: bool = True,
    premise_status: str = "UNKNOWN",
    calibration: DispositionCalibration | None = None,
) -> tuple[ControllerDisposition, str]:
    """Choose exactly one outcome using non-overlapping precedence."""

    thresholds = calibration or DispositionCalibration()
    if verification is not None and not verification.passed:
        return (ControllerDisposition.VERIFICATION_FAILURE, "deterministic verification failed")
    if graph.contradictions:
        return (ControllerDisposition.CONFLICTING_EVIDENCE, "independent evidence conflicts")
    if premise_status == "REFUTED":
        return (ControllerDisposition.INCORRECT_PREMISE, "the exact evidence refutes the premise")
    unknown = [
        mention
        for mention in frame.entity_mentions
        if mention.copy_status == "unknown_but_copyable"
    ]
    explicit_external_request = any(
        cue in frame.normalized_query.casefold()
        for cue in (
            "not in this corpus",
            "outside this corpus",
            "out of corpus",
            "official biography of",
        )
    )
    if unknown and not corpus_coverage and explicit_external_request:
        return (
            ControllerDisposition.OUT_OF_CORPUS,
            "named entity is absent from the frozen corpus",
        )
    if unknown:
        return (ControllerDisposition.ABSTAIN, "named entity is unresolved but copyable")
    ambiguous = [
        mention
        for mention in frame.entity_mentions
        if mention.copy_status == "ambiguous"
        or (
            mention.copy_status == "linked"
            and mention.selected_confidence < thresholds.entity_confidence
        )
    ]
    if (
        frame.clarification_need
        or ambiguous
        or frame.uncertainty >= thresholds.clarification_uncertainty
    ):
        return (ControllerDisposition.CLARIFY, "the entity or discourse reference is ambiguous")
    if selection is None:
        return (
            ControllerDisposition.ABSTAIN,
            "selected evidence does not answer every required facet",
        )
    if selection.confidence < thresholds.answer_confidence:
        return (
            ControllerDisposition.ABSTAIN,
            "answer confidence is below its calibrated threshold",
        )
    if verification is None:
        return (ControllerDisposition.VERIFICATION_FAILURE, "answer was not verified")
    return (ControllerDisposition.ANSWER, "exact plan and source bindings verified")
