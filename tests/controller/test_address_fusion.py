from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from aethersparse.controller.address_fusion import (
    UNRESOLVED_ADDRESS,
    AddressChannel,
    AddressChannelOutput,
    AddressFusionModel,
    AddressFusionParameters,
    AddressLabelledExample,
    AddressProposal,
    AddressSubchannel,
    MentionHypothesis,
    PersistedAddressBeliefEnvelope,
    PersistedAddressUnionEnvelope,
    ReadinessDecision,
    ScoreTransform,
    VerifiedAddressSourceManifest,
    VerifiedMentionAlignmentManifest,
    VerifiedPreCapCaptureManifest,
    VerifiedTuningAddressQualification,
    address_subchannel_for_channel,
    assess_specialist_readiness,
    evaluate_address_fusion,
    fit_address_fusion,
    plan_successive_halving,
    select_temperature,
    union_address_channels,
)
from aethersparse.controller.semantic_address import canonical_entity_id


def _title(index: int) -> str:
    return f"Entity {index}"


def _id(index: int) -> str:
    return canonical_entity_id(_title(index))


def _sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _mention(*channels: AddressChannel) -> MentionHypothesis:
    return MentionHypothesis(
        hypothesis_id="mention:alpha",
        surface="Alpha",
        normalized_surface="alpha",
        char_start=0,
        char_end=5,
        proposal_channels=channels,
    )


def _proposal(
    mention: MentionHypothesis,
    entity: int,
    channel: AddressChannel,
    rank: int,
    score: float,
    **features: object,
) -> AddressProposal:
    return AddressProposal(
        mention=mention,
        entity_id=_id(entity),
        canonical_title=_title(entity),
        channel=channel,
        source_subchannel=address_subchannel_for_channel(channel),
        source_record_id=f"record:{channel.value}:{rank}",
        channel_pre_cap_rank=rank,
        raw_channel_score=score,
        score_transform=ScoreTransform.IDENTITY_0_1,
        channel_score=score,
        **features,
    )


def _output(
    mention: MentionHypothesis,
    channel: AddressChannel,
    proposals: tuple[AddressProposal, ...],
    *,
    unresolved: float = 0.0,
    generated: int | None = None,
    channel_cap: int | None = None,
    complete: bool | None = None,
) -> AddressChannelOutput:
    generated_count = len(proposals) if generated is None else generated
    capture_complete = generated_count == len(proposals) if complete is None else complete
    emitted_records = [
        {
            "source_record_id": item.source_record_id,
            "entity_id": item.entity_id,
            "canonical_title": item.canonical_title,
            "channel": item.channel.value,
            "channel_pre_cap_rank": item.channel_pre_cap_rank,
        }
        for item in sorted(proposals, key=lambda item: item.channel_pre_cap_rank)
    ]
    return AddressChannelOutput(
        mention=mention,
        channel=channel,
        proposals=proposals,
        generated_candidate_count=generated_count,
        emitted_candidate_count=len(proposals),
        channel_cap=channel_cap,
        complete_pre_cap_capture=capture_complete,
        source_artifact_sha256=hashlib.sha256(channel.value.encode()).hexdigest(),
        source_bundle_sha256=hashlib.sha256(b"test-source-bundle").hexdigest(),
        source_schema_version="aethersparse.test-channel-output.v1",
        emitted_records_sha256=_sha256(emitted_records),
        unresolved_probability_mass=unresolved,
    )


def _neutral_model() -> AddressFusionModel:
    return AddressFusionModel(
        AddressFusionParameters(
            candidate_weights=(0.0,) * 16,
            unresolved_bias=0.0,
            unresolved_mass_weight=1.0,
            ambiguity_weight=0.0,
            disagreement_weight=0.0,
        )
    )


def test_union_combines_all_channels_before_global_cap_and_preserves_provenance() -> None:
    mention = _mention(AddressChannel.EXACT_TITLE, AddressChannel.FUZZY, AddressChannel.SEMANTIC)
    exact = _output(
        mention,
        AddressChannel.EXACT_TITLE,
        (
            _proposal(
                mention,
                1,
                AddressChannel.EXACT_TITLE,
                1,
                0.9,
                exact_score=0.9,
                title_indicator=True,
            ),
            _proposal(mention, 2, AddressChannel.EXACT_TITLE, 2, 0.7, exact_score=0.7),
        ),
    )
    fuzzy = _output(
        mention,
        AddressChannel.FUZZY,
        (_proposal(mention, 3, AddressChannel.FUZZY, 1, 0.85, fuzzy_score=0.85),),
    )
    semantic = _output(
        mention,
        AddressChannel.SEMANTIC,
        (
            _proposal(
                mention,
                3,
                AddressChannel.SEMANTIC,
                1,
                0.8,
                semantic_score=0.8,
                relation_score=0.6,
            ),
        ),
        unresolved=0.2,
    )

    union = union_address_channels((exact, fuzzy, semantic), cap=2)

    assert union.pre_cap_candidate_count == 3
    assert tuple(item.entity_id for item in union.candidates) == (_id(1), _id(3))
    assert tuple(item.entity_id for item in union.pruned_candidates) == (_id(2),)
    assert union.pruned_candidates[0].global_pre_cap_rank == 3
    assert union.candidates[1].channels == (AddressChannel.FUZZY, AddressChannel.SEMANTIC)
    assert union.candidates[1].fuzzy_score == 0.85
    assert union.candidates[1].semantic_score == 0.8
    semantic_provenance = union.candidates[1].provenance[1]
    assert semantic_provenance.generated_candidate_count == 1
    assert semantic_provenance.emitted_candidate_count == 1
    assert semantic_provenance.complete_pre_cap_capture is True
    assert semantic_provenance.source_schema_version == ("aethersparse.test-channel-output.v1")
    assert tuple(item.channel for item in union.channel_captures) == (
        AddressChannel.EXACT_TITLE,
        AddressChannel.FUZZY,
        AddressChannel.SEMANTIC,
    )
    assert union.all_channels_complete_pre_cap is True
    assert union.unresolved_probability_mass == 0.2
    assert union.channel_disagreement == pytest.approx(1.0 / 3.0)


def test_union_is_deterministic_under_channel_order_and_cap_saturation() -> None:
    mention = _mention(AddressChannel.ALIAS, AddressChannel.ANCHOR_PRIOR)
    alias = _output(
        mention,
        AddressChannel.ALIAS,
        tuple(
            _proposal(mention, index, AddressChannel.ALIAS, index, 0.5, alias_indicator=True)
            for index in range(1, 6)
        ),
    )
    anchor = _output(
        mention,
        AddressChannel.ANCHOR_PRIOR,
        (
            _proposal(
                mention,
                5,
                AddressChannel.ANCHOR_PRIOR,
                1,
                0.5,
                anchor_prior=0.75,
                support_count=10,
                source_document_count=4,
                source_diversity=0.4,
            ),
        ),
    )
    forward = union_address_channels((alias, anchor), cap=3)
    reverse = union_address_channels((anchor, alias), cap=3)

    assert forward == reverse
    assert tuple(item.entity_id for item in forward.candidates) == (_id(5), _id(1), _id(2))
    assert tuple(item.global_pre_cap_rank for item in forward.pruned_candidates) == (4, 5)
    assert forward.pruned_candidates[0].provenance[0].source_record_id == "record:alias:3"
    assert forward.pruned_candidates[0].provenance[0].channel_pre_cap_rank == 3

    incomplete_sidecar = forward.model_dump(mode="python")
    incomplete_sidecar["pruned_candidates"] = incomplete_sidecar["pruned_candidates"][:-1]
    incomplete_sidecar["pre_cap_candidate_count"] = 4
    with pytest.raises(ValidationError, match="every emitted channel record"):
        type(forward).model_validate(incomplete_sidecar)


def test_union_merges_independent_mention_detection_provenance() -> None:
    exact_mention = _mention(AddressChannel.EXACT_TITLE)
    fuzzy_mention = _mention(AddressChannel.FUZZY)
    union = union_address_channels(
        (
            _output(
                exact_mention,
                AddressChannel.ALIAS,
                (_proposal(exact_mention, 1, AddressChannel.ALIAS, 1, 0.8),),
            ),
            _output(
                fuzzy_mention,
                AddressChannel.SEMANTIC,
                (_proposal(fuzzy_mention, 2, AddressChannel.SEMANTIC, 1, 0.7),),
            ),
        ),
        cap=8,
    )

    assert union.mention_hypothesis.proposal_channels == (
        AddressChannel.EXACT_TITLE,
        AddressChannel.FUZZY,
    )


def test_union_rejects_conflicting_canonical_titles_and_noncanonical_ids() -> None:
    mention = _mention(AddressChannel.ALIAS, AddressChannel.SEMANTIC)
    alias = _proposal(mention, 1, AddressChannel.ALIAS, 1, 0.5)
    semantic = _proposal(mention, 1, AddressChannel.SEMANTIC, 1, 0.5).model_copy(
        update={"canonical_title": " ENTITY   1 "}
    )
    with pytest.raises(ValueError, match="conflicting titles"):
        union_address_channels(
            (
                _output(mention, AddressChannel.ALIAS, (alias,)),
                _output(mention, AddressChannel.SEMANTIC, (semantic,)),
            ),
            cap=8,
        )
    with pytest.raises(ValidationError, match="syntactically valid canonical"):
        AddressProposal(
            mention=mention,
            entity_id="semantic-vector:42",
            canonical_title="Approximate",
            channel=AddressChannel.SEMANTIC,
            source_subchannel=AddressSubchannel.SEMANTIC_ANN,
            source_record_id="semantic:42",
            channel_pre_cap_rank=1,
            raw_channel_score=0.5,
            score_transform=ScoreTransform.IDENTITY_0_1,
            channel_score=0.5,
        )


@pytest.mark.parametrize(
    ("channel", "subchannel"),
    (
        (AddressChannel.EXACT_TITLE, AddressSubchannel.CANONICAL_TITLE),
        (AddressChannel.ALIAS, AddressSubchannel.ALIAS),
        (AddressChannel.REDIRECT, AddressSubchannel.REDIRECT),
        (AddressChannel.ANCHOR_PRIOR, AddressSubchannel.ANCHOR_OCCURRENCE),
        (AddressChannel.FUZZY, AddressSubchannel.FUZZY_TITLE),
    ),
)
def test_generation_channels_have_one_explicit_source_subchannel(
    channel: AddressChannel, subchannel: AddressSubchannel
) -> None:
    assert address_subchannel_for_channel(channel) is subchannel
    mention = _mention(channel)
    proposal = _proposal(mention, 1, channel, 1, 0.5)
    forged = proposal.model_dump(mode="python")
    forged["source_subchannel"] = AddressSubchannel.SEMANTIC_ANN
    with pytest.raises(ValidationError, match="does not match"):
        AddressProposal.model_validate(forged)


@pytest.mark.parametrize(
    ("raw", "transform", "bounded"),
    (
        (0.25, ScoreTransform.IDENTITY_0_1, 0.25),
        (2.0, ScoreTransform.CLAMP_0_1, 1.0),
        (-0.5, ScoreTransform.COSINE_MINUS1_1, 0.25),
        (0.0, ScoreTransform.LOGISTIC, 0.5),
    ),
)
def test_raw_and_bounded_channel_scores_have_an_explicit_transform(
    raw: float, transform: ScoreTransform, bounded: float
) -> None:
    mention = _mention(AddressChannel.FUZZY)
    values = _proposal(mention, 1, AddressChannel.FUZZY, 1, 0.5).model_dump(mode="python")
    values.update(
        raw_channel_score=raw,
        score_transform=transform,
        channel_score=bounded,
    )
    proposal = AddressProposal.model_validate(values)
    assert proposal.channel_score == bounded

    values["channel_score"] = 0.4
    with pytest.raises(ValidationError, match="raw-score transform"):
        AddressProposal.model_validate(values)
    with pytest.raises(ValidationError, match="authoritative canonical title"):
        AddressProposal(
            mention=mention,
            entity_id=_id(1),
            canonical_title="Wrong authoritative title",
            channel=AddressChannel.ALIAS,
            source_subchannel=AddressSubchannel.ALIAS,
            source_record_id="alias:1",
            channel_pre_cap_rank=1,
            raw_channel_score=0.5,
            score_transform=ScoreTransform.IDENTITY_0_1,
            channel_score=0.5,
        )


def test_channel_capture_records_cap_completeness_and_zero_output_source() -> None:
    mention = _mention(AddressChannel.FUZZY, AddressChannel.SEMANTIC)
    fuzzy = _output(
        mention,
        AddressChannel.FUZZY,
        (_proposal(mention, 1, AddressChannel.FUZZY, 1, 0.8),),
        generated=3,
        channel_cap=1,
        complete=False,
    )
    semantic = _output(mention, AddressChannel.SEMANTIC, (), unresolved=0.25)
    union = union_address_channels((fuzzy, semantic), cap=8)

    assert union.all_channels_complete_pre_cap is False
    fuzzy_capture = next(
        item for item in union.channel_captures if item.channel is AddressChannel.FUZZY
    )
    assert fuzzy_capture.generated_candidate_count == 3
    assert fuzzy_capture.emitted_candidate_count == 1
    assert fuzzy_capture.channel_cap == 1
    semantic_capture = next(
        item for item in union.channel_captures if item.channel is AddressChannel.SEMANTIC
    )
    assert semantic_capture.generated_candidate_count == 0
    assert semantic_capture.source_artifact_sha256 == hashlib.sha256(b"semantic").hexdigest()
    assert (
        semantic_capture.source_bundle_sha256 == hashlib.sha256(b"test-source-bundle").hexdigest()
    )

    invalid = fuzzy.model_dump(mode="python")
    invalid["complete_pre_cap_capture"] = True
    with pytest.raises(ValidationError, match="completeness disagrees"):
        AddressChannelOutput.model_validate(invalid)
    invalid_hash = fuzzy.model_dump(mode="python")
    invalid_hash["emitted_records_sha256"] = "f" * 64
    with pytest.raises(ValidationError, match="emitted-record SHA-256"):
        AddressChannelOutput.model_validate(invalid_hash)


def test_union_rejects_overlapping_boundary_and_forged_channel_provenance() -> None:
    mention = _mention(AddressChannel.EXACT_TITLE)
    output = _output(
        mention,
        AddressChannel.EXACT_TITLE,
        (_proposal(mention, 1, AddressChannel.EXACT_TITLE, 1, 0.9),),
    )
    union = union_address_channels((output,), cap=8)
    overlapping = union.model_dump(mode="python")
    overlapping["pre_cap_candidate_count"] = 2
    pruned = overlapping["candidates"][0].copy()
    pruned["global_pre_cap_rank"] = 2
    overlapping["pruned_candidates"] = [pruned]
    with pytest.raises(ValidationError, match="must be disjoint"):
        type(union).model_validate(overlapping)

    forged = union.model_dump(mode="python")
    forged["candidates"][0]["provenance"][0]["source_artifact_sha256"] = "f" * 64
    with pytest.raises(ValidationError, match="disagrees with channel capture"):
        type(union).model_validate(forged)


def _example(
    case: str,
    partition: str,
    correct: int,
    scores: tuple[float, float],
    *,
    include_correct: bool = True,
) -> AddressLabelledExample:
    mention = _mention(AddressChannel.EXACT_TITLE)
    proposals = (
        _proposal(mention, 1, AddressChannel.EXACT_TITLE, 1, scores[0], exact_score=scores[0]),
        _proposal(mention, 2, AddressChannel.EXACT_TITLE, 2, scores[1], exact_score=scores[1]),
    )
    if not include_correct:
        correct = 3
    return AddressLabelledExample(
        case_id=case,
        partition=partition,
        corpus_tier="10k",
        training_eligible=True,
        channel_outputs=(_output(mention, AddressChannel.EXACT_TITLE, proposals),),
        correct_entity_ids=(_id(correct),),
    )


def test_development_fit_and_tuning_temperature_preserve_split_boundary() -> None:
    development = (
        _example("case:1", "development", 1, (0.95, 0.05)),
        _example("case:2", "development", 2, (0.05, 0.95)),
        _example("case:3", "development", 3, (0.6, 0.4), include_correct=False),
    )
    tuning = (
        _example("case:4", "tuning", 1, (0.9, 0.1)),
        _example("case:5", "tuning", 2, (0.1, 0.9)),
    )
    fitted = fit_address_fusion(development, iterations=50)
    calibrated = select_temperature(tuning, fitted)

    belief = AddressFusionModel(calibrated).predict(
        union_address_channels(tuning[0].channel_outputs, cap=64)
    )
    assert calibrated.temperature_selected_on == "tuning"
    assert calibrated.candidate_weights == fitted.candidate_weights
    assert UNRESOLVED_ADDRESS in belief.distribution.labels
    assert sum(belief.distribution.probabilities) == pytest.approx(1.0)
    with pytest.raises(ValueError, match="development"):
        fit_address_fusion(tuning)
    crossing = (_example("case:1", "tuning", 1, (0.9, 0.1)),)
    with pytest.raises(ValueError, match="cross development/tuning"):
        select_temperature(crossing, fitted)
    with pytest.raises(ValidationError, match="sealed partitions"):
        _example("sealed", "evaluation", 1, (0.9, 0.1))


def test_persisted_belief_envelope_is_versioned_and_content_addressed() -> None:
    example = _example("case:wire", "tuning", 1, (0.9, 0.1))
    union = union_address_channels(example.channel_outputs, cap=8)
    belief = _neutral_model().predict(union)
    union_envelope = PersistedAddressUnionEnvelope(
        union=union,
        union_sha256=_sha256(union.model_dump(mode="json")),
        source_manifest_sha256="a" * 64,
        architecture_sha256="c" * 64,
    )
    belief_sha256 = _sha256(belief.model_dump(mode="json"))
    envelope = PersistedAddressBeliefEnvelope(
        belief=belief,
        belief_sha256=belief_sha256,
        source_manifest_sha256="a" * 64,
        fusion_parameters_sha256="b" * 64,
        architecture_sha256="c" * 64,
    )

    assert envelope.schema_version == "aethersparse.address-belief-envelope.v12"
    assert union_envelope.schema_version == "aethersparse.address-union-envelope.v12"
    assert envelope.belief.union.schema_version == ("aethersparse.address-candidate-union.v12")
    forged = envelope.model_dump(mode="python")
    forged["belief_sha256"] = "d" * 64
    with pytest.raises(ValidationError, match="belief SHA-256 mismatch"):
        PersistedAddressBeliefEnvelope.model_validate(forged)
    forged_union = union_envelope.model_dump(mode="python")
    forged_union["union_sha256"] = "d" * 64
    with pytest.raises(ValidationError, match="union SHA-256 mismatch"):
        PersistedAddressUnionEnvelope.model_validate(forged_union)


def test_k_evaluation_measures_generation_separately_from_ranking() -> None:
    mention = _mention(AddressChannel.ALIAS)
    proposals = tuple(
        _proposal(
            mention,
            index,
            AddressChannel.ALIAS,
            index,
            1.0 - index / 20.0,
            alias_indicator=True,
        )
        for index in range(1, 11)
    )
    example = AddressLabelledExample(
        case_id="case:k",
        partition="tuning",
        corpus_tier="397k",
        training_eligible=True,
        channel_outputs=(_output(mention, AddressChannel.ALIAS, proposals),),
        correct_entity_ids=(_id(9),),
    )
    result = evaluate_address_fusion((example,), _neutral_model(), partition="tuning")
    by_k = {item.k: item for item in result.k_recall}

    assert by_k[8].entity_recall == 0.0
    assert by_k[8].multi_entity_completeness == 0.0
    assert by_k[16].entity_recall == 1.0
    assert by_k[32].multi_entity_completeness == 1.0
    assert len(result.address_selective_risk_coverage) == 5
    assert 0.0 <= result.availability_state_expected_calibration_error <= 1.0


def _single_output_example(
    case_id: str, *, candidate_entity: int | None, correct_entity: int
) -> AddressLabelledExample:
    mention = _mention(AddressChannel.SEMANTIC)
    proposals = (
        ()
        if candidate_entity is None
        else (
            _proposal(
                mention,
                candidate_entity,
                AddressChannel.SEMANTIC,
                1,
                0.8,
                semantic_score=0.8,
            ),
        )
    )
    return AddressLabelledExample(
        case_id=case_id,
        partition="tuning",
        corpus_tier="10k",
        training_eligible=True,
        channel_outputs=(
            _output(
                mention,
                AddressChannel.SEMANTIC,
                proposals,
                unresolved=1.0 if candidate_entity is None else 0.0,
            ),
        ),
        correct_entity_ids=(_id(correct_entity),),
    )


def test_generation_failure_is_not_a_zero_risk_covered_address() -> None:
    missing = _single_output_example("case:missing", candidate_entity=None, correct_entity=1)
    result = evaluate_address_fusion((missing,), _neutral_model(), partition="tuning")

    assert result.entity_top1_accuracy == 0.0
    assert result.state_top1_accuracy == 1.0
    assert result.availability_state_negative_log_likelihood == 0.0
    assert result.availability_state_multiclass_brier == 0.0
    assert result.availability_state_expected_calibration_error == 0.0
    assert result.resolved_address_expected_calibration_error is None
    assert result.address_selective_risk_coverage[0].coverage == 0.0
    assert result.address_selective_risk_coverage[0].risk is None


def test_unresolved_predictions_reduce_address_coverage_denominator() -> None:
    examples = (
        _single_output_example("case:right", candidate_entity=1, correct_entity=1),
        _single_output_example("case:wrong", candidate_entity=1, correct_entity=2),
        _single_output_example("case:missing-1", candidate_entity=None, correct_entity=3),
        _single_output_example("case:missing-2", candidate_entity=None, correct_entity=4),
    )
    result = evaluate_address_fusion(examples, _neutral_model(), partition="tuning")
    final = result.address_selective_risk_coverage[-1]

    assert final.coverage == 0.5
    assert final.risk == 0.5
    assert result.availability_calibration_scope == (
        "candidate_addresses_plus_unresolved_availability_state"
    )
    assert result.address_risk_scope == (
        "resolved_entity_predictions_only_unresolved_reduces_coverage"
    )


def test_specialist_readiness_blocks_current_v11_and_exposes_fixed_sweep_hook() -> None:
    blocked = assess_specialist_readiness(
        None,
        unavailable_reasons=("legacy_candidate_pool_proxy_is_not_generation_recall",),
    )
    assert blocked.decision is ReadinessDecision.ADDRESS_SUBSTRATE_INADEQUATE
    assert blocked.specialist_authorized is False
    assert "verified_tuning_address_qualification_unavailable" in blocked.blockers
    assert blocked.tuning_candidate_completeness is None
    assert plan_successive_halving(blocked).trials == ()

    mention = _mention(*tuple(AddressChannel))
    outputs = tuple(
        _output(
            mention,
            channel,
            (
                (_proposal(mention, 1, channel, 1, 1.0),)
                if channel is AddressChannel.EXACT_TITLE
                else ()
            ),
        )
        for channel in AddressChannel
    )
    example = AddressLabelledExample(
        case_id="case:ready",
        partition="tuning",
        corpus_tier="397k",
        training_eligible=True,
        channel_outputs=outputs,
        correct_entity_ids=(_id(1),),
    )
    qualification = evaluate_address_fusion((example,), _neutral_model(), partition="tuning")
    observed_channels = tuple(sorted(AddressChannel, key=lambda item: item.value))
    capture_manifest = VerifiedPreCapCaptureManifest(
        input_examples_sha256=qualification.input_examples_sha256,
        channel_capture_evidence_sha256=(qualification.channel_capture_evidence_sha256),
        channel_output_count=len(AddressChannel),
        observed_channels=observed_channels,
        verifier_sha256="a" * 64,
    )
    source_manifest = VerifiedAddressSourceManifest(
        input_examples_sha256=qualification.input_examples_sha256,
        channel_capture_evidence_sha256=(qualification.channel_capture_evidence_sha256),
        channel_output_count=len(AddressChannel),
        observed_channels=observed_channels,
        source_artifact_sha256s=qualification.source_artifact_sha256s,
        source_bundle_sha256s=qualification.source_bundle_sha256s,
        source_schema_versions=qualification.source_schema_versions,
        development_examples_sha256="0" * 64,
        lawful_development_example_count=100,
        verifier_sha256="f" * 64,
    )
    alignment_manifest = VerifiedMentionAlignmentManifest(
        input_examples_sha256=qualification.input_examples_sha256,
        case_ids_sha256=qualification.case_ids_sha256,
        mention_alignment_evidence_sha256=(qualification.mention_alignment_evidence_sha256),
        aligned_record_count=1,
        expected_record_count=1,
        verifier_sha256="b" * 64,
    )
    evidence = VerifiedTuningAddressQualification(
        qualification=qualification,
        qualification_sha256=_sha256(qualification.model_dump(mode="json")),
        source_manifest=source_manifest,
        source_manifest_sha256=_sha256(source_manifest.model_dump(mode="json")),
        pre_cap_capture_manifest=capture_manifest,
        pre_cap_capture_manifest_sha256=_sha256(capture_manifest.model_dump(mode="json")),
        mention_alignment_manifest=alignment_manifest,
        mention_alignment_manifest_sha256=_sha256(alignment_manifest.model_dump(mode="json")),
        architecture_sha256="d" * 64,
        evaluator_sha256="e" * 64,
    )
    ready = assess_specialist_readiness(evidence)
    plan = plan_successive_halving(ready)
    assert ready.decision is ReadinessDecision.CONTEXTUAL_SPECIALIST_JUSTIFIED
    assert ready.aspirational_recall_at_16_met is True
    assert plan.authorized is True
    assert tuple(item.parameter_count for item in plan.trials) == (
        250000,
        1000000,
        3000000,
        5000000,
    )

    forged = evidence.model_dump(mode="python")
    forged["qualification_sha256"] = "f" * 64
    with pytest.raises(ValidationError, match="qualification SHA-256 mismatch"):
        VerifiedTuningAddressQualification.model_validate(forged)

    wrong_alignment = evidence.model_dump(mode="python")
    wrong_alignment["mention_alignment_manifest"]["case_ids_sha256"] = "f" * 64
    wrong_alignment["mention_alignment_manifest_sha256"] = _sha256(
        wrong_alignment["mention_alignment_manifest"]
    )
    with pytest.raises(ValidationError, match="not bound to the tuning qualification"):
        VerifiedTuningAddressQualification.model_validate(wrong_alignment)

    wrong_source = evidence.model_dump(mode="python")
    wrong_source["source_manifest"]["source_bundle_sha256s"] = ["0" * 64]
    wrong_source["source_manifest_sha256"] = _sha256(wrong_source["source_manifest"])
    with pytest.raises(ValidationError, match="source manifest is not bound"):
        VerifiedTuningAddressQualification.model_validate(wrong_source)
