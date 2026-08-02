"""Frozen independent natural-query benchmark and full ablation metrics.

The tooling enforces role separation and provenance.  It intentionally does not
author, answer, and grade questions in one invocation.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from enum import StrEnum
from statistics import median

from pydantic import Field, model_validator

from aethersparse.controller.models import (
    AnswerShape,
    ControllerDisposition,
    FrozenModel,
    RequiredFacet,
)

BENCHMARK_IDENTITY = "INDEPENDENT_NATURAL_QUERY_SET_V050_R1"
REQUIRED_CATEGORIES = frozenset(
    {
        "direct_fact",
        "alias",
        "redirect",
        "misspelling",
        "quotation",
        "date",
        "quantity",
        "incorrect_premise",
        "comparison",
        "two_source",
        "three_to_six_source",
        "ambiguous_entity",
        "unknown_entity",
        "out_of_corpus",
        "pronoun",
        "follow_up",
        "incomplete",
        "clarification",
        "abstention",
    }
)


class Partition(StrEnum):
    TUNING = "tuning"
    DEVELOPMENT = "development"
    EVALUATION = "evaluation"
    FINAL_HELD = "final_held"


class AblationSystem(StrEnum):
    FLAT_LEXICAL_EXTRACTIVE = "flat_lexical_extractive"
    DETERMINISTIC_FEATURE_FUSION = "deterministic_feature_fusion"
    FUSION_CONTEXTUAL_LINKER = "fusion_contextual_entity_linker"
    FUSION_QUERY_FRAME = "fusion_query_frame_and_facets"
    FUSION_EXACT_GRAPH = "fusion_exact_evidence_graph"
    FULL_EXTRACTIVE_CONTROLLER = "full_extractive_cognitive_controller"
    FULL_CONSTRAINED_REALIZER = "full_system_constrained_realizer"
    VERIFIED_RAG = "small_verified_rag_comparator"


class RoleIdentity(FrozenModel):
    identity: str
    role: str
    process_identity: str
    runtime_access: bool = False


class GoldEvidence(FrozenModel):
    span_id: str
    document_id: str
    document_hash: str
    source_revision: str
    source_url: str
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    exact_text: str
    exact_text_hash: str

    @model_validator(mode="after")
    def valid_bounds(self) -> GoldEvidence:
        if self.char_end <= self.char_start:
            raise ValueError("gold evidence span has invalid bounds")
        return self


class AuditSourceDocument(FrozenModel):
    document_id: str
    source_revision: str
    source_url: str
    text: str
    document_hash: str


class NaturalQueryCase(FrozenModel):
    case_id: str
    partition: Partition
    question: str
    categories: tuple[str, ...] = Field(min_length=1)
    author_identity: str
    adjudicator_identity: str
    accepted_disposition: ControllerDisposition
    accepted_answers: tuple[str, ...] = ()
    required_entity_ids: tuple[str, ...] = ()
    required_answer_shape: AnswerShape
    required_facets: tuple[RequiredFacet, ...]
    gold_claim_ids: tuple[str, ...] = ()
    gold_evidence: tuple[GoldEvidence, ...] = ()
    prior_case_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def answer_contract(self) -> NaturalQueryCase:
        if self.accepted_disposition is ControllerDisposition.ANSWER and (
            not self.accepted_answers or not self.gold_evidence
        ):
            raise ValueError("ANSWER case requires accepted text and exact gold evidence")
        return self


class FrozenBenchmark(FrozenModel):
    benchmark_identity: str = BENCHMARK_IDENTITY
    schema_version: str = "1.0"
    author_roles: tuple[RoleIdentity, ...]
    adjudicator_role: RoleIdentity
    evaluator_role: RoleIdentity
    auditor_role: RoleIdentity
    cases: tuple[NaturalQueryCase, ...]
    content_sha256: str


class ProvenanceAudit(FrozenModel):
    benchmark_identity: str
    passed: bool
    case_count: int
    evidence_span_count: int
    checked_categories: tuple[str, ...]
    missing_categories: tuple[str, ...]
    tuning_evaluation_article_overlap: tuple[str, ...]
    duplicate_question_groups: tuple[tuple[str, ...], ...]
    role_violations: tuple[str, ...]
    evidence_violations: tuple[str, ...]


class EvaluationOutcome(FrozenModel):
    case_id: str
    system: AblationSystem
    disposition: ControllerDisposition
    answer_text: str | None = None
    retrieved_document_ids: tuple[str, ...] = ()
    retrieved_span_ids: tuple[str, ...] = ()
    linked_entity_ids: tuple[str, ...] = ()
    unknown_input_spans: tuple[str, ...] = ()
    copied_unknown_spans: tuple[str, ...] = ()
    answer_shape: AnswerShape = AnswerShape.UNKNOWN
    predicted_facets: tuple[RequiredFacet, ...] = ()
    factual_surface_count: int = Field(default=0, ge=0)
    unsupported_surface_count: int = Field(default=0, ge=0)
    bytes_read: int = Field(default=0, ge=0)
    blocks_read: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0.0)
    peak_ram_bytes: int = Field(default=0, ge=0)
    model_bytes: int = Field(default=0, ge=0)
    macs: int = Field(default=0, ge=0)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify_gold_evidence(
    evidence: GoldEvidence,
    source: AuditSourceDocument | str,
) -> bool:
    source_text = source.text if isinstance(source, AuditSourceDocument) else source
    if _sha256_text(source_text) not in {
        evidence.document_hash,
        evidence.document_hash.removeprefix("sha256:"),
    }:
        return False
    if isinstance(source, AuditSourceDocument) and (
        source.document_id != evidence.document_id
        or source.source_revision != evidence.source_revision
        or source.source_url != evidence.source_url
        or _sha256_text(source.text)
        not in {source.document_hash, source.document_hash.removeprefix("sha256:")}
    ):
        return False
    copied = source_text[evidence.char_start : evidence.char_end]
    return copied == evidence.exact_text and _sha256_text(copied) in {
        evidence.exact_text_hash,
        evidence.exact_text_hash.removeprefix("sha256:"),
    }


def _canonical_case_payload(cases: tuple[NaturalQueryCase, ...]) -> str:
    return json.dumps(
        [case.model_dump(mode="json") for case in sorted(cases, key=lambda item: item.case_id)],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def freeze_benchmark(
    cases: tuple[NaturalQueryCase, ...],
    *,
    author_roles: tuple[RoleIdentity, ...],
    adjudicator_role: RoleIdentity,
    evaluator_role: RoleIdentity,
    auditor_role: RoleIdentity,
    require_full: bool = True,
) -> FrozenBenchmark:
    if require_full and len(cases) < 2_000:
        raise ValueError("the frozen v0.5 natural set requires at least 2,000 questions")
    if require_full and len(author_roles) < 3:
        raise ValueError("full qualification requires at least three isolated authoring processes")
    if require_full and len({case.author_identity for case in cases}) < 3:
        raise ValueError("full qualification cases must use at least three authoring processes")
    role_ids = [
        *(role.identity for role in author_roles),
        adjudicator_role.identity,
        evaluator_role.identity,
        auditor_role.identity,
    ]
    if len(role_ids) != len(set(role_ids)):
        raise ValueError("author/adjudicator/evaluator/auditor identities must be distinct")
    process_ids = [
        *(role.process_identity for role in author_roles),
        adjudicator_role.process_identity,
        evaluator_role.process_identity,
        auditor_role.process_identity,
    ]
    if len(process_ids) != len(set(process_ids)):
        raise ValueError("author/adjudicator/evaluator/auditor processes must be isolated")
    if any(role.runtime_access for role in author_roles):
        raise ValueError("question authors may not access runtime outputs")
    known_authors = {role.identity for role in author_roles}
    if any(case.author_identity not in known_authors for case in cases):
        raise ValueError("case names an unregistered author process")
    if any(case.adjudicator_identity != adjudicator_role.identity for case in cases):
        raise ValueError("case was not frozen by the registered evidence adjudicator")
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("case IDs must be unique")
    payload = _canonical_case_payload(cases)
    return FrozenBenchmark(
        author_roles=author_roles,
        adjudicator_role=adjudicator_role,
        evaluator_role=evaluator_role,
        auditor_role=auditor_role,
        cases=cases,
        content_sha256=_sha256_text(payload),
    )


def audit_benchmark(
    benchmark: FrozenBenchmark,
    source_documents: dict[str, AuditSourceDocument | str],
    *,
    require_full: bool = True,
) -> ProvenanceAudit:
    role_violations: list[str] = []
    all_roles = (
        *benchmark.author_roles,
        benchmark.adjudicator_role,
        benchmark.evaluator_role,
        benchmark.auditor_role,
    )
    identities = [role.identity for role in all_roles]
    if len(identities) != len(set(identities)):
        role_violations.append("role_identity_collision")
    process_ids = [role.process_identity for role in all_roles]
    if len(process_ids) != len(set(process_ids)):
        role_violations.append("role_process_collision")
    if any(role.runtime_access for role in benchmark.author_roles):
        role_violations.append("author_runtime_access")
    if require_full and len({case.author_identity for case in benchmark.cases}) < 3:
        role_violations.append("insufficient_used_author_processes")

    evidence_violations: list[str] = []
    evidence_count = 0
    for case in benchmark.cases:
        for evidence in case.gold_evidence:
            evidence_count += 1
            source = source_documents.get(evidence.document_id)
            if source is None:
                evidence_violations.append(f"{case.case_id}:{evidence.span_id}:missing_document")
            elif require_full and isinstance(source, str):
                evidence_violations.append(
                    f"{case.case_id}:{evidence.span_id}:missing_source_metadata"
                )
            elif not verify_gold_evidence(evidence, source):
                evidence_violations.append(f"{case.case_id}:{evidence.span_id}:binding_mismatch")

    article_sets: dict[Partition, set[str]] = defaultdict(set)
    for case in benchmark.cases:
        article_sets[case.partition].update(item.document_id for item in case.gold_evidence)
    tuning = article_sets[Partition.TUNING] | article_sets[Partition.DEVELOPMENT]
    evaluation = article_sets[Partition.EVALUATION] | article_sets[Partition.FINAL_HELD]
    overlap = tuple(sorted(tuning & evaluation))

    question_groups: dict[str, list[str]] = defaultdict(list)
    for case in benchmark.cases:
        normalized = " ".join(case.question.casefold().split())
        question_groups[normalized].append(case.case_id)
    duplicates = tuple(tuple(ids) for ids in question_groups.values() if len(ids) > 1)
    categories = {category for case in benchmark.cases for category in case.categories}
    missing = tuple(sorted(REQUIRED_CATEGORIES - categories)) if require_full else ()
    count_ok = len(benchmark.cases) >= 2_000 if require_full else bool(benchmark.cases)
    hash_ok = benchmark.content_sha256 == _sha256_text(_canonical_case_payload(benchmark.cases))
    return ProvenanceAudit(
        benchmark_identity=benchmark.benchmark_identity,
        passed=count_ok
        and hash_ok
        and not role_violations
        and not evidence_violations
        and not overlap
        and not duplicates
        and not missing,
        case_count=len(benchmark.cases),
        evidence_span_count=evidence_count,
        checked_categories=tuple(sorted(categories)),
        missing_categories=missing,
        tuning_evaluation_article_overlap=overlap,
        duplicate_question_groups=duplicates,
        role_violations=tuple(role_violations),
        evidence_violations=tuple(evidence_violations),
    )


def _safe_ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _percentile95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]


def evaluate_ablation(
    benchmark: FrozenBenchmark,
    outcomes: tuple[EvaluationOutcome, ...],
    *,
    require_complete: bool = True,
) -> dict[str, object]:
    """Return a complete, category-addressable metric matrix for every system."""

    cases = {case.case_id: case for case in benchmark.cases}
    grouped: dict[AblationSystem, list[EvaluationOutcome]] = defaultdict(list)
    for outcome in outcomes:
        if outcome.case_id not in cases:
            raise ValueError(f"outcome references unknown case {outcome.case_id}")
        grouped[outcome.system].append(outcome)
    if require_complete:
        expected_ids = set(cases)
        for system in AblationSystem:
            observed_ids = {row.case_id for row in grouped.get(system, [])}
            if observed_ids != expected_ids:
                missing = sorted(expected_ids - observed_ids)
                extra = sorted(observed_ids - expected_ids)
                raise ValueError(
                    f"incomplete ablation {system.value}: missing={missing[:5]} extra={extra[:5]}"
                )
    reports: dict[str, object] = {}
    for system in AblationSystem:
        rows = grouped.get(system, [])
        row_ids = [row.case_id for row in rows]
        if len(row_ids) != len(set(row_ids)):
            raise ValueError(f"duplicate outcomes for {system.value}")
        answer_cases = [
            cases[row.case_id]
            for row in rows
            if cases[row.case_id].accepted_disposition is ControllerDisposition.ANSWER
        ]
        article_hits = article_hits_strict = 0
        evidence_hits = entity_hits = shape_hits = facet_hits = 0
        entity_case_count = 0
        exact_answers = supported_answers = silent_wrong = 0
        unknown_span_count = copied_unknown_count = 0
        clarification_tp = clarification_fp = clarification_fn = 0
        abstention_tp = abstention_fp = abstention_fn = 0
        category_correct: Counter[str] = Counter()
        category_total: Counter[str] = Counter()
        for row in rows:
            case = cases[row.case_id]
            gold_docs = {item.document_id for item in case.gold_evidence}
            gold_spans = {item.span_id for item in case.gold_evidence}
            if case.accepted_disposition is ControllerDisposition.ANSWER:
                article_hits += int(bool(gold_docs & set(row.retrieved_document_ids)))
                article_hits_strict += int(
                    bool(gold_docs) and gold_docs.issubset(row.retrieved_document_ids)
                )
                evidence_hits += int(bool(gold_spans & set(row.retrieved_span_ids)))
            entity_ok = set(case.required_entity_ids).issubset(row.linked_entity_ids)
            if case.required_entity_ids:
                entity_case_count += 1
                entity_hits += int(entity_ok)
            shape_hits += int(row.answer_shape is case.required_answer_shape)
            facet_hits += int(set(case.required_facets).issubset(row.predicted_facets))
            normalized_answer = " ".join((row.answer_text or "").casefold().split())
            exact = row.disposition is case.accepted_disposition
            if case.accepted_disposition is ControllerDisposition.ANSWER:
                exact = (
                    exact
                    and any(
                        " ".join(value.casefold().split()) == normalized_answer
                        for value in case.accepted_answers
                    )
                    and row.unsupported_surface_count == 0
                )
                exact_answers += int(exact)
                supported_answers += int(
                    row.unsupported_surface_count == 0 and row.answer_text is not None
                )
                silent_wrong += int(
                    row.disposition is ControllerDisposition.ANSWER and not entity_ok
                )
            unknown_span_count += len(row.unknown_input_spans)
            copied_unknown_count += sum(
                1 for value in row.unknown_input_spans if value in row.copied_unknown_spans
            )
            if case.accepted_disposition is ControllerDisposition.CLARIFY:
                clarification_tp += int(row.disposition is ControllerDisposition.CLARIFY)
                clarification_fn += int(row.disposition is not ControllerDisposition.CLARIFY)
            elif row.disposition is ControllerDisposition.CLARIFY:
                clarification_fp += 1
            accepted_abstention = case.accepted_disposition in {
                ControllerDisposition.ABSTAIN,
                ControllerDisposition.OUT_OF_CORPUS,
            }
            if accepted_abstention:
                abstention_tp += int(
                    row.disposition
                    in {ControllerDisposition.ABSTAIN, ControllerDisposition.OUT_OF_CORPUS}
                )
                abstention_fn += int(
                    row.disposition
                    not in {ControllerDisposition.ABSTAIN, ControllerDisposition.OUT_OF_CORPUS}
                )
            elif row.disposition in {
                ControllerDisposition.ABSTAIN,
                ControllerDisposition.OUT_OF_CORPUS,
            }:
                abstention_fp += 1
            for category in case.categories:
                category_total[category] += 1
                category_correct[category] += int(exact)
        factual_surfaces = sum(row.factual_surface_count for row in rows)
        unsupported = sum(row.unsupported_surface_count for row in rows)
        emitted_answers = sum(row.disposition is ControllerDisposition.ANSWER for row in rows)
        category_accuracy = {
            category: _safe_ratio(category_correct[category], total)
            for category, total in sorted(category_total.items())
        }
        multi_source_total = sum(
            category_total[category] for category in ("two_source", "three_to_six_source")
        )
        multi_source_correct = sum(
            category_correct[category] for category in ("two_source", "three_to_six_source")
        )
        follow_up_total = sum(category_total[category] for category in ("follow_up", "pronoun"))
        follow_up_correct = sum(category_correct[category] for category in ("follow_up", "pronoun"))
        reports[system.value] = {
            "case_count": len(rows),
            "article_recall": _safe_ratio(article_hits, len(answer_cases)),
            "article_recall_strict": _safe_ratio(article_hits_strict, len(answer_cases)),
            "evidence_recall": _safe_ratio(evidence_hits, len(answer_cases)),
            "article_recall_at_8": _safe_ratio(article_hits, len(answer_cases)),
            "evidence_recall_at_8": _safe_ratio(evidence_hits, len(answer_cases)),
            "entity_accuracy": _safe_ratio(entity_hits, entity_case_count),
            "answer_shape_accuracy": _safe_ratio(shape_hits, len(rows)),
            "required_facet_accuracy": _safe_ratio(facet_hits, len(rows)),
            "exact_supported_answer_accuracy": _safe_ratio(exact_answers, len(answer_cases)),
            "supported_answer_rate": _safe_ratio(supported_answers, len(answer_cases)),
            "unsupported_claim_rate": _safe_ratio(unsupported, factual_surfaces),
            "silent_wrong_entity_rate": _safe_ratio(silent_wrong, emitted_answers),
            "unknown_copy_fidelity": _safe_ratio(
                copied_unknown_count, unknown_span_count
            ),
            "comparison_accuracy": category_accuracy.get("comparison", 0.0),
            "multi_source_accuracy": _safe_ratio(multi_source_correct, multi_source_total),
            "follow_up_coreference_accuracy": _safe_ratio(follow_up_correct, follow_up_total),
            "clarification_precision": _safe_ratio(
                clarification_tp, clarification_tp + clarification_fp
            ),
            "clarification_recall": _safe_ratio(
                clarification_tp, clarification_tp + clarification_fn
            ),
            "abstention_precision": _safe_ratio(abstention_tp, abstention_tp + abstention_fp),
            "abstention_recall": _safe_ratio(abstention_tp, abstention_tp + abstention_fn),
            "category_accuracy": category_accuracy,
            "mean_bytes_read": _safe_ratio(sum(row.bytes_read for row in rows), len(rows)),
            "mean_blocks_read": _safe_ratio(sum(row.blocks_read for row in rows), len(rows)),
            "p50_latency_ms": median([row.latency_ms for row in rows]) if rows else 0.0,
            "p95_latency_ms": _percentile95([row.latency_ms for row in rows]),
            "peak_ram_bytes": max((row.peak_ram_bytes for row in rows), default=0),
            "model_bytes": max((row.model_bytes for row in rows), default=0),
            "mean_macs": _safe_ratio(sum(row.macs for row in rows), len(rows)),
        }
    return {
        "benchmark_identity": benchmark.benchmark_identity,
        "benchmark_sha256": benchmark.content_sha256,
        "systems": reports,
    }
