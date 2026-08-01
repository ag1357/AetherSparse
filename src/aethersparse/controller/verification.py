"""Fail-closed deterministic verification of every factual answer surface."""

from __future__ import annotations

import hashlib
import re

from aethersparse.controller.evidence import compare_quantities
from aethersparse.controller.models import (
    AnswerPlan,
    AnswerShape,
    EvidenceGraph,
    QueryFrame,
    RealizedAnswer,
    VerificationFinding,
    VerificationReport,
)

DATE_VALUE_RE = re.compile(r"\b(?:1[0-9]{3}|20[0-9]{2}|2100)(?:-[0-9]{2}-[0-9]{2})?\b")
NUMBER_RE = re.compile(r"[-+]?\d+(?:[,.]\d+)?")


def _hash_matches(text: str, expected: str) -> bool:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return expected in {digest, f"sha256:{digest}"}


def verify_realization(
    frame: QueryFrame,
    graph: EvidenceGraph,
    plan: AnswerPlan,
    answer: RealizedAnswer,
) -> VerificationReport:
    findings: list[VerificationFinding] = []

    def check(code: str, passed: bool, detail: str) -> None:
        findings.append(VerificationFinding(code=code, passed=passed, detail=detail))

    span_map = {span.span_id: span for span in graph.source_spans}
    claim_map = {claim.claim_id: claim for claim in graph.claims}
    plan_map = {claim.plan_claim_id: claim for claim in plan.planned_claims}

    if plan.answer_shape is AnswerShape.COMPARISON and len(plan.planned_claims) == 2:
        left, right = plan.planned_claims
        expected_text = f"{left.surface} {plan.comparison_operator} {right.surface}."
    elif plan.answer_shape is AnswerShape.LIST:
        expected_text = "; ".join(claim.surface for claim in plan.planned_claims)
    else:
        expected_text = plan.planned_claims[0].surface
    check(
        "REALIZATION_FIDELITY",
        answer.text == expected_text,
        "realizer may change neither factual surfaces nor approved deterministic glue",
    )

    check("HAS_BINDINGS", bool(answer.bindings), "answer must bind copied factual surfaces")
    for span in graph.source_spans:
        check(
            f"SOURCE_HASH:{span.span_id}",
            _hash_matches(span.text, span.text_hash),
            "immutable source text hash must match",
        )
    covered_claims: set[str] = set()
    for binding in answer.bindings:
        surface_ok = answer.text[binding.start : binding.end] == binding.surface
        check(f"SURFACE_OFFSET:{binding.plan_claim_id}", surface_ok, "surface offsets are exact")
        planned = plan_map.get(binding.plan_claim_id)
        check(
            f"PLAN_BINDING:{binding.plan_claim_id}",
            planned is not None
            and planned.surface == binding.surface
            and planned.structured_claim_ids == binding.structured_claim_ids,
            "surface must be authorized by the answer plan",
        )
        for claim_id in binding.structured_claim_ids:
            claim = claim_map.get(claim_id)
            covered_claims.add(claim_id)
            valid_claim = claim is not None and set(binding.source_span_ids).issubset(
                set(claim.source_span_ids)
            )
            check(f"CLAIM_SOURCE:{claim_id}", valid_claim, "claim must bind exact named spans")
            spans_present = all(span_id in span_map for span_id in binding.source_span_ids)
            check(f"SPAN_PRESENT:{claim_id}", spans_present, "all bound spans exist in graph")
            copied_from_source = any(
                binding.surface in span_map[span_id].text
                for span_id in binding.source_span_ids
                if span_id in span_map
            )
            check(
                f"SOURCE_CONTAINS_SURFACE:{claim_id}",
                copied_from_source,
                "every factual surface must be copied from an exact bound source span",
            )
            if claim is None:
                continue
            entity_ok = (
                not frame.candidate_entity_ids
                or claim.subject_entity_id in frame.candidate_entity_ids
                or claim.object_entity_id in frame.candidate_entity_ids
            )
            check(
                f"ENTITY_DIRECTION:{claim_id}", entity_ok, "claim direction matches linked entity"
            )
            relation_ok = (
                not frame.requested_relation_families
                or claim.relation_family in frame.requested_relation_families
            )
            check(f"RELATION_DIRECTION:{claim_id}", relation_ok, "claim relation matches frame")
            if frame.answer_shape is AnswerShape.DATE:
                check(
                    f"DATE:{claim_id}",
                    bool(DATE_VALUE_RE.search(binding.surface))
                    and bool(DATE_VALUE_RE.search(claim.occurred_at or claim.object_value)),
                    "date must be copied from a date claim",
                )
            if frame.answer_shape is AnswerShape.QUANTITY:
                check(
                    f"QUANTITY:{claim_id}",
                    bool(NUMBER_RE.search(binding.surface))
                    and bool(NUMBER_RE.search(claim.quantity_value or claim.object_value)),
                    "quantity and unit must be claim-backed",
                )
                if claim.quantity_unit:
                    check(
                        f"UNIT:{claim_id}",
                        claim.quantity_unit.casefold() in binding.surface.casefold(),
                        "quantity unit must be retained",
                    )
            if frame.answer_shape is AnswerShape.QUOTATION:
                check(
                    f"ATTRIBUTION:{claim_id}",
                    bool(claim.speaker_entity_id and claim.quotation)
                    and binding.surface == claim.quotation,
                    "quotation must preserve exact text and speaker attribution",
                )
            if frame.answer_shape is AnswerShape.VERIFICATION:
                check(
                    f"NEGATION:{claim_id}",
                    (claim.polarity == "negative")
                    == bool(re.search(r"\b(?:not|no|never|false)\b", claim.object_value, re.I)),
                    "claim polarity and copied negation must agree",
                )

    expected_claims = {
        claim_id for planned in plan.planned_claims for claim_id in planned.structured_claim_ids
    }
    check(
        "PLAN_COVERAGE",
        expected_claims == covered_claims,
        "every planned claim is surfaced exactly once or in a bound composition",
    )
    if len(plan.planned_claims) > 1:
        families = [
            {
                span_map[span_id].source_family
                for span_id in claim.source_span_ids
                if span_id in span_map
            }
            for claim_id in expected_claims
            if (claim := claim_map.get(claim_id)) is not None
        ]
        check(
            "SOURCE_LINEAGE_DIVERSITY",
            len({tuple(sorted(item)) for item in families}) == len(families),
            "multi-claim composition may not count one duplicated source family twice",
        )
    if plan.answer_shape is AnswerShape.COMPARISON and len(expected_claims) == 2:
        comparison_claims = [
            claim_map[planned.structured_claim_ids[0]] for planned in plan.planned_claims
        ]
        comparison = compare_quantities(comparison_claims[0], comparison_claims[1])
        expected_operator = (
            None
            if comparison is None
            else "="
            if comparison == 0
            else ">"
            if comparison > 0
            else "<"
        )
        check(
            "COMPARISON_DIRECTION",
            plan.comparison_operator == expected_operator,
            "comparison direction must be recomputed from exact compatible quantities",
        )
    check(
        "NO_GRAPH_CONTRADICTION", not graph.contradictions, "selected graph is contradiction-free"
    )
    passed = bool(findings) and all(finding.passed for finding in findings)
    return VerificationReport(
        passed=passed,
        findings=tuple(findings),
        bound_surface_count=len(answer.bindings),
    )


def adversarial_mutations(answer: RealizedAnswer) -> tuple[RealizedAnswer, ...]:
    """Create deterministic mutations; none are silently repaired by verification."""

    mutations: list[RealizedAnswer] = []
    replacements = ((" not ", " "), (" is ", " is not "), ("<", ">"), (">", "<"))
    for old, new in replacements:
        if old in answer.text:
            mutations.append(answer.model_copy(update={"text": answer.text.replace(old, new, 1)}))
    for binding in answer.bindings:
        if binding.surface:
            changed = answer.text[: binding.start] + "X" + answer.text[binding.start + 1 :]
            mutations.append(answer.model_copy(update={"text": changed}))
    return tuple(mutations)
