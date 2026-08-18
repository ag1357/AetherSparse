from __future__ import annotations

import hashlib
import json

from aethersparse.controller.address_fusion import (
    AddressChannel,
    AddressChannelOutput,
    AddressFusionModel,
    AddressFusionParameters,
    AddressProposal,
    MentionHypothesis,
    ScoreTransform,
    address_subchannel_for_channel,
    union_address_channels,
)
from aethersparse.controller.semantic_address import canonical_entity_id
from aethersparse.observer.address_fusion import address_belief_telemetry


def test_address_belief_reuses_training_only_observer_contract() -> None:
    mention = MentionHypothesis(
        hypothesis_id="mention:alpha",
        surface="Alpha",
        normalized_surface="alpha",
        char_start=0,
        char_end=5,
        proposal_channels=(AddressChannel.EXACT_TITLE,),
    )
    proposal = AddressProposal(
        mention=mention,
        entity_id=canonical_entity_id("Alpha"),
        canonical_title="Alpha",
        channel=AddressChannel.EXACT_TITLE,
        source_subchannel=address_subchannel_for_channel(AddressChannel.EXACT_TITLE),
        source_record_id="exact-title:alpha",
        channel_pre_cap_rank=1,
        raw_channel_score=1.0,
        score_transform=ScoreTransform.IDENTITY_0_1,
        channel_score=1.0,
        exact_score=1.0,
        title_indicator=True,
    )
    emitted_records = [
        {
            "source_record_id": proposal.source_record_id,
            "entity_id": proposal.entity_id,
            "canonical_title": proposal.canonical_title,
            "channel": proposal.channel.value,
            "channel_pre_cap_rank": proposal.channel_pre_cap_rank,
        }
    ]
    emitted_records_sha256 = hashlib.sha256(
        json.dumps(emitted_records, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    union = union_address_channels(
        (
            AddressChannelOutput(
                mention=mention,
                channel=AddressChannel.EXACT_TITLE,
                proposals=(proposal,),
                generated_candidate_count=1,
                emitted_candidate_count=1,
                complete_pre_cap_capture=True,
                source_artifact_sha256="a" * 64,
                source_bundle_sha256="b" * 64,
                source_schema_version="aethersparse.test-channel-output.v1",
                emitted_records_sha256=emitted_records_sha256,
                unresolved_probability_mass=0.1,
            ),
        ),
        cap=8,
    )
    model = AddressFusionModel(
        AddressFusionParameters(
            candidate_weights=(0.0,) * 16,
            unresolved_bias=0.0,
            unresolved_mass_weight=1.0,
            ambiguity_weight=0.0,
            disagreement_weight=0.0,
        )
    )
    telemetry = address_belief_telemetry(model.predict(union))

    assert telemetry.module_id == "semantic-address-v2.fusion"
    assert telemetry.active is True
    assert len(telemetry.output_distribution) == 2
    assert sum(item.probability for item in telemetry.output_distribution) == 1.0
