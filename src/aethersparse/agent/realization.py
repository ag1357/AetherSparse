"""Deterministic evidence-bound answer realization."""

from __future__ import annotations

from collections.abc import Sequence

from aethersparse.agent.contracts import (
    AnswerKind,
    EvidenceHandle,
    GroundedAnswer,
    VerifiedAnswerPlan,
)


class GroundingError(ValueError):
    """Raised rather than emitting an unsupported answer."""


class GroundedAnswerRealizer:
    """Small grammar/copy realizer; it cannot invent a value or citation."""

    @staticmethod
    def _supports(handle: EvidenceHandle, value: str) -> bool:
        return value in handle.supported_values or value in handle.exact_text

    def realize(
        self, plan: VerifiedAnswerPlan, evidence: Sequence[EvidenceHandle]
    ) -> GroundedAnswer:
        if plan.verifier_status != "ACCEPTED":
            raise GroundingError("the exact verifier did not accept this plan")
        if plan.kind is AnswerKind.CLARIFICATION:
            assert plan.clarification is not None
            labels = ", ".join(
                f"{choice.choice_id}: {choice.label}" for choice in plan.clarification.choices
            )
            return GroundedAnswer(
                text=f"{plan.clarification.question} {labels}",
                plan_id=plan.plan_id,
                evidence_handle_ids=(),
            )

        by_id = {item.handle_id: item for item in evidence}
        used: list[str] = []
        for value in plan.values:
            handles = [by_id.get(handle_id) for handle_id in value.evidence_handle_ids]
            if any(handle is None for handle in handles):
                raise GroundingError(f"unknown evidence handle for {value.text!r}")
            if not any(
                self._supports(handle, value.text) for handle in handles if handle is not None
            ):
                raise GroundingError(f"value is not an exact copy from evidence: {value.text!r}")
            used.extend(value.evidence_handle_ids)

        values = [item.text for item in plan.values]
        subject = plan.subject or "The result"
        relation = plan.relation or "is"
        scalar_kinds = {
            AnswerKind.FACTUAL_VALUE,
            AnswerKind.ENTITY,
            AnswerKind.DATE,
            AnswerKind.QUANTITY,
        }
        if plan.kind in scalar_kinds:
            text = f"{subject} {relation} {values[0]}."
        elif plan.kind is AnswerKind.LIST:
            text = f"{subject}: {', '.join(values)}."
        elif plan.kind is AnswerKind.COMPARISON:
            assert plan.comparison_labels is not None
            if len(values) != 2:
                raise GroundingError("comparison requires exactly two grounded values")
            left, right = plan.comparison_labels
            text = f"{left}: {values[0]}; {right}: {values[1]}."
        elif plan.kind is AnswerKind.QUOTATION:
            text = f'{subject}: "{values[0]}"'
        else:  # pragma: no cover - closed enum protects this boundary
            raise GroundingError(f"unsupported answer kind: {plan.kind}")
        return GroundedAnswer(
            text=text,
            plan_id=plan.plan_id,
            evidence_handle_ids=tuple(dict.fromkeys(used)),
        )


class RealizationSmoother:
    """Future 1M-5M smoother interface; V13 intentionally has no implementation."""

    def smooth(self, answer: GroundedAnswer) -> GroundedAnswer:
        raise NotImplementedError("a smoother requires measured usability evidence")
