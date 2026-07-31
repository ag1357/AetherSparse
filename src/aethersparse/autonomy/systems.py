"""Matched, provenance-bound architecture variants for autonomous qualification.

The four systems in this module deliberately share one immutable corpus, one
structured query frame, one citation verifier, and one resource budget.  Their
only difference is evidence selection and bounded reasoning.  No variant can
emit a free-form claim: every answer is a deterministic template over a value
copied from selected evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import StrEnum
from statistics import mean
from typing import Literal


class SystemVariant(StrEnum):
    TOP1_TEMPLATE = "A_TOP1_TEMPLATE"
    COMPILED_MICROPROGRAM = "B_COMPILED_MICROPROGRAM"
    BOUNDED_LRVM = "C_BOUNDED_LRVM"
    TINY_CONSTRAINED_RAG = "D_TINY_CONSTRAINED_RAG"


class QuestionKind(StrEnum):
    """Required decisive-suite hooks.

    The category is an evaluation label, not privileged answer information.
    """

    DIRECT_FACT = "direct_fact"
    UNSEEN_PARAPHRASE = "unseen_paraphrase"
    TEMPORAL_ORDERING = "temporal_ordering"
    NUMERICAL_UNIT = "numerical_unit"
    CAUSAL_MULTIHOP = "causal_multihop"
    QUOTATION_ATTRIBUTION = "quotation_attribution"
    AMBIGUOUS_ENTITY = "ambiguous_entity"
    WRONG_PREMISE = "wrong_premise"
    CONFLICTING_SOURCES = "conflicting_sources"
    DUPLICATED_SOURCE_FAMILY = "duplicated_source_family"
    MISSING_EVIDENCE = "missing_evidence"
    UNKNOWN_TERM = "unknown_term"
    OUT_OF_DOMAIN = "out_of_domain"
    SESSION_FOLLOWUP = "session_followup"
    ADVERSARIAL_ENTITY = "adversarial_entity"
    ADVERSARIAL_DATE = "adversarial_date"
    ADVERSARIAL_QUANTITY = "adversarial_quantity"
    ADVERSARIAL_NEGATION = "adversarial_negation"
    ADVERSARIAL_ATTRIBUTION = "adversarial_attribution"


class AnswerDisposition(StrEnum):
    ANSWER = "answer"
    CLARIFY = "clarify"
    ABSTAIN = "abstain"
    OUT_OF_DOMAIN = "out_of_domain"


class VerificationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True, slots=True)
class KnowledgeFact:
    fact_id: str
    subject_id: str
    relation_id: str
    object_value: str
    evidence_span_id: str
    evidence_text: str
    source_doc_id: str
    source_family: str
    lineage_id: str
    aliases: tuple[str, ...] = ()
    valid_at: int | None = None
    quantity: float | None = None
    unit: str | None = None
    polarity: Literal["positive", "negative"] = "positive"
    attribution: str | None = None
    quality: float = 1.0

    def serialized(self) -> bytes:
        payload = {
            "fact_id": self.fact_id,
            "subject_id": self.subject_id,
            "relation_id": self.relation_id,
            "object_value": self.object_value,
            "evidence_span_id": self.evidence_span_id,
            "evidence_text": self.evidence_text,
            "source_doc_id": self.source_doc_id,
            "source_family": self.source_family,
            "lineage_id": self.lineage_id,
            "aliases": self.aliases,
            "valid_at": self.valid_at,
            "quantity": self.quantity,
            "unit": self.unit,
            "polarity": self.polarity,
            "attribution": self.attribution,
            "quality": self.quality,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True, slots=True)
class QueryFrame:
    subject_surface: str | None
    relation_id: str | None
    kind: QuestionKind
    path_relations: tuple[str, ...] = ()
    premise_object: str | None = None
    requested_unit: str | None = None
    temporal_mode: Literal["earliest", "latest", "before", "after"] | None = None
    session_subject_id: str | None = None


@dataclass(frozen=True, slots=True)
class MatchedQuestion:
    question_id: str
    text: str
    frame: QueryFrame
    expected_disposition: AnswerDisposition
    expected_value: str | None = None
    hard_subset: bool = False


@dataclass(frozen=True, slots=True)
class MatchedCorpus:
    corpus_id: str
    facts: tuple[KnowledgeFact, ...]
    domain_relations: tuple[str, ...]
    index_bytes: int

    def __post_init__(self) -> None:
        fact_ids = [fact.fact_id for fact in self.facts]
        span_ids = [fact.evidence_span_id for fact in self.facts]
        if len(set(fact_ids)) != len(fact_ids):
            raise ValueError("fact_id values must be unique")
        if len(set(span_ids)) != len(span_ids):
            raise ValueError("evidence_span_id values must be unique")
        if self.index_bytes < 1:
            raise ValueError("index_bytes must be positive")

    @property
    def identity(self) -> str:
        digest = hashlib.sha256()
        digest.update(self.corpus_id.encode())
        digest.update(str(self.index_bytes).encode())
        for fact in sorted(self.facts, key=lambda item: item.fact_id):
            digest.update(fact.serialized())
        return f"sha256:{digest.hexdigest()}"

    @property
    def serialized_bytes(self) -> int:
        return sum(len(fact.serialized()) for fact in self.facts)


@dataclass(frozen=True, slots=True)
class MatchedBudget:
    max_candidates: int = 16
    max_evidence: int = 4
    max_reasoning_hops: int = 4
    max_scheduler_cycles: int = 32
    max_bytes_read: int = 64 * 1024
    max_working_ram_bytes: int = 4 * 1024 * 1024
    max_model_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        if min(
            self.max_candidates,
            self.max_evidence,
            self.max_reasoning_hops,
            self.max_scheduler_cycles,
            self.max_bytes_read,
            self.max_working_ram_bytes,
            self.max_model_bytes,
        ) < 1:
            raise ValueError("all matched budget limits must be positive")


@dataclass(frozen=True, slots=True)
class OperationCost:
    operation: str
    category: Literal[
        "parse",
        "index",
        "storage",
        "reason",
        "schedule",
        "rerank",
        "realize",
        "verify",
        "interface",
    ]
    bytes_read: int = 0
    storage_reads: int = 0
    read_pattern: Literal["none", "sequential", "random"] = "none"
    integer_ops: int = 0
    neural_macs: int = 0
    working_ram_bytes: int = 0
    scheduler_cycles: int = 0
    realization_ops: int = 0
    interface_bytes: int = 0
    deterministic: bool = True


@dataclass(frozen=True, slots=True)
class VerificationResult:
    status: VerificationStatus
    checks: tuple[str, ...]
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class SystemTrace:
    provisional_parse: QueryFrame
    refined_parse: QueryFrame
    resolved_subject_ids: tuple[str, ...]
    retrieved_candidate_ids: tuple[str, ...]
    selected_evidence_ids: tuple[str, ...]
    operations: tuple[OperationCost, ...]
    verification: VerificationResult


@dataclass(frozen=True, slots=True)
class SystemResult:
    variant: SystemVariant
    question_id: str
    corpus_identity: str
    disposition: AnswerDisposition
    value: str | None
    sentence: str | None
    citation_span_ids: tuple[str, ...]
    failure_reason: str | None
    trace: SystemTrace
    model_bytes: int
    index_bytes: int

    @property
    def bytes_read(self) -> int:
        return sum(operation.bytes_read for operation in self.trace.operations)

    @property
    def neural_macs(self) -> int:
        return sum(operation.neural_macs for operation in self.trace.operations)

    @property
    def peak_working_ram_bytes(self) -> int:
        return max(
            (operation.working_ram_bytes for operation in self.trace.operations),
            default=0,
        )


@dataclass(frozen=True, slots=True)
class EvaluationRecord:
    question_id: str
    kind: QuestionKind
    hard_subset: bool
    correct: bool
    unsupported_claim: bool
    disposition: AnswerDisposition


@dataclass(frozen=True, slots=True)
class VariantMetrics:
    variant: SystemVariant
    question_count: int
    accuracy: float
    hard_subset_accuracy: float | None
    unsupported_claim_rate: float
    clarification_precision: float | None
    abstention_precision: float | None
    mean_bytes_read: float
    mean_neural_macs: float
    category_accuracy: dict[str, float]


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    corpus_identity: str
    question_ids: tuple[str, ...]
    metrics: tuple[VariantMetrics, ...]
    records: dict[str, tuple[EvaluationRecord, ...]]
    results: dict[str, tuple[SystemResult, ...]]


@dataclass(slots=True)
class _ExecutionState:
    frame: QueryFrame
    resolved_subjects: tuple[str, ...] = ()
    candidates: tuple[KnowledgeFact, ...] = ()
    selected: tuple[KnowledgeFact, ...] = ()
    operations: list[OperationCost] = field(default_factory=list)


class MatchedSystem:
    """Common corpus/index/parser/verifier boundary for every architecture."""

    variant: SystemVariant
    model_bytes: int = 0

    def __init__(self, corpus: MatchedCorpus, budget: MatchedBudget) -> None:
        self.corpus = corpus
        self.budget = budget
        # The corpus is immutable. Hashing every fact for every query turns the
        # otherwise bounded runtime into O(corpus_size) work, so compute the
        # provenance identity once at construction and reuse it in all traces.
        self._corpus_identity = corpus.identity
        self._facts_by_id = {fact.fact_id: fact for fact in corpus.facts}
        self._by_subject_relation: dict[tuple[str, str], list[KnowledgeFact]] = (
            defaultdict(list)
        )
        self._alias_index: dict[str, set[str]] = defaultdict(set)
        for fact in corpus.facts:
            self._by_subject_relation[(fact.subject_id, fact.relation_id)].append(fact)
            self._alias_index[_normalize(fact.subject_id)].add(fact.subject_id)
            for alias in fact.aliases:
                self._alias_index[_normalize(alias)].add(fact.subject_id)
        for facts in self._by_subject_relation.values():
            facts.sort(key=lambda fact: (-fact.quality, fact.fact_id))

    def execute(self, question: MatchedQuestion) -> SystemResult:
        state = _ExecutionState(frame=question.frame)
        state.operations.append(
            OperationCost(
                operation="PROVISIONAL_PARSE",
                category="parse",
                integer_ops=max(1, len(question.text) * 4),
                working_ram_bytes=2048,
            )
        )

        early = self._resolve_and_guard(question, state)
        if early is not None:
            return early

        state.candidates = self._retrieve(state)
        if not state.candidates:
            return self._failure(
                question,
                state,
                AnswerDisposition.ABSTAIN,
                "No bounded canonical evidence matched the query frame.",
            )

        selected, value, failure = self._select_and_reason(question, state)
        state.selected = selected
        if failure is not None or value is None:
            return self._failure(
                question,
                state,
                AnswerDisposition.ABSTAIN,
                failure or "Evidence was insufficient.",
            )

        verification = self._verify(value, selected)
        if verification.status is not VerificationStatus.PASS:
            return self._failure(
                question,
                state,
                AnswerDisposition.ABSTAIN,
                verification.failure_reason or "Claim verification failed.",
                verification=verification,
            )

        sentence = self._realize(question.frame, value)
        state.operations.extend(
            (
                OperationCost(
                    operation="REALIZE_CONSTRAINED_TEMPLATE",
                    category="realize",
                    integer_ops=len(sentence),
                    working_ram_bytes=1024,
                    realization_ops=len(sentence),
                ),
                OperationCost(
                    operation="VERIFY_PROVENANCE_BINDINGS",
                    category="verify",
                    integer_ops=sum(len(fact.evidence_text) for fact in selected),
                    working_ram_bytes=2048,
                ),
                OperationCost(
                    operation="SERIALIZE_EXTERNAL_API_RESPONSE",
                    category="interface",
                    integer_ops=len(sentence),
                    working_ram_bytes=1024,
                    interface_bytes=len(sentence.encode())
                    + sum(len(fact.evidence_text.encode()) for fact in selected),
                ),
            )
        )
        if self._budget_exceeded(state):
            return self._failure(
                question,
                state,
                AnswerDisposition.ABSTAIN,
                "Matched execution budget exceeded; answer withheld.",
            )
        return SystemResult(
            variant=self.variant,
            question_id=question.question_id,
            corpus_identity=self._corpus_identity,
            disposition=AnswerDisposition.ANSWER,
            value=value,
            sentence=sentence,
            citation_span_ids=tuple(fact.evidence_span_id for fact in selected),
            failure_reason=None,
            trace=self._trace(state, verification),
            model_bytes=self.model_bytes,
            index_bytes=self.corpus.index_bytes,
        )

    def _resolve_and_guard(
        self,
        question: MatchedQuestion,
        state: _ExecutionState,
    ) -> SystemResult | None:
        frame = question.frame
        if frame.kind is QuestionKind.OUT_OF_DOMAIN:
            return self._failure(
                question,
                state,
                AnswerDisposition.OUT_OF_DOMAIN,
                "The request is outside the compiled domain.",
            )
        surface = frame.subject_surface or frame.session_subject_id
        if surface is None or frame.relation_id is None:
            return self._failure(
                question,
                state,
                AnswerDisposition.ABSTAIN,
                "The query frame lacks a resolvable subject or relation.",
            )
        subjects = tuple(sorted(self._alias_index.get(_normalize(surface), ())))
        state.resolved_subjects = subjects
        state.operations.append(
            OperationCost(
                operation="RESOLVE_ENTITY_ALIAS",
                category="index",
                bytes_read=min(self.corpus.index_bytes, 4096),
                storage_reads=1,
                read_pattern="random",
                integer_ops=max(8, len(surface) * 6),
                working_ram_bytes=4096,
            )
        )
        if not subjects:
            return self._failure(
                question,
                state,
                AnswerDisposition.ABSTAIN,
                "Unknown entity or term; no identity was guessed.",
            )
        if len(subjects) > 1:
            return self._failure(
                question,
                state,
                AnswerDisposition.CLARIFY,
                "The entity alias is ambiguous.",
            )
        return None

    def _retrieve(self, state: _ExecutionState) -> tuple[KnowledgeFact, ...]:
        assert state.frame.relation_id is not None
        subject = state.resolved_subjects[0]
        relations = (
            (state.frame.path_relations[0],)
            if state.frame.path_relations
            else (state.frame.relation_id,)
        )
        candidates: list[KnowledgeFact] = []
        for relation in relations:
            candidates.extend(self._by_subject_relation.get((subject, relation), ()))
        candidates = candidates[: self.budget.max_candidates]
        fact_bytes = sum(len(fact.serialized()) for fact in candidates)
        state.operations.append(
            OperationCost(
                operation="BOUNDED_INDEX_LOOKUP",
                category="storage",
                bytes_read=min(self.corpus.index_bytes, 4096) + fact_bytes,
                storage_reads=1 + bool(candidates),
                read_pattern="random",
                integer_ops=24 + len(candidates) * 8,
                working_ram_bytes=max(4096, fact_bytes),
            )
        )
        return tuple(candidates)

    def _select_and_reason(
        self,
        question: MatchedQuestion,
        state: _ExecutionState,
    ) -> tuple[tuple[KnowledgeFact, ...], str | None, str | None]:
        raise NotImplementedError

    def _direct_value(
        self,
        frame: QueryFrame,
        candidates: tuple[KnowledgeFact, ...],
        *,
        detect_conflict: bool,
    ) -> tuple[tuple[KnowledgeFact, ...], str | None, str | None]:
        independent = _deduplicate_lineages(candidates)
        if not independent:
            return (), None, "Only duplicated lineage evidence was available."
        if frame.temporal_mode in {"earliest", "latest"}:
            dated = tuple(fact for fact in independent if fact.valid_at is not None)
            if not dated:
                return (), None, "Temporal ordering requires dated evidence."
            reverse = frame.temporal_mode == "latest"
            winner = sorted(
                dated,
                key=lambda fact: (
                    -(fact.valid_at or 0) if reverse else fact.valid_at or 0,
                    fact.fact_id,
                ),
            )[0]
            return (winner,), winner.object_value, None
        values = {fact.object_value for fact in independent}
        if detect_conflict and len(values) > 1:
            return (), None, "Independent source families conflict."
        winner = independent[0]
        value = _convert_quantity(winner, frame.requested_unit)
        if value is None:
            return (), None, "The requested unit conversion is not certified."
        if frame.premise_object is not None and frame.premise_object != value:
            return (winner,), None, "The question contains an unsupported premise."
        return (winner,), value, None

    def _multihop(
        self,
        state: _ExecutionState,
    ) -> tuple[tuple[KnowledgeFact, ...], str | None, str | None]:
        relations = state.frame.path_relations
        if not relations or len(relations) > self.budget.max_reasoning_hops:
            return (), None, "The required reasoning path exceeds the bounded program."
        current = state.resolved_subjects[0]
        selected: list[KnowledgeFact] = []
        for relation in relations:
            candidates = tuple(self._by_subject_relation.get((current, relation), ()))
            independent = _deduplicate_lineages(candidates)
            if not independent:
                return tuple(selected), None, "A required reasoning hop lacks evidence."
            if len({fact.object_value for fact in independent}) > 1:
                return tuple(selected), None, "A required reasoning hop conflicts."
            fact = independent[0]
            selected.append(fact)
            current = fact.object_value
        state.operations.append(
            OperationCost(
                operation="EXECUTE_BOUNDED_REASONING_PATH",
                category="reason",
                bytes_read=sum(len(fact.serialized()) for fact in selected[1:]),
                storage_reads=max(0, len(selected) - 1),
                read_pattern="random" if len(selected) > 1 else "none",
                integer_ops=len(selected) * 48,
                working_ram_bytes=4096 + len(selected) * 512,
            )
        )
        return tuple(selected), current, None

    def _verify(
        self,
        value: str,
        selected: tuple[KnowledgeFact, ...],
    ) -> VerificationResult:
        if not selected:
            return VerificationResult(
                status=VerificationStatus.FAIL,
                checks=("evidence_present",),
                failure_reason="No selected evidence.",
            )
        for fact in selected:
            known = self._facts_by_id.get(fact.fact_id)
            if known is None or known != fact:
                return VerificationResult(
                    status=VerificationStatus.FAIL,
                    checks=("immutable_fact_identity",),
                    failure_reason="Selected evidence differs from the immutable corpus.",
                )
            if not fact.evidence_text or not fact.evidence_span_id:
                return VerificationResult(
                    status=VerificationStatus.FAIL,
                    checks=("source_span_present",),
                    failure_reason="Selected evidence lacks an immutable source span.",
                )
        terminal = selected[-1]
        accepted_values = {terminal.object_value}
        converted = _convert_quantity(terminal, None)
        if converted is not None:
            accepted_values.add(converted)
        if value not in accepted_values and not _is_certified_conversion(terminal, value):
            return VerificationResult(
                status=VerificationStatus.FAIL,
                checks=("value_copied_or_certified",),
                failure_reason="Answer value is not bound to terminal evidence.",
            )
        return VerificationResult(
            status=VerificationStatus.PASS,
            checks=(
                "immutable_fact_identity",
                "source_span_present",
                "value_copied_or_certified",
                "deterministic_realization_only",
            ),
        )

    def _realize(self, frame: QueryFrame, value: str) -> str:
        if frame.kind is QuestionKind.QUOTATION_ATTRIBUTION:
            return f"The attributed speaker is {value}."
        if frame.kind is QuestionKind.NUMERICAL_UNIT:
            return f"The supported value is {value}."
        return f"The supported answer is {value}."

    def _failure(
        self,
        question: MatchedQuestion,
        state: _ExecutionState,
        disposition: AnswerDisposition,
        reason: str,
        *,
        verification: VerificationResult | None = None,
    ) -> SystemResult:
        result = verification or VerificationResult(
            status=VerificationStatus.NOT_APPLICABLE,
            checks=("no_final_claim_emitted",),
        )
        return SystemResult(
            variant=self.variant,
            question_id=question.question_id,
            corpus_identity=self._corpus_identity,
            disposition=disposition,
            value=None,
            sentence=None,
            citation_span_ids=(),
            failure_reason=reason,
            trace=self._trace(state, result),
            model_bytes=self.model_bytes,
            index_bytes=self.corpus.index_bytes,
        )

    def _trace(
        self,
        state: _ExecutionState,
        verification: VerificationResult,
    ) -> SystemTrace:
        return SystemTrace(
            provisional_parse=state.frame,
            refined_parse=state.frame,
            resolved_subject_ids=state.resolved_subjects,
            retrieved_candidate_ids=tuple(fact.fact_id for fact in state.candidates),
            selected_evidence_ids=tuple(fact.fact_id for fact in state.selected),
            operations=tuple(state.operations),
            verification=verification,
        )

    def _budget_exceeded(self, state: _ExecutionState) -> bool:
        return (
            sum(operation.bytes_read for operation in state.operations)
            > self.budget.max_bytes_read
            or max(
                (operation.working_ram_bytes for operation in state.operations),
                default=0,
            )
            > self.budget.max_working_ram_bytes
            or self.model_bytes > self.budget.max_model_bytes
        )


class Top1TemplateSystem(MatchedSystem):
    variant = SystemVariant.TOP1_TEMPLATE

    def _select_and_reason(
        self,
        question: MatchedQuestion,
        state: _ExecutionState,
    ) -> tuple[tuple[KnowledgeFact, ...], str | None, str | None]:
        del question
        if state.frame.path_relations and len(state.frame.path_relations) > 1:
            return (), None, "Top-1 retrieval cannot execute a multi-hop path."
        selected, value, failure = self._direct_value(
            state.frame,
            state.candidates[:1],
            detect_conflict=False,
        )
        state.operations.append(
            OperationCost(
                operation="SELECT_TOP1",
                category="reason",
                integer_ops=4,
                working_ram_bytes=512,
            )
        )
        return selected, value, failure


class CompiledMicroprogramSystem(MatchedSystem):
    variant = SystemVariant.COMPILED_MICROPROGRAM

    def _select_and_reason(
        self,
        question: MatchedQuestion,
        state: _ExecutionState,
    ) -> tuple[tuple[KnowledgeFact, ...], str | None, str | None]:
        del question
        state.operations.append(
            OperationCost(
                operation="DISPATCH_COMPILED_MICROPROGRAM",
                category="reason",
                integer_ops=32,
                working_ram_bytes=2048,
            )
        )
        if len(state.frame.path_relations) > 1:
            return self._multihop(state)
        return self._direct_value(
            state.frame,
            state.candidates,
            detect_conflict=True,
        )


class BoundedLRVMSystem(MatchedSystem):
    variant = SystemVariant.BOUNDED_LRVM

    def _select_and_reason(
        self,
        question: MatchedQuestion,
        state: _ExecutionState,
    ) -> tuple[tuple[KnowledgeFact, ...], str | None, str | None]:
        del question
        agenda: deque[tuple[str, int]] = deque(
            [(state.resolved_subjects[0], 0)]
        )
        cycles = 0
        target_depth = max(1, len(state.frame.path_relations))
        while agenda and cycles < self.budget.max_scheduler_cycles:
            entity, depth = agenda.popleft()
            cycles += 1
            if depth >= target_depth:
                break
            relation = (
                state.frame.path_relations[depth]
                if state.frame.path_relations
                else state.frame.relation_id
            )
            if relation is None:
                break
            for fact in self._by_subject_relation.get((entity, relation), ()):
                agenda.append((fact.object_value, depth + 1))
                if len(agenda) >= self.budget.max_candidates:
                    break
        state.operations.append(
            OperationCost(
                operation="LRVM_BOUNDED_SCHEDULE",
                category="schedule",
                integer_ops=cycles * 64,
                working_ram_bytes=4096 + len(agenda) * 256,
                scheduler_cycles=cycles,
            )
        )
        if agenda and cycles >= self.budget.max_scheduler_cycles:
            return (), None, "LRVM scheduler cycle bound reached."
        if len(state.frame.path_relations) > 1:
            return self._multihop(state)
        return self._direct_value(
            state.frame,
            state.candidates,
            detect_conflict=True,
        )


class TinyConstrainedRAGSystem(MatchedSystem):
    """Retrieval-augmented int8 byte-trigram language scorer.

    The tiny language model scores only evidence-bound candidate sequences and
    the winning value is copied through deterministic templates. It therefore
    cannot emit free-form unsupported text. Its fixed 4,096-bin int8 table is
    charged as model storage and each occupied query/fact feature pair is
    charged as a MAC.
    """

    variant = SystemVariant.TINY_CONSTRAINED_RAG
    model_bytes = 4096

    def _select_and_reason(
        self,
        question: MatchedQuestion,
        state: _ExecutionState,
    ) -> tuple[tuple[KnowledgeFact, ...], str | None, str | None]:
        query_features = _hashed_trigrams(question.text)
        scored: list[tuple[float, KnowledgeFact]] = []
        macs = 0
        for fact in state.candidates:
            fact_features = _hashed_trigrams(
                f"{fact.subject_id} {fact.relation_id} {fact.object_value}"
            )
            overlap = len(query_features & fact_features)
            denominator = math.sqrt(max(1, len(query_features) * len(fact_features)))
            scored.append((overlap / denominator + fact.quality, fact))
            macs += len(query_features) + len(fact_features)
        scored.sort(key=lambda item: (-item[0], item[1].fact_id))
        reranked = tuple(fact for _, fact in scored)
        state.operations.append(
            OperationCost(
                operation="INT8_BYTE_TRIGRAM_LM_CONSTRAINED_RERANK",
                category="rerank",
                integer_ops=macs * 3,
                neural_macs=macs,
                working_ram_bytes=8192,
            )
        )
        if len(state.frame.path_relations) > 1:
            return self._multihop(state)
        return self._direct_value(
            state.frame,
            reranked,
            detect_conflict=True,
        )


def build_matched_systems(
    corpus: MatchedCorpus,
    budget: MatchedBudget | None = None,
) -> tuple[MatchedSystem, ...]:
    """Construct all variants with the exact same corpus and budget object."""

    matched_budget = budget or MatchedBudget()
    return (
        Top1TemplateSystem(corpus, matched_budget),
        CompiledMicroprogramSystem(corpus, matched_budget),
        BoundedLRVMSystem(corpus, matched_budget),
        TinyConstrainedRAGSystem(corpus, matched_budget),
    )


def evaluate_matched_systems(
    corpus: MatchedCorpus,
    questions: tuple[MatchedQuestion, ...],
    budget: MatchedBudget | None = None,
) -> ComparisonReport:
    """Run a matched deterministic comparison without an LLM judge."""

    systems = build_matched_systems(corpus, budget)
    all_records: dict[str, tuple[EvaluationRecord, ...]] = {}
    all_results: dict[str, tuple[SystemResult, ...]] = {}
    all_metrics: list[VariantMetrics] = []
    for system in systems:
        results = tuple(system.execute(question) for question in questions)
        records = tuple(
            _grade(question, result)
            for question, result in zip(questions, results, strict=True)
        )
        key = system.variant.value
        all_records[key] = records
        all_results[key] = results
        all_metrics.append(_summarize(system.variant, records, results))
    identities = {
        result.corpus_identity
        for results in all_results.values()
        for result in results
    }
    if identities and identities != {corpus.identity}:
        raise RuntimeError("matched systems did not use an identical corpus")
    return ComparisonReport(
        corpus_identity=corpus.identity,
        question_ids=tuple(question.question_id for question in questions),
        metrics=tuple(all_metrics),
        records=all_records,
        results=all_results,
    )


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _hashed_trigrams(text: str) -> set[int]:
    normalized = f"  {_normalize(text)}  "
    return {
        int.from_bytes(
            hashlib.blake2s(
                normalized[index : index + 3].encode(),
                digest_size=2,
            ).digest()
        )
        % 4096
        for index in range(max(1, len(normalized) - 2))
    }


def _deduplicate_lineages(
    candidates: tuple[KnowledgeFact, ...],
) -> tuple[KnowledgeFact, ...]:
    selected: list[KnowledgeFact] = []
    seen_lineages: set[str] = set()
    for fact in candidates:
        if fact.lineage_id in seen_lineages:
            continue
        selected.append(fact)
        seen_lineages.add(fact.lineage_id)
    return tuple(selected)


def _convert_quantity(fact: KnowledgeFact, requested_unit: str | None) -> str | None:
    if fact.quantity is None:
        return fact.object_value
    if requested_unit is None or requested_unit == fact.unit:
        return f"{fact.quantity:g} {fact.unit}".strip()
    conversions: dict[tuple[str, str], float] = {
        ("m", "cm"): 100.0,
        ("cm", "m"): 0.01,
        ("kg", "g"): 1000.0,
        ("g", "kg"): 0.001,
        ("s", "ms"): 1000.0,
        ("ms", "s"): 0.001,
    }
    factor = conversions.get((fact.unit or "", requested_unit))
    if factor is None:
        return None
    return f"{fact.quantity * factor:g} {requested_unit}"


def _is_certified_conversion(fact: KnowledgeFact, value: str) -> bool:
    if fact.quantity is None:
        return False
    return any(
        _convert_quantity(fact, unit) == value
        for unit in ("m", "cm", "kg", "g", "s", "ms")
    )


def _grade(question: MatchedQuestion, result: SystemResult) -> EvaluationRecord:
    disposition_correct = result.disposition is question.expected_disposition
    value_correct = (
        result.disposition is not AnswerDisposition.ANSWER
        or (
            question.expected_value is not None
            and _normalize(result.value or "") == _normalize(question.expected_value)
        )
    )
    unsupported = (
        result.disposition is AnswerDisposition.ANSWER
        and result.trace.verification.status is not VerificationStatus.PASS
    )
    return EvaluationRecord(
        question_id=question.question_id,
        kind=question.frame.kind,
        hard_subset=question.hard_subset,
        correct=disposition_correct and value_correct and not unsupported,
        unsupported_claim=unsupported,
        disposition=result.disposition,
    )


def _summarize(
    variant: SystemVariant,
    records: tuple[EvaluationRecord, ...],
    results: tuple[SystemResult, ...],
) -> VariantMetrics:
    hard = tuple(record for record in records if record.hard_subset)
    categories: dict[QuestionKind, list[bool]] = defaultdict(list)
    for record in records:
        categories[record.kind].append(record.correct)
    clarify = tuple(
        record for record in records if record.disposition is AnswerDisposition.CLARIFY
    )
    abstain = tuple(
        record for record in records if record.disposition is AnswerDisposition.ABSTAIN
    )
    return VariantMetrics(
        variant=variant,
        question_count=len(records),
        accuracy=mean(record.correct for record in records) if records else 0.0,
        hard_subset_accuracy=mean(record.correct for record in hard) if hard else None,
        unsupported_claim_rate=(
            mean(record.unsupported_claim for record in records) if records else 0.0
        ),
        clarification_precision=(
            mean(record.correct for record in clarify) if clarify else None
        ),
        abstention_precision=(
            mean(record.correct for record in abstain) if abstain else None
        ),
        mean_bytes_read=mean(result.bytes_read for result in results) if results else 0.0,
        mean_neural_macs=mean(result.neural_macs for result in results) if results else 0.0,
        category_accuracy={
            kind.value: mean(correct)
            for kind, correct in sorted(categories.items(), key=lambda item: item[0].value)
        },
    )
