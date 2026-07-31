from __future__ import annotations

from aethersparse.autonomy.extraction import (
    AdjudicationDecision,
    IndependentAdjudicator,
    IndependentExtractor,
    IndependentValidator,
)
from aethersparse.autonomy.qualification import (
    build_matched_corpus,
    build_matched_questions,
)
from aethersparse.autonomy.synthetic import DEBUG_SCALE, generate_world
from aethersparse.autonomy.systems import (
    SystemVariant,
    evaluate_matched_systems,
)


def test_debug_hidden_compiler_meets_packet_targets() -> None:
    world = generate_world(
        DEBUG_SCALE,
        partition="evaluation",
        master_seed="qualification-regression",
    )
    extraction = IndependentExtractor(world.entities).extract_world(world)
    validation = IndependentValidator(world.entities).validate_world(world, extraction)
    adjudication = IndependentAdjudicator().adjudicate_world(
        world,
        extraction,
        validation,
    )
    visible = {
        span.claim_id
        for source in world.sources
        for span in source.spans
    }
    canonical = [
        result
        for result in adjudication.results
        if result.decision is AdjudicationDecision.CANONICAL
    ]
    recovered = {
        result.matched_claim_id
        for result in canonical
        if result.synthetic_truth_match
    }
    assert all(result.synthetic_truth_match for result in canonical)
    assert len(recovered & visible) / len(visible) >= 0.95


def test_compiled_system_beats_top1_on_structured_hard_subset() -> None:
    world = generate_world(
        DEBUG_SCALE,
        partition="evaluation",
        master_seed="systems-regression",
    )
    corpus = build_matched_corpus(world)
    questions = build_matched_questions(world, question_count=380)
    report = evaluate_matched_systems(corpus, questions)
    metrics = {metric.variant: metric for metric in report.metrics}
    assert (
        metrics[SystemVariant.COMPILED_MICROPROGRAM].hard_subset_accuracy
        > metrics[SystemVariant.TOP1_TEMPLATE].hard_subset_accuracy
    )
    assert metrics[SystemVariant.COMPILED_MICROPROGRAM].unsupported_claim_rate == 0
