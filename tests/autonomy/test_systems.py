from __future__ import annotations

from aethersparse.autonomy.systems import (
    AnswerDisposition,
    CompiledMicroprogramSystem,
    KnowledgeFact,
    MatchedBudget,
    MatchedCorpus,
    MatchedQuestion,
    QueryFrame,
    QuestionKind,
    SystemVariant,
    VerificationStatus,
    build_matched_systems,
    evaluate_matched_systems,
)


def _fact(
    fact_id: str,
    subject: str,
    relation: str,
    value: str,
    *,
    alias: str | None = None,
    lineage: str | None = None,
    valid_at: int | None = None,
    quantity: float | None = None,
    unit: str | None = None,
) -> KnowledgeFact:
    return KnowledgeFact(
        fact_id=fact_id,
        subject_id=subject,
        relation_id=relation,
        object_value=value,
        evidence_span_id=f"span_{fact_id}",
        evidence_text=f"{subject} {relation} {value}.",
        source_doc_id=f"doc_{fact_id}",
        source_family=f"family_{lineage or fact_id}",
        lineage_id=lineage or fact_id,
        aliases=(alias,) if alias else (),
        valid_at=valid_at,
        quantity=quantity,
        unit=unit,
    )


def _corpus(*, padding: int = 0) -> MatchedCorpus:
    facts = [
        _fact("direct", "nova", "capital", "Lumen", alias="Nova"),
        _fact("temporal_1", "nova", "milestone", "Prototype", valid_at=2020),
        _fact("temporal_2", "nova", "milestone", "Release", valid_at=2022),
        _fact(
            "quantity",
            "tower",
            "height",
            "2 m",
            alias="Tower",
            quantity=2.0,
            unit="m",
        ),
        _fact("cause_1", "heat", "causes", "expansion", alias="Heat"),
        _fact("cause_2", "expansion", "causes", "alarm"),
        _fact("quote", "quote_42", "spoken_by", "Ada", alias="the quotation"),
        _fact("ambiguous_1", "river_bank", "located_in", "north", alias="bank"),
        _fact("ambiguous_2", "finance_bank", "founded_in", "south", alias="bank"),
        _fact("conflict_1", "nova", "status", "open", lineage="origin_a"),
        _fact("conflict_2", "nova", "status", "closed", lineage="origin_b"),
        _fact("duplicate_1", "nova", "designation", "star", lineage="same_origin"),
        _fact("duplicate_2", "nova", "designation", "star", lineage="same_origin"),
        _fact("negated", "relay", "state", "not active", alias="Relay"),
    ]
    facts.extend(
        _fact(
            f"padding_{index}",
            f"irrelevant_{index}",
            "padding",
            str(index),
        )
        for index in range(padding)
    )
    return MatchedCorpus(
        corpus_id=f"matched_{padding}",
        facts=tuple(facts),
        domain_relations=(
            "capital",
            "milestone",
            "height",
            "causes",
            "spoken_by",
            "located_in",
            "founded_in",
            "status",
            "designation",
            "state",
            "missing",
        ),
        index_bytes=16_384,
    )


def _question(
    question_id: str,
    kind: QuestionKind,
    subject: str | None,
    relation: str | None,
    disposition: AnswerDisposition,
    value: str | None = None,
    **frame_kwargs: object,
) -> MatchedQuestion:
    return MatchedQuestion(
        question_id=question_id,
        text=f"Question {question_id} about {subject or 'it'}?",
        frame=QueryFrame(
            subject_surface=subject,
            relation_id=relation,
            kind=kind,
            **frame_kwargs,  # type: ignore[arg-type]
        ),
        expected_disposition=disposition,
        expected_value=value,
        hard_subset=kind
        in {
            QuestionKind.CAUSAL_MULTIHOP,
            QuestionKind.CONFLICTING_SOURCES,
            QuestionKind.WRONG_PREMISE,
        },
    )


def test_all_variants_share_corpus_budget_and_verifier() -> None:
    corpus = _corpus()
    budget = MatchedBudget(max_model_bytes=4096)
    systems = build_matched_systems(corpus, budget)

    results = tuple(
        system.execute(
            _question(
                "direct",
                QuestionKind.DIRECT_FACT,
                "Nova",
                "capital",
                AnswerDisposition.ANSWER,
                "Lumen",
            )
        )
        for system in systems
    )

    assert {result.corpus_identity for result in results} == {corpus.identity}
    assert {id(system.budget) for system in systems} == {id(budget)}
    assert all(result.value == "Lumen" for result in results)
    assert all(result.citation_span_ids == ("span_direct",) for result in results)
    assert all(
        result.trace.verification.status is VerificationStatus.PASS
        for result in results
    )
    assert all(result.trace.operations for result in results)


def test_compiled_system_handles_temporal_quantity_and_multihop() -> None:
    system = CompiledMicroprogramSystem(_corpus(), MatchedBudget())
    latest = system.execute(
        _question(
            "latest",
            QuestionKind.TEMPORAL_ORDERING,
            "Nova",
            "milestone",
            AnswerDisposition.ANSWER,
            "Release",
            temporal_mode="latest",
        )
    )
    quantity = system.execute(
        _question(
            "quantity",
            QuestionKind.NUMERICAL_UNIT,
            "Tower",
            "height",
            AnswerDisposition.ANSWER,
            "200 cm",
            requested_unit="cm",
        )
    )
    causal = system.execute(
        _question(
            "causal",
            QuestionKind.CAUSAL_MULTIHOP,
            "Heat",
            "causes",
            AnswerDisposition.ANSWER,
            "alarm",
            path_relations=("causes", "causes"),
        )
    )

    assert latest.value == "Release"
    assert quantity.value == "200 cm"
    assert causal.value == "alarm"
    assert causal.trace.selected_evidence_ids == ("cause_1", "cause_2")
    assert causal.citation_span_ids == ("span_cause_1", "span_cause_2")


def test_fail_closed_cases_emit_no_claim_or_citation() -> None:
    system = CompiledMicroprogramSystem(_corpus(), MatchedBudget())
    cases = (
        _question(
            "ambiguous",
            QuestionKind.AMBIGUOUS_ENTITY,
            "bank",
            "located_in",
            AnswerDisposition.CLARIFY,
        ),
        _question(
            "premise",
            QuestionKind.WRONG_PREMISE,
            "Nova",
            "capital",
            AnswerDisposition.ABSTAIN,
            premise_object="Wrong",
        ),
        _question(
            "conflict",
            QuestionKind.CONFLICTING_SOURCES,
            "Nova",
            "status",
            AnswerDisposition.ABSTAIN,
        ),
        _question(
            "missing",
            QuestionKind.MISSING_EVIDENCE,
            "Nova",
            "missing",
            AnswerDisposition.ABSTAIN,
        ),
        _question(
            "unknown",
            QuestionKind.UNKNOWN_TERM,
            "unseen thing",
            "capital",
            AnswerDisposition.ABSTAIN,
        ),
        _question(
            "ood",
            QuestionKind.OUT_OF_DOMAIN,
            None,
            None,
            AnswerDisposition.OUT_OF_DOMAIN,
        ),
    )

    for case in cases:
        result = system.execute(case)
        assert result.disposition is case.expected_disposition
        assert result.value is None
        assert result.sentence is None
        assert result.citation_span_ids == ()
        assert result.failure_reason


def test_comparison_exposes_every_decisive_suite_hook() -> None:
    categories = tuple(QuestionKind)
    questions = tuple(
        _question(
            f"hook_{kind.value}",
            kind,
            None if kind is QuestionKind.OUT_OF_DOMAIN else "Nova",
            None if kind is QuestionKind.OUT_OF_DOMAIN else "capital",
            (
                AnswerDisposition.OUT_OF_DOMAIN
                if kind is QuestionKind.OUT_OF_DOMAIN
                else AnswerDisposition.ANSWER
            ),
            None if kind is QuestionKind.OUT_OF_DOMAIN else "Lumen",
        )
        for kind in categories
    )
    report = evaluate_matched_systems(_corpus(), questions)

    assert len(report.metrics) == 4
    assert {metric.variant for metric in report.metrics} == set(SystemVariant)
    for metric in report.metrics:
        assert set(metric.category_accuracy) == {kind.value for kind in categories}
        assert metric.unsupported_claim_rate == 0.0


def test_reasoning_beats_top1_on_matched_hard_subset() -> None:
    questions = (
        _question(
            "causal",
            QuestionKind.CAUSAL_MULTIHOP,
            "Heat",
            "causes",
            AnswerDisposition.ANSWER,
            "alarm",
            path_relations=("causes", "causes"),
        ),
        _question(
            "conflict",
            QuestionKind.CONFLICTING_SOURCES,
            "Nova",
            "status",
            AnswerDisposition.ABSTAIN,
        ),
    )
    report = evaluate_matched_systems(_corpus(), questions)
    by_variant = {metric.variant: metric for metric in report.metrics}

    assert (
        by_variant[SystemVariant.COMPILED_MICROPROGRAM].hard_subset_accuracy
        == 1.0
    )
    assert by_variant[SystemVariant.TOP1_TEMPLATE].hard_subset_accuracy == 0.0


def test_storage_reads_do_not_scale_with_irrelevant_corpus_facts() -> None:
    question = _question(
        "direct",
        QuestionKind.DIRECT_FACT,
        "Nova",
        "capital",
        AnswerDisposition.ANSWER,
        "Lumen",
    )
    small = CompiledMicroprogramSystem(_corpus(), MatchedBudget()).execute(question)
    large = CompiledMicroprogramSystem(_corpus(padding=1000), MatchedBudget()).execute(
        question
    )

    assert small.bytes_read == large.bytes_read
    assert sum(op.storage_reads for op in small.trace.operations) == sum(
        op.storage_reads for op in large.trace.operations
    )
