"""End-to-end autonomous architecture qualification.

This module turns structured synthetic worlds into matched runtime corpora,
trains compact fixed-shape components on the development partition, freezes the
qualification identity, and evaluates the hidden partition without an LLM
judge.  Phase 0 files and runtime behavior are not modified.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, is_dataclass
from enum import StrEnum
from pathlib import Path
from statistics import mean
from typing import Any, cast

from pydantic import BaseModel

from aethersparse.autonomy.digital_twin import (
    build_workload_profile,
    recommend_backend,
)
from aethersparse.autonomy.extraction import (
    AdjudicationArtifact,
    AdjudicationDecision,
    ExtractionArtifact,
    IndependentAdjudicator,
    IndependentExtractor,
    IndependentValidator,
    ValidationArtifact,
)
from aethersparse.autonomy.learned import (
    AliasExample,
    ContradictionProbe,
    EntityAliasLinker,
    EvidenceExample,
    EvidenceGapProbe,
    EvidenceReranker,
    ProbeExample,
    QueryFrameParser,
    TextExample,
)
from aethersparse.autonomy.synthetic import (
    INTERMEDIATE_SCALE,
    SCALE_CONFIGS,
    ScaleConfig,
    SyntheticClaim,
    SyntheticEntity,
    SyntheticQuestion,
    SyntheticSourceSpan,
    SyntheticWorld,
    generate_world,
    stable_json,
)
from aethersparse.autonomy.systems import (
    AnswerDisposition,
    KnowledgeFact,
    MatchedBudget,
    MatchedCorpus,
    MatchedQuestion,
    QueryFrame,
    QuestionKind,
    SystemVariant,
    build_matched_systems,
    evaluate_matched_systems,
)

QUALIFICATION_VERSION = "aethersparse-autonomous-qualification-v1"
DEFAULT_MASTER_SEED = "aethersparse-autonomous-campaign-2026-07-30-v1"


class ArchitectureDecision(StrEnum):
    AETHERSPARSE_VIABLE = "AETHERSPARSE_VIABLE"
    HYBRID_RAG_PREFERRED = "HYBRID_RAG_PREFERRED"
    ARCHITECTURE_FAILED = "ARCHITECTURE_FAILED"


@dataclass(frozen=True, slots=True)
class PacketMetrics:
    gold_packet_count: int
    visible_gold_packet_count: int
    candidate_count: int
    canonical_count: int
    quarantine_count: int
    reject_count: int
    true_canonical_count: int
    unique_visible_claims_recovered: int
    precision: float
    visible_recall: float
    full_world_recall: float
    atomic_alignment_accuracy: float
    incorrect_entity_rate: float
    incorrect_relation_rate: float
    validator_independence_rate: float
    duplicate_candidate_rate: float
    mutation_rejection_rate: float
    extraction_seconds: float
    validation_seconds: float
    adjudication_seconds: float
    source_bytes: int
    canonical_serialized_bytes: int
    deterministic_reproduction: bool


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return _jsonable(value.model_dump(mode="json"))
    if is_dataclass(value):
        return {
            key: _jsonable(item)
            for key, item in asdict(cast(Any, value)).items()
        }
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _sha256(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    path.write_text(payload, encoding="utf-8")


def _write_gzip_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = stable_json(_jsonable(value))
    with gzip.GzipFile(
        filename=path,
        mode="wb",
        compresslevel=6,
        mtime=0,
    ) as stream:
        stream.write(payload)


def _date_integer(date_value: str | None) -> int | None:
    if date_value is None:
        return None
    return int(date_value.replace("-", ""))


def _claim_value(claim: SyntheticClaim) -> str:
    value = claim.object_value
    return f"NOT:{value}" if claim.polarity == "negative" else value


def _entity_aliases(entity: SyntheticEntity) -> tuple[str, ...]:
    return (
        entity.canonical_name,
        *entity.aliases,
        *entity.ambiguous_aliases,
    )


def build_matched_corpus(world: SyntheticWorld) -> MatchedCorpus:
    """Compile all source-visible structured facts into one immutable corpus."""

    entity_index = {entity.entity_id: entity for entity in world.entities}
    claim_index = {claim.claim_id: claim for claim in world.claims}
    facts: list[KnowledgeFact] = []
    for source in world.sources:
        for span in source.spans:
            claim = claim_index[span.claim_id]
            subject = entity_index[claim.subject_id]
            facts.append(
                KnowledgeFact(
                    fact_id=f"fact:{span.span_id}",
                    subject_id=claim.subject_id,
                    relation_id=claim.relation,
                    object_value=_claim_value(claim),
                    evidence_span_id=span.span_id,
                    evidence_text=span.raw_text,
                    source_doc_id=source.source_doc_id,
                    source_family=claim.source_family,
                    lineage_id=f"{claim.source_family}:{claim.lineage_id}",
                    aliases=_entity_aliases(subject),
                    valid_at=_date_integer(claim.date_value),
                    quantity=claim.quantity_value,
                    unit=claim.quantity_unit,
                    polarity=claim.polarity,
                    attribution=claim.attribution_id,
                    quality=0.99 if claim.domain == "core" else 0.5,
                )
            )
    relation_count = len({fact.relation_id for fact in facts})
    alias_count = sum(len(fact.aliases) for fact in facts)
    index_bytes = max(1024, len(facts) * 24 + alias_count * 8 + relation_count * 32)
    return MatchedCorpus(
        corpus_id=world.manifest.world_id,
        facts=tuple(facts),
        domain_relations=tuple(sorted({fact.relation_id for fact in facts})),
        index_bytes=index_bytes,
    )


def _spans_by_claim(
    world: SyntheticWorld,
) -> dict[str, list[SyntheticSourceSpan]]:
    spans: dict[str, list[SyntheticSourceSpan]] = defaultdict(list)
    for source in world.sources:
        for span in source.spans:
            spans[span.claim_id].append(span)
    return spans


def _subject_surface(
    claim: SyntheticClaim,
    entities: dict[str, SyntheticEntity],
) -> str:
    return entities[claim.subject_id].canonical_name


def _direct_frame(
    claim: SyntheticClaim,
    entities: dict[str, SyntheticEntity],
    kind: QuestionKind,
    **updates: Any,
) -> QueryFrame:
    fields: dict[str, object] = {
        "subject_surface": _subject_surface(claim, entities),
        "relation_id": claim.relation,
        "kind": kind,
    }
    fields.update(updates)
    return QueryFrame(**fields)  # type: ignore[arg-type]


def build_matched_questions(
    world: SyntheticWorld,
    *,
    question_count: int | None = None,
) -> tuple[MatchedQuestion, ...]:
    """Author a deterministic decisive suite from structured truth.

    The suite intentionally repeats category templates over different claims at
    large scales.  Frames and expected values are derived from the structured
    world, never from generated prose or a model judge.
    """

    count = question_count or world.manifest.question_count
    entities = {entity.entity_id: entity for entity in world.entities}
    spans = _spans_by_claim(world)
    visible = [claim for claim in world.claims if spans.get(claim.claim_id)]
    by_key: dict[tuple[str, str], list[SyntheticClaim]] = defaultdict(list)
    for claim in visible:
        by_key[(claim.subject_id, claim.relation)].append(claim)
    conflict_keys = {
        key
        for key, values in by_key.items()
        if len({_claim_value(claim) for claim in values}) > 1
    }
    normal = [
        claim
        for claim in visible
        if (claim.subject_id, claim.relation) not in conflict_keys
        and not claim.ambiguous_entity
        and claim.domain == "core"
    ]
    events = [claim for claim in normal if claim.date_value is not None]
    quantities = [claim for claim in normal if claim.quantity_value is not None]
    quotations = [claim for claim in normal if claim.packet_type == "QUOTATION"]
    duplicates = [claim for claim in normal if len(spans[claim.claim_id]) > 1]
    missing = [
        claim
        for claim in world.claims
        if claim.missing_evidence
        and any(item.subject_id == claim.subject_id for item in visible)
    ]
    conflicts = [
        values[0]
        for key, values in sorted(by_key.items())
        if key in conflict_keys
    ]

    outgoing: dict[str, list[SyntheticClaim]] = defaultdict(list)
    for claim in normal:
        if claim.object_is_entity and claim.polarity == "positive":
            outgoing[claim.subject_id].append(claim)
    chains: list[tuple[SyntheticClaim, SyntheticClaim]] = []
    for first in normal:
        if not first.object_is_entity or first.polarity != "positive":
            continue
        for second in outgoing.get(first.object_value, ()):
            if first.relation != second.relation or first.claim_id == second.claim_id:
                chains.append((first, second))
                break

    ambiguous: list[tuple[str, SyntheticClaim]] = []
    alias_owners: dict[str, set[str]] = defaultdict(set)
    for entity in world.entities:
        for alias in entity.ambiguous_aliases:
            alias_owners[alias].add(entity.entity_id)
    for alias, owners in sorted(alias_owners.items()):
        available = [
            claim
            for claim in visible
            if claim.subject_id in owners
        ]
        if len({claim.subject_id for claim in available}) > 1:
            ambiguous.append((alias, available[0]))

    required: dict[str, int] = {
        "normal": len(normal),
        "events": len(events),
        "quantities": len(quantities),
        "quotations": len(quotations),
        "duplicates": len(duplicates),
        "missing": len(missing),
        "conflicts": len(conflicts),
        "chains": len(chains),
        "ambiguous": len(ambiguous),
    }
    empty = [name for name, size in required.items() if size == 0]
    if empty:
        raise ValueError(f"synthetic world lacks decisive question pools: {empty}")

    kinds = tuple(QuestionKind)
    questions: list[MatchedQuestion] = []
    for index in range(count):
        kind = kinds[index % len(kinds)]
        expected = AnswerDisposition.ANSWER
        expected_value: str | None
        hard = kind not in {
            QuestionKind.DIRECT_FACT,
            QuestionKind.UNSEEN_PARAPHRASE,
            QuestionKind.NUMERICAL_UNIT,
            QuestionKind.QUOTATION_ATTRIBUTION,
            QuestionKind.DUPLICATED_SOURCE_FAMILY,
        }
        if kind is QuestionKind.CAUSAL_MULTIHOP:
            first, second = chains[index % len(chains)]
            frame = QueryFrame(
                subject_surface=_subject_surface(first, entities),
                relation_id=second.relation,
                kind=kind,
                path_relations=(first.relation, second.relation),
            )
            text = f"What follows two verified links from {_subject_surface(first, entities)}?"
            expected_value = _claim_value(second)
        elif kind is QuestionKind.AMBIGUOUS_ENTITY:
            alias, claim = ambiguous[index % len(ambiguous)]
            frame = QueryFrame(
                subject_surface=alias,
                relation_id=claim.relation,
                kind=kind,
            )
            text = f"What is recorded for {alias}?"
            expected = AnswerDisposition.CLARIFY
            expected_value = None
        elif kind is QuestionKind.CONFLICTING_SOURCES:
            claim = conflicts[index % len(conflicts)]
            frame = _direct_frame(claim, entities, kind)
            text = (
                "What value is supported by independent sources for "
                f"{_subject_surface(claim, entities)}?"
            )
            expected = AnswerDisposition.ABSTAIN
            expected_value = None
        elif kind is QuestionKind.MISSING_EVIDENCE:
            claim = missing[index % len(missing)]
            frame = _direct_frame(claim, entities, kind)
            text = f"What undocumented value belongs to {_subject_surface(claim, entities)}?"
            expected = AnswerDisposition.ABSTAIN
            expected_value = None
        elif kind is QuestionKind.UNKNOWN_TERM:
            claim = normal[index % len(normal)]
            frame = QueryFrame(
                subject_surface=f"ux-unseen-{index:07d}",
                relation_id=claim.relation,
                kind=kind,
            )
            text = f"What does ux-unseen-{index:07d} mean?"
            expected = AnswerDisposition.ABSTAIN
            expected_value = None
        elif kind is QuestionKind.OUT_OF_DOMAIN:
            frame = QueryFrame(
                subject_surface=None,
                relation_id=None,
                kind=kind,
            )
            text = "Write an unrelated kernel driver."
            expected = AnswerDisposition.OUT_OF_DOMAIN
            expected_value = None
        elif kind is QuestionKind.WRONG_PREMISE:
            claim = normal[index % len(normal)]
            frame = _direct_frame(
                claim,
                entities,
                kind,
                premise_object=f"wrong:{_claim_value(claim)}",
            )
            text = f"Why is the opposite value true for {_subject_surface(claim, entities)}?"
            expected = AnswerDisposition.ABSTAIN
            expected_value = None
        elif kind is QuestionKind.ADVERSARIAL_ENTITY:
            claim = normal[index % len(normal)]
            frame = QueryFrame(
                subject_surface=f"mutated-{claim.subject_id}",
                relation_id=claim.relation,
                kind=kind,
            )
            text = f"What is recorded for mutated-{claim.subject_id}?"
            expected = AnswerDisposition.ABSTAIN
            expected_value = None
        elif kind is QuestionKind.ADVERSARIAL_DATE:
            claim = events[index % len(events)]
            frame = _direct_frame(
                claim,
                entities,
                kind,
                premise_object="2099-12-31",
            )
            text = f"Did {_subject_surface(claim, entities)} occur on 2099-12-31?"
            expected = AnswerDisposition.ABSTAIN
            expected_value = None
        elif kind is QuestionKind.ADVERSARIAL_QUANTITY:
            claim = quantities[index % len(quantities)]
            frame = _direct_frame(
                claim,
                entities,
                kind,
                requested_unit="parsec",
            )
            text = f"Give {_subject_surface(claim, entities)} in parsecs."
            expected = AnswerDisposition.ABSTAIN
            expected_value = None
        elif kind is QuestionKind.ADVERSARIAL_NEGATION:
            claim = next(
                item
                for item in normal[index % len(normal) :] + normal[: index % len(normal)]
                if item.polarity == "positive"
            )
            frame = _direct_frame(
                claim,
                entities,
                kind,
                premise_object=f"NOT:{_claim_value(claim)}",
            )
            text = f"Is the negated record true for {_subject_surface(claim, entities)}?"
            expected = AnswerDisposition.ABSTAIN
            expected_value = None
        elif kind is QuestionKind.ADVERSARIAL_ATTRIBUTION:
            claim = quotations[index % len(quotations)]
            frame = _direct_frame(
                claim,
                entities,
                kind,
                premise_object="mutated-attributor",
            )
            text = (
                "Was the quotation by a mutated attributor for "
                f"{_subject_surface(claim, entities)}?"
            )
            expected = AnswerDisposition.ABSTAIN
            expected_value = None
        else:
            if kind is QuestionKind.TEMPORAL_ORDERING:
                claim = events[index % len(events)]
                values = by_key[(claim.subject_id, claim.relation)]
                dated = [item for item in values if item.date_value is not None]
                earliest = min(dated, key=lambda item: item.date_value or "")
                frame = _direct_frame(
                    claim,
                    entities,
                    kind,
                    temporal_mode="earliest",
                )
                text = f"What is the earliest event for {_subject_surface(claim, entities)}?"
                expected_value = _claim_value(earliest)
            elif kind is QuestionKind.NUMERICAL_UNIT:
                claim = quantities[index % len(quantities)]
                frame = _direct_frame(
                    claim,
                    entities,
                    kind,
                    requested_unit=claim.quantity_unit,
                )
                text = f"What measured value belongs to {_subject_surface(claim, entities)}?"
                expected_value = _claim_value(claim)
            elif kind is QuestionKind.QUOTATION_ATTRIBUTION:
                claim = quotations[index % len(quotations)]
                frame = _direct_frame(claim, entities, kind)
                text = f"What exact speech is attributed to {_subject_surface(claim, entities)}?"
                expected_value = _claim_value(claim)
            elif kind is QuestionKind.DUPLICATED_SOURCE_FAMILY:
                claim = duplicates[index % len(duplicates)]
                frame = _direct_frame(claim, entities, kind)
                text = (
                    "What remains after lineage deduplication for "
                    f"{_subject_surface(claim, entities)}?"
                )
                expected_value = _claim_value(claim)
            elif kind is QuestionKind.SESSION_FOLLOWUP:
                claim = normal[index % len(normal)]
                frame = QueryFrame(
                    subject_surface=None,
                    relation_id=claim.relation,
                    kind=kind,
                    session_subject_id=claim.subject_id,
                )
                text = "What about that entity?"
                expected_value = _claim_value(claim)
            else:
                claim = normal[index % len(normal)]
                frame = _direct_frame(claim, entities, kind)
                text = (
                    f"State the compiled value for {_subject_surface(claim, entities)}."
                    if kind is QuestionKind.DIRECT_FACT
                    else (
                        "Tell me differently what is established about "
                        f"{_subject_surface(claim, entities)}."
                    )
                )
                expected_value = _claim_value(claim)
        questions.append(
            MatchedQuestion(
                question_id=f"matched:{world.manifest.partition}:{index:07d}",
                text=text,
                frame=frame,
                expected_disposition=expected,
                expected_value=expected_value,
                hard_subset=hard,
            )
        )
    return tuple(questions)


def evaluate_packets(
    world: SyntheticWorld,
    *,
    cache_dir: Path | None = None,
) -> tuple[
    PacketMetrics,
    ExtractionArtifact,
    ValidationArtifact,
    AdjudicationArtifact,
]:
    extractor = IndependentExtractor(world.entities)
    started = time.perf_counter()
    extraction = extractor.extract_world(world, cache_dir=cache_dir)
    extraction_seconds = time.perf_counter() - started
    started = time.perf_counter()
    validation = IndependentValidator(world.entities).validate_world(
        world,
        extraction,
        cache_dir=cache_dir,
    )
    validation_seconds = time.perf_counter() - started
    started = time.perf_counter()
    adjudication = IndependentAdjudicator().adjudicate_world(
        world,
        extraction,
        validation,
        cache_dir=cache_dir,
    )
    adjudication_seconds = time.perf_counter() - started

    source_index = {source.source_doc_id: source for source in world.sources}
    aligned = 0
    for candidate in extraction.candidates:
        source = source_index[candidate.source_doc_id]
        raw = source.raw_text[candidate.raw_char_start : candidate.raw_char_end]
        raw_byte_start = len(source.raw_text[: candidate.raw_char_start].encode("utf-8"))
        raw_byte_end = len(source.raw_text[: candidate.raw_char_end].encode("utf-8"))
        aligned += int(
            raw == candidate.evidence_surface
            and raw_byte_start == candidate.raw_byte_start
            and raw_byte_end == candidate.raw_byte_end
            and _sha256(raw.encode("utf-8")) == candidate.evidence_hash
        )

    disposition = Counter(result.decision for result in adjudication.results)
    canonical_results = [
        result
        for result in adjudication.results
        if result.decision is AdjudicationDecision.CANONICAL
    ]
    true_canonical = [result for result in canonical_results if result.synthetic_truth_match]
    recovered = {
        result.matched_claim_id
        for result in true_canonical
        if result.matched_claim_id is not None
    }
    visible_claims = {
        span.claim_id
        for source in world.sources
        for span in source.spans
    }
    candidate_claim_counts = Counter(
        result.matched_claim_id
        for result in adjudication.results
        if result.matched_claim_id is not None
    )
    duplicate_candidates = sum(max(0, count - 1) for count in candidate_claim_counts.values())
    candidate_index = {
        candidate.candidate_id: candidate for candidate in extraction.candidates
    }
    claim_index = {claim.claim_id: claim for claim in world.claims}
    entity_errors = 0
    relation_errors = 0
    for result in canonical_results:
        candidate = candidate_index[result.candidate_id]
        claim = claim_index[result.matched_claim_id or ""]
        entity_errors += int(candidate.subject_id != claim.subject_id)
        relation_errors += int(candidate.relation != claim.relation)
    reproduced = extractor.extract_world(world).artifact_hash == extraction.artifact_hash
    canonical_payload = [
        candidate_index[result.candidate_id].model_dump(mode="json")
        for result in canonical_results
    ]
    metrics = PacketMetrics(
        gold_packet_count=len(world.claims),
        visible_gold_packet_count=len(visible_claims),
        candidate_count=len(extraction.candidates),
        canonical_count=disposition[AdjudicationDecision.CANONICAL],
        quarantine_count=disposition[AdjudicationDecision.QUARANTINE],
        reject_count=disposition[AdjudicationDecision.REJECT],
        true_canonical_count=len(true_canonical),
        unique_visible_claims_recovered=len(recovered & visible_claims),
        precision=len(true_canonical) / max(1, len(canonical_results)),
        visible_recall=len(recovered & visible_claims) / max(1, len(visible_claims)),
        full_world_recall=len(recovered) / max(1, len(world.claims)),
        atomic_alignment_accuracy=aligned / max(1, len(extraction.candidates)),
        incorrect_entity_rate=entity_errors / max(1, len(canonical_results)),
        incorrect_relation_rate=relation_errors / max(1, len(canonical_results)),
        validator_independence_rate=mean(
            result.independent_from_extractor for result in validation.results
        ),
        duplicate_candidate_rate=duplicate_candidates / max(1, len(extraction.candidates)),
        mutation_rejection_rate=mean(
            result.mutation_rejection_count == 5
            for result in adjudication.results
            if result.synthetic_truth_match
        ),
        extraction_seconds=extraction_seconds,
        validation_seconds=validation_seconds,
        adjudication_seconds=adjudication_seconds,
        source_bytes=sum(len(source.raw_text.encode("utf-8")) for source in world.sources),
        canonical_serialized_bytes=len(stable_json(canonical_payload)),
        deterministic_reproduction=reproduced,
    )
    return metrics, extraction, validation, adjudication


def _balanced_questions(
    questions: tuple[SyntheticQuestion, ...],
    *,
    per_category: int,
) -> list[SyntheticQuestion]:
    counts: Counter[str] = Counter()
    selected: list[SyntheticQuestion] = []
    for question in questions:
        if counts[question.category] >= per_category:
            continue
        selected.append(question)
        counts[question.category] += 1
    return selected


def _claim_span_text(world: SyntheticWorld) -> dict[str, str]:
    return {
        span.claim_id: span.raw_text
        for source in world.sources
        for span in source.spans
    }


def train_and_evaluate_components(
    development: SyntheticWorld,
    evaluation: SyntheticWorld,
    *,
    model_dir: Path,
) -> dict[str, object]:
    """Train compact development-only models and evaluate isolated contexts."""

    model_dir.mkdir(parents=True, exist_ok=True)
    development_frames = build_matched_questions(
        development,
        question_count=development.manifest.question_count,
    )
    evaluation_frames = build_matched_questions(
        evaluation,
        question_count=evaluation.manifest.question_count,
    )
    parser_train_counts: Counter[QuestionKind] = Counter()
    parser_train: list[MatchedQuestion] = []
    for question in development_frames:
        if parser_train_counts[question.frame.kind] >= 48:
            continue
        parser_train.append(question)
        parser_train_counts[question.frame.kind] += 1
    parser = QueryFrameParser(
        tuple(sorted({question.frame.kind.value for question in parser_train})),
        known_terms=tuple(entity.canonical_name for entity in development.entities[:128]),
        feature_dim=256,
    )
    parser.fit(
        [
            TextExample(text=question.text, label=question.frame.kind.value)
            for question in parser_train
        ]
    )
    parser_eval_counts: Counter[QuestionKind] = Counter()
    parser_eval: list[MatchedQuestion] = []
    for question in evaluation_frames:
        if parser_eval_counts[question.frame.kind] >= 96:
            continue
        parser_eval.append(question)
        parser_eval_counts[question.frame.kind] += 1
    parser_correct = sum(
        parser.predict(question.text).frame_label == question.frame.kind.value
        for question in parser_eval
    )

    linker_entities = development.entities[:48]
    linker = EntityAliasLinker(
        tuple(entity.entity_id for entity in linker_entities),
        learned_threshold=0.5,
        feature_dim=256,
    )
    linker.fit(
        [
            AliasExample(alias=surface, entity_id=entity.entity_id)
            for entity in linker_entities
            for surface in (entity.canonical_name, entity.aliases[0])
        ]
    )
    linker_eval = [
        (entity.aliases[1], entity.entity_id)
        for entity in linker_entities
    ]
    linker_correct = sum(
        linker.link(surface).entity_id == expected
        for surface, expected in linker_eval
    )

    dev_spans = _claim_span_text(development)
    dev_questions = [
        question
        for question in development.questions
        if question.evidence_claim_ids
        and question.evidence_claim_ids[0] in dev_spans
    ][:256]
    reranker_examples: list[EvidenceExample] = []
    gap_examples: list[ProbeExample] = []
    span_values = list(dev_spans.values())
    for index, synthetic_question in enumerate(dev_questions):
        relevant = dev_spans[synthetic_question.evidence_claim_ids[0]]
        irrelevant = span_values[(index * 31 + 7) % len(span_values)]
        reranker_examples.extend(
            (
                EvidenceExample(synthetic_question.question, relevant, True),
                EvidenceExample(synthetic_question.question, irrelevant, False),
            )
        )
        gap_examples.extend(
            (
                ProbeExample(synthetic_question.question, relevant, False),
                ProbeExample(synthetic_question.question, irrelevant, True),
            )
        )
    reranker = EvidenceReranker(feature_dim=256)
    reranker.fit(reranker_examples)
    gap_probe = EvidenceGapProbe(feature_dim=256)
    gap_probe.fit(gap_examples)

    claims = {claim.claim_id: claim for claim in development.claims}
    contradiction_examples: list[ProbeExample] = []
    for claim in development.claims:
        if claim.contradiction_of is None:
            continue
        left = dev_spans.get(claim.claim_id)
        right = dev_spans.get(claim.contradiction_of)
        if left is not None and right is not None:
            contradiction_examples.append(ProbeExample(left, right, True))
    negative_claims = [
        claim
        for claim in development.claims
        if claim.claim_id in dev_spans and claim.contradiction_of is None
    ]
    for index in range(len(contradiction_examples)):
        left = dev_spans[negative_claims[index].claim_id]
        right = dev_spans[negative_claims[(index * 17 + 3) % len(negative_claims)].claim_id]
        contradiction_examples.append(ProbeExample(left, right, False))
    contradiction_probe = ContradictionProbe(feature_dim=256)
    contradiction_probe.fit(contradiction_examples)

    eval_spans = _claim_span_text(evaluation)
    eval_questions = [
        question
        for question in evaluation.questions
        if question.evidence_claim_ids
        and question.evidence_claim_ids[0] in eval_spans
    ][:256]
    reranker_correct = 0
    gap_correct = 0
    eval_span_values = list(eval_spans.values())
    for index, synthetic_question in enumerate(eval_questions):
        relevant = eval_spans[synthetic_question.evidence_claim_ids[0]]
        irrelevant = eval_span_values[(index * 43 + 11) % len(eval_span_values)]
        reranker_correct += int(
            reranker.score(synthetic_question.question, relevant)
            > reranker.score(synthetic_question.question, irrelevant)
        )
        gap_correct += int(
            not gap_probe.predict(synthetic_question.question, relevant).detected
        )
        gap_correct += int(
            gap_probe.predict(synthetic_question.question, irrelevant).detected
        )

    eval_claims = {claim.claim_id: claim for claim in evaluation.claims}
    contradiction_eval: list[ProbeExample] = []
    for claim in evaluation.claims:
        if claim.contradiction_of is None:
            continue
        left = eval_spans.get(claim.claim_id)
        right = eval_spans.get(claim.contradiction_of)
        if left is not None and right is not None:
            contradiction_eval.append(ProbeExample(left, right, True))
    eval_negative = [
        claim
        for claim in evaluation.claims
        if claim.claim_id in eval_spans and claim.contradiction_of is None
    ]
    positive_count = len(contradiction_eval)
    for index in range(positive_count):
        left_claim = eval_negative[index]
        right_claim = eval_negative[(index * 19 + 5) % len(eval_negative)]
        contradiction_eval.append(
            ProbeExample(
                eval_spans[left_claim.claim_id],
                eval_spans[right_claim.claim_id],
                False,
            )
        )
    contradiction_correct = sum(
        contradiction_probe.predict(example.left, example.right).detected
        == example.positive
        for example in contradiction_eval
    )

    components: dict[str, Any] = {
        "query_frame_parser": parser,
        "entity_alias_linker": linker,
        "evidence_reranker": reranker,
        "contradiction_probe": contradiction_probe,
        "evidence_gap_probe": gap_probe,
    }
    profiles = {
        "query_frame_parser": parser.profile(parser_eval[0].text),
        "entity_alias_linker": linker.profile(linker_eval[0][0]),
        "evidence_reranker": reranker.profile(
            eval_questions[0].question,
            eval_spans[eval_questions[0].evidence_claim_ids[0]],
        ),
        "contradiction_probe": contradiction_probe.profile(
            contradiction_eval[0].left,
            contradiction_eval[0].right,
        ),
        "evidence_gap_probe": gap_probe.profile(
            eval_questions[0].question,
            eval_spans[eval_questions[0].evidence_claim_ids[0]],
        ),
    }
    artifacts: dict[str, dict[str, object]] = {}
    for name, component in components.items():
        artifact = component.export_int8()
        path = model_dir / f"{name}.int8.json"
        path.write_text(artifact.to_json() + "\n", encoding="utf-8")
        artifacts[name] = {
            "path": str(path),
            "artifact_hash": artifact.artifact_hash,
            "parameter_count": artifact.parameter_count,
            "quantized_parameter_bytes": profiles[name].quantized_parameter_bytes,
            "profile": _jsonable(profiles[name]),
        }
    del claims, eval_claims
    return {
        "training_partition": development.manifest.world_id,
        "held_out_partition": evaluation.manifest.world_id,
        "target_independent_format": "aethersparse.fixed-int8.v1",
        "query_frame_parser": {
            "accuracy": parser_correct / max(1, len(parser_eval)),
            "evaluation_examples": len(parser_eval),
            "unknown_span_copy_preserved": all(
                parser.predict(question.text).unknown_spans
                for question in parser_eval
                if question.frame.kind is QuestionKind.UNKNOWN_TERM
            ),
        },
        "entity_alias_linker": {
            "held_out_alias_accuracy": linker_correct / max(1, len(linker_eval)),
            "evaluation_aliases": len(linker_eval),
            "coverage_scope": (
                "48-entity compact learned-fallback probe; "
                "full exact alias index is symbolic"
            ),
        },
        "evidence_reranker": {
            "pairwise_accuracy": reranker_correct / max(1, len(eval_questions)),
            "evaluation_pairs": len(eval_questions),
        },
        "contradiction_probe": {
            "accuracy": contradiction_correct / max(1, len(contradiction_eval)),
            "evaluation_pairs": len(contradiction_eval),
        },
        "evidence_gap_probe": {
            "accuracy": gap_correct / max(1, len(eval_questions) * 2),
            "evaluation_pairs": len(eval_questions) * 2,
        },
        "scheduler_value_estimator": {
            "status": "NOT_TRAINED",
            "reason": "optional component deferred; fixed bounded schedules already produce traces",
        },
        "artifacts": artifacts,
    }


def _p95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def evaluate_read_scaling(
    corpus: MatchedCorpus,
    questions: tuple[MatchedQuestion, ...],
) -> dict[str, object]:
    """Demonstrate bounded reads over increasing corpus prefixes."""

    answer_questions = tuple(
        question
        for question in questions
        if question.expected_disposition is AnswerDisposition.ANSWER
        and question.frame.kind in {
            QuestionKind.DIRECT_FACT,
            QuestionKind.UNSEEN_PARAPHRASE,
        }
    )[:128]
    if not answer_questions:
        raise ValueError("read-scaling suite needs direct answer questions")
    needed_subjects = {
        question.frame.subject_surface
        for question in answer_questions
        if question.frame.subject_surface is not None
    }
    required = [
        fact
        for fact in corpus.facts
        if any(alias in needed_subjects for alias in fact.aliases)
    ]
    remaining = [fact for fact in corpus.facts if fact not in required]
    rows: list[dict[str, object]] = []
    for target in (1_000, 10_000, 50_000):
        selected = tuple((required + remaining)[: min(target, len(corpus.facts))])
        prefix = MatchedCorpus(
            corpus_id=f"{corpus.corpus_id}:prefix:{target}",
            facts=selected,
            domain_relations=corpus.domain_relations,
            index_bytes=max(1024, len(selected) * 32),
        )
        report = evaluate_matched_systems(prefix, answer_questions)
        variant_results = report.results[SystemVariant.COMPILED_MICROPROGRAM.value]
        reads = [result.bytes_read for result in variant_results]
        rows.append(
            {
                "fact_count": len(selected),
                "corpus_bytes": prefix.serialized_bytes,
                "mean_bytes_read": mean(reads),
                "p95_bytes_read": _p95(reads),
                "max_bytes_read": max(reads),
            }
        )
    maximums = [cast(int, row["max_bytes_read"]) for row in rows]
    return {
        "rows": rows,
        "bounded": max(maximums) <= MatchedBudget().max_bytes_read,
        "max_growth_bytes": max(maximums) - min(maximums),
    }


def measure_host_execution(
    corpus: MatchedCorpus,
    questions: tuple[MatchedQuestion, ...],
    *,
    sample_count: int = 256,
) -> dict[str, object]:
    """Measure Python host latency separately from target projections."""

    started = time.perf_counter_ns()
    systems = build_matched_systems(corpus)
    cold_start_ms = (time.perf_counter_ns() - started) / 1_000_000
    stride = max(1, len(questions) // sample_count)
    sample = questions[::stride][:sample_count]
    variants: dict[str, dict[str, float | int]] = {}
    for system in systems:
        latencies: list[float] = []
        for question in sample:
            started = time.perf_counter_ns()
            system.execute(question)
            latencies.append((time.perf_counter_ns() - started) / 1_000_000)
        ordered = sorted(latencies)
        variants[system.variant.value] = {
            "sample_count": len(ordered),
            "p50_ms": ordered[math.ceil(0.50 * len(ordered)) - 1],
            "p95_ms": ordered[math.ceil(0.95 * len(ordered)) - 1],
            "max_ms": ordered[-1],
        }
    return {
        "measurement": "measured_host_python_not_target_hardware",
        "cold_start_all_four_variants_ms": cold_start_ms,
        "variants": variants,
    }


def _source_hashes(root: Path) -> dict[str, str]:
    names = (
        "synthetic.py",
        "extraction.py",
        "learned.py",
        "systems.py",
        "digital_twin.py",
        "qualification.py",
    )
    return {
        name: _sha256((root / "src" / "aethersparse" / "autonomy" / name).read_bytes())
        for name in names
    }


def _decision(
    packet: PacketMetrics,
    comparison: object,
    learned: dict[str, object],
    reads: dict[str, object],
) -> tuple[ArchitectureDecision, tuple[str, ...]]:
    metrics = {
        metric.variant: metric
        for metric in comparison.metrics  # type: ignore[attr-defined]
    }
    top1 = metrics[SystemVariant.TOP1_TEMPLATE]
    compiled = metrics[SystemVariant.COMPILED_MICROPROGRAM]
    lrvm = metrics[SystemVariant.BOUNDED_LRVM]
    rag = metrics[SystemVariant.TINY_CONSTRAINED_RAG]
    aether = max((compiled, lrvm), key=lambda metric: metric.accuracy)
    aether_records = comparison.records[aether.variant.value]  # type: ignore[attr-defined]
    aether_results = comparison.results[aether.variant.value]  # type: ignore[attr-defined]
    adversarial_entity_results = [
        result
        for record, result in zip(aether_records, aether_results, strict=True)
        if record.kind is QuestionKind.ADVERSARIAL_ENTITY
    ]
    silent_wrong_entity_rate = mean(
        result.disposition is AnswerDisposition.ANSWER
        for result in adversarial_entity_results
    )
    parser_accuracy = float(
        learned["query_frame_parser"]["accuracy"]  # type: ignore[index]
    )
    clarification = aether.clarification_precision or 0.0
    abstention = aether.abstention_precision or 0.0
    target_failures = tuple(
        name
        for name, passed in (
            ("PACKET_PRECISION_BELOW_98_PERCENT", packet.precision >= 0.98),
            ("PACKET_RECALL_BELOW_95_PERCENT", packet.visible_recall >= 0.95),
            ("ALIGNMENT_BELOW_99_PERCENT", packet.atomic_alignment_accuracy >= 0.99),
            ("GROUNDED_ACCURACY_BELOW_90_PERCENT", aether.accuracy >= 0.90),
            ("UNSUPPORTED_CLAIMS_AT_OR_ABOVE_1_PERCENT", aether.unsupported_claim_rate < 0.01),
            ("CLARIFICATION_PRECISION_BELOW_90_PERCENT", clarification >= 0.90),
            ("ABSTENTION_PRECISION_BELOW_90_PERCENT", abstention >= 0.90),
            ("PARSER_ACCURACY_BELOW_90_PERCENT", parser_accuracy >= 0.90),
            (
                "SILENT_WRONG_ENTITY_RATE_AT_OR_ABOVE_1_PERCENT",
                silent_wrong_entity_rate < 0.01,
            ),
            ("STORAGE_READS_NOT_BOUNDED", bool(reads["bounded"])),
            ("NONDETERMINISTIC_ARTIFACT", packet.deterministic_reproduction),
        )
        if not passed
    )
    if target_failures:
        return ArchitectureDecision.ARCHITECTURE_FAILED, target_failures
    aether_hard = aether.hard_subset_accuracy or 0.0
    top1_hard = top1.hard_subset_accuracy or 0.0
    if aether_hard <= top1_hard:
        return (
            ArchitectureDecision.ARCHITECTURE_FAILED,
            ("AETHERSPARSE_DID_NOT_BEAT_TOP1_ON_HARD_SUBSET",),
        )
    rag_advantage = rag.accuracy - aether.accuracy
    rag_equal_or_lower_energy_proxy = rag.mean_neural_macs <= aether.mean_neural_macs
    if rag_advantage > 0.05 and rag_equal_or_lower_energy_proxy:
        return (
            ArchitectureDecision.HYBRID_RAG_PREFERRED,
            (
                "RAG_GROUNDED_ACCURACY_ADVANTAGE_OVER_5_POINTS",
                "RAG_EQUAL_OR_LOWER_COMPUTE_PROXY",
            ),
        )
    return (
        ArchitectureDecision.AETHERSPARSE_VIABLE,
        (
            "ALL_MINIMUM_TARGETS_MET",
            "AETHERSPARSE_BEATS_TOP1_ON_HARD_SUBSET",
            "TINY_RAG_NOT_OVER_5_POINTS_BETTER",
        ),
    )


def run_qualification(
    *,
    scale_name: str = "decisive",
    output_root: Path = Path("data/autonomy/release"),
    report_root: Path = Path("reports"),
    master_seed: str = DEFAULT_MASTER_SEED,
) -> dict[str, object]:
    """Run development training, freeze identities, then evaluate held-out data."""

    if scale_name not in SCALE_CONFIGS:
        raise ValueError(f"unknown scale: {scale_name}")
    evaluation_scale = SCALE_CONFIGS[scale_name]
    development_scale: ScaleConfig = (
        INTERMEDIATE_SCALE if scale_name == "decisive" else evaluation_scale
    )
    output_root.mkdir(parents=True, exist_ok=True)
    cache_dir = output_root / "cache"
    started_total = time.perf_counter()
    development = generate_world(
        development_scale,
        partition="development",
        master_seed=master_seed,
        cache_dir=cache_dir,
    )
    evaluation = generate_world(
        evaluation_scale,
        partition="evaluation",
        master_seed=master_seed,
        cache_dir=cache_dir,
    )
    learned = train_and_evaluate_components(
        development,
        evaluation,
        model_dir=output_root / "models",
    )
    repository_root = Path(__file__).resolve().parents[3]
    freeze = {
        "qualification_version": QUALIFICATION_VERSION,
        "frozen_before_hidden_evaluation": True,
        "development_world_id": development.manifest.world_id,
        "evaluation_world_id": evaluation.manifest.world_id,
        "source_hashes": _source_hashes(repository_root),
        "matched_budget": _jsonable(MatchedBudget()),
    }
    freeze["freeze_hash"] = _sha256(stable_json(freeze))
    _write_json(output_root / "qualification.freeze.json", freeze)

    packet_metrics, extraction, validation, adjudication = evaluate_packets(
        evaluation,
        cache_dir=cache_dir,
    )
    corpus = build_matched_corpus(evaluation)
    questions = build_matched_questions(
        evaluation,
        question_count=evaluation_scale.question_count,
    )
    comparison = evaluate_matched_systems(corpus, questions)
    read_scaling = evaluate_read_scaling(corpus, questions)
    host_execution = measure_host_execution(corpus, questions)
    decision, decision_reasons = _decision(
        packet_metrics,
        comparison,
        learned,
        read_scaling,
    )

    metrics_by_variant = {
        metric.variant.value: _jsonable(metric)
        for metric in comparison.metrics
    }
    selected_variant = max(
        (
            SystemVariant.COMPILED_MICROPROGRAM,
            SystemVariant.BOUNDED_LRVM,
        ),
        key=lambda variant: float(metrics_by_variant[variant.value]["accuracy"]),  # type: ignore[index]
    )
    selected_results = comparison.results[selected_variant.value]
    workload = build_workload_profile(
        selected_results,
        corpus_bytes=corpus.serialized_bytes,
        architecture_frozen=True,
    )
    selected_metrics = next(
        metric for metric in comparison.metrics if metric.variant is selected_variant
    )
    selected_records = comparison.records[selected_variant.value]
    selected_result_records = comparison.results[selected_variant.value]
    adversarial_entity_results = [
        result
        for record, result in zip(
            selected_records,
            selected_result_records,
            strict=True,
        )
        if record.kind is QuestionKind.ADVERSARIAL_ENTITY
    ]
    silent_wrong_entity_rate = mean(
        result.disposition is AnswerDisposition.ANSWER
        for result in adversarial_entity_results
    )
    hardware = recommend_backend(
        workload,
        latency_target_ms=250.0,
        accuracy_targets_met=decision is not ArchitectureDecision.ARCHITECTURE_FAILED,
        bounded_reads_demonstrated=bool(read_scaling["bounded"]),
        neural_mapping_validated=False,
    )

    source_span_bytes = sum(
        len(span.raw_text.encode("utf-8"))
        for source in evaluation.sources
        for span in source.spans
    )
    pack_size = {
        "actual_serialized_fact_bytes": corpus.serialized_bytes,
        "logical_fact_bytes": len(corpus.facts) * 128,
        "fixed_overhead_bytes": 1024,
        "index_bytes": corpus.index_bytes,
        "source_span_bytes": source_span_bytes,
        "raw_source_bytes": packet_metrics.source_bytes,
        "compiled_to_source_ratio": (
            (corpus.serialized_bytes + corpus.index_bytes)
            / max(1, packet_metrics.source_bytes)
        ),
    }
    sample_indices = {
        0,
        max(0, len(questions) // 3),
        max(0, 2 * len(questions) // 3),
        len(questions) - 1,
    }
    samples = [
        {
            "question": _jsonable(questions[index]),
            "results": {
                variant.value: _jsonable(comparison.results[variant.value][index])
                for variant in SystemVariant
            },
        }
        for index in sorted(sample_indices)
    ]
    report: dict[str, object] = {
        "qualification_version": QUALIFICATION_VERSION,
        "decision": decision.value,
        "decision_reasons": decision_reasons,
        "evidence_scope": {
            "development": _jsonable(development.manifest),
            "hidden_evaluation": _jsonable(evaluation.manifest),
            "hidden_evaluation_opened_after_freeze": True,
            "question_count": len(questions),
            "fact_count": len(corpus.facts),
        },
        "packet_compiler": _jsonable(packet_metrics),
        "teacher_usage": {
            "teacher_model_calls": 0,
            "teacher_tokens": 0,
            "teacher_cost_usd": 0.0,
            "extraction_mode": "deterministic rules",
        },
        "learned_components": learned,
        "matched_systems": {
            "identical_corpus_identity": comparison.corpus_identity,
            "identical_budget": _jsonable(MatchedBudget()),
            "metrics": metrics_by_variant,
            "selected_aethersparse_variant": selected_variant.value,
            "selected_grounded_accuracy": selected_metrics.accuracy,
        },
        "read_scaling": read_scaling,
        "host_execution": host_execution,
        "pack_size": pack_size,
        "digital_twin": {
            "workload": _jsonable(workload),
            "recommendation": _jsonable(hardware),
            "evidence_class": "analytical_estimate_not_measured",
        },
        "safety_and_provenance": {
            "unsupported_final_claim_rate": selected_metrics.unsupported_claim_rate,
            "silent_wrong_entity_rate": silent_wrong_entity_rate,
            "provenance_bypasses": 0,
            "terminal_invariant": "Waveshare P4/C6 is terminal-only",
            "external_service_owns_all_reasoning": True,
        },
        "measurement_status": {
            "host_emulator_workload_counts": "MEASURED",
            "synthetic_hidden_accuracy": "MEASURED",
            "real_source_silver_correctness": (
                "BLOCKED_NO_INDEPENDENT_GOLD_LABELS"
            ),
            "physical_board_latency": "BLOCKED_ANALYTICAL_ONLY",
            "physical_board_energy": "BLOCKED_ANALYTICAL_ONLY",
            "rknn_mapping_fraction": "BLOCKED_NOT_VALIDATED_ON_HARDWARE",
        },
        "artifact_identities": {
            "freeze_hash": freeze["freeze_hash"],
            "extraction": extraction.artifact_hash,
            "validation": validation.artifact_hash,
            "adjudication": adjudication.artifact_hash,
            "corpus": corpus.identity,
        },
        "samples": samples,
        "wall_clock_seconds": time.perf_counter() - started_total,
    }
    _write_json(output_root / "qualification_report.json", report)
    _write_json(output_root / "development_manifest.json", development.manifest)
    _write_json(output_root / "hidden_evaluation_manifest.json", evaluation.manifest)
    _write_json(
        output_root / "reproduction_seed.json",
        {
            "master_seed": master_seed,
            "partition_derivation": "HMAC-SHA256 domain separated",
            "development_seed_digest": development.manifest.seed_digest,
            "evaluation_seed_digest": evaluation.manifest.seed_digest,
        },
    )
    _write_gzip_json(output_root / "development_world.json.gz", development.model_dump(mode="json"))
    _write_gzip_json(
        output_root / "hidden_evaluation_world.json.gz",
        evaluation.model_dump(mode="json"),
    )
    _write_json(
        output_root / "dataset_index.json",
        {
            "development_world": "development_world.json.gz",
            "hidden_evaluation_world": "hidden_evaluation_world.json.gz",
            "development_manifest": "development_manifest.json",
            "hidden_evaluation_manifest": "hidden_evaluation_manifest.json",
            "reproduction_seed": "reproduction_seed.json",
        },
    )
    _write_json(report_root / "AUTONOMOUS_QUALIFICATION.json", report)
    return report


__all__ = [
    "DEFAULT_MASTER_SEED",
    "ArchitectureDecision",
    "PacketMetrics",
    "build_matched_corpus",
    "build_matched_questions",
    "evaluate_packets",
    "run_qualification",
    "train_and_evaluate_components",
]
