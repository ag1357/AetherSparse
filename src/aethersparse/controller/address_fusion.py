"""Canonical multi-channel address union, calibration, and qualification.

Candidate generation is deliberately separated from ranking.  Every channel
proposal is first unioned by authoritative canonical entity ID.  Only then is
the deterministic global cap applied.  Learned scores may reorder an already
generated set, but they can never mint an address or hide the provenance that
generated it.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from aethersparse.controller.models import (
    CORPUS_ENTITY_ID_PREFIX,
    FrozenModel,
)
from aethersparse.controller.semantic_address import canonical_entity_id, normalize_mention
from aethersparse.specialists.workspace import CategoricalBelief

UNRESOLVED_ADDRESS = "__unresolved__"
_EPSILON = 1e-12
_FEATURE_COUNT = 16
_ALLOWED_K = (8, 16, 32, 64)
_ENTITY_ID_PATTERN = re.compile(r"^as:(?:v050|user):entity:[0-9a-f]{24}$")


def _json_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _model_sha256(value: FrozenModel) -> str:
    return _json_sha256(value.model_dump(mode="json"))


def _valid_entity_id(value: str) -> bool:
    return _ENTITY_ID_PATTERN.fullmatch(value) is not None


def _validate_canonical_entity(entity_id: str, canonical_title: str) -> None:
    if not _valid_entity_id(entity_id):
        raise ValueError("address must use a syntactically valid canonical entity ID")
    if entity_id.startswith(CORPUS_ENTITY_ID_PREFIX) and entity_id != canonical_entity_id(
        canonical_title
    ):
        raise ValueError("corpus entity ID does not match its authoritative canonical title")


def _validate_capture_boundary(
    *,
    generated: int,
    emitted: int,
    cap: int | None,
    complete: bool,
) -> None:
    if generated < emitted:
        raise ValueError("generated candidate count cannot be below emitted count")
    expected_emitted = generated if cap is None else min(generated, cap)
    if emitted != expected_emitted:
        raise ValueError("emitted candidate count disagrees with the channel cap")
    if complete != (emitted == generated):
        raise ValueError("pre-cap completeness disagrees with generated and emitted counts")


class AddressChannel(StrEnum):
    """Independent proposal channels; approximate channels are never authoritative."""

    RETAINED = "retained"
    EXACT_TITLE = "exact_title"
    ALIAS = "alias"
    REDIRECT = "redirect"
    ANCHOR_PRIOR = "anchor_prior"
    FUZZY = "fuzzy"
    SEMANTIC = "semantic"


class AddressSubchannel(StrEnum):
    """Typed source semantics for every address generation channel."""

    RETAINED_CANDIDATE = "retained_candidate"
    CANONICAL_TITLE = "canonical_title"
    ALIAS = "alias"
    REDIRECT = "redirect"
    ANCHOR_OCCURRENCE = "anchor_occurrence"
    FUZZY_TITLE = "fuzzy_title"
    SEMANTIC_ANN = "semantic_ann"


_SUBCHANNEL_BY_CHANNEL = {
    AddressChannel.RETAINED: AddressSubchannel.RETAINED_CANDIDATE,
    AddressChannel.EXACT_TITLE: AddressSubchannel.CANONICAL_TITLE,
    AddressChannel.ALIAS: AddressSubchannel.ALIAS,
    AddressChannel.REDIRECT: AddressSubchannel.REDIRECT,
    AddressChannel.ANCHOR_PRIOR: AddressSubchannel.ANCHOR_OCCURRENCE,
    AddressChannel.FUZZY: AddressSubchannel.FUZZY_TITLE,
    AddressChannel.SEMANTIC: AddressSubchannel.SEMANTIC_ANN,
}


def address_subchannel_for_channel(channel: AddressChannel) -> AddressSubchannel:
    """Return the one lawful source subchannel for a generation channel."""

    return _SUBCHANNEL_BY_CHANNEL[channel]


class ScoreTransform(StrEnum):
    """Auditable transform from a source-native raw score to a bounded score."""

    IDENTITY_0_1 = "identity_0_1"
    CLAMP_0_1 = "clamp_0_1"
    COSINE_MINUS1_1 = "cosine_minus1_1_to_0_1"
    LOGISTIC = "logistic"


def _bounded_channel_score(raw: float, transform: ScoreTransform) -> float:
    if not math.isfinite(raw):
        raise ValueError("raw channel score must be finite")
    if transform is ScoreTransform.IDENTITY_0_1:
        if not 0.0 <= raw <= 1.0:
            raise ValueError("identity score transform requires raw score in [0,1]")
        return raw
    if transform is ScoreTransform.CLAMP_0_1:
        return min(1.0, max(0.0, raw))
    if transform is ScoreTransform.COSINE_MINUS1_1:
        if not -1.0 <= raw <= 1.0:
            raise ValueError("cosine score transform requires raw score in [-1,1]")
        return (raw + 1.0) / 2.0
    if raw >= 0.0:
        return 1.0 / (1.0 + math.exp(-raw))
    exponential = math.exp(raw)
    return exponential / (1.0 + exponential)


class ReadinessDecision(StrEnum):
    ADDRESS_SUBSTRATE_INADEQUATE = "ADDRESS_SUBSTRATE_INADEQUATE"
    ADDRESS_GENERATION_IMPROVED_BUT_NOT_READY = "ADDRESS_GENERATION_IMPROVED_BUT_NOT_READY"
    CONTEXTUAL_SPECIALIST_JUSTIFIED = "CONTEXTUAL_SPECIALIST_JUSTIFIED"


class MentionHypothesis(FrozenModel):
    """A copied mention proposal shared by one or more generation channels."""

    schema_version: Literal["aethersparse.mention-hypothesis.v12"] = (
        "aethersparse.mention-hypothesis.v12"
    )
    hypothesis_id: str
    surface: str
    normalized_surface: str
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    proposal_channels: tuple[AddressChannel, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_copy_and_normalization(self) -> MentionHypothesis:
        if self.char_end - self.char_start != len(self.surface):
            raise ValueError("mention bounds must cover the copied surface")
        if self.normalized_surface != normalize_mention(self.surface):
            raise ValueError("normalized mention does not match the copied surface")
        if len(set(self.proposal_channels)) != len(self.proposal_channels):
            raise ValueError("mention proposal channels must be unique")
        return self


class AddressProposal(FrozenModel):
    """One channel's pre-global-cap proposal for an exact canonical address."""

    schema_version: Literal["aethersparse.address-proposal.v12"] = (
        "aethersparse.address-proposal.v12"
    )
    mention: MentionHypothesis
    entity_id: str
    canonical_title: str
    channel: AddressChannel
    source_subchannel: AddressSubchannel
    source_record_id: str = Field(
        min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"
    )
    channel_pre_cap_rank: int = Field(ge=1)
    raw_channel_score: float
    score_transform: ScoreTransform
    channel_score: float = Field(ge=0.0, le=1.0)
    exact_score: float = Field(default=0.0, ge=0.0, le=1.0)
    fuzzy_score: float = Field(default=0.0, ge=0.0, le=1.0)
    semantic_score: float = Field(default=0.0, ge=0.0, le=1.0)
    anchor_prior: float = Field(default=0.0, ge=0.0, le=1.0)
    support_count: int = Field(default=0, ge=0)
    source_document_count: int = Field(default=0, ge=0)
    source_diversity: float = Field(default=0.0, ge=0.0, le=1.0)
    title_indicator: bool = False
    redirect_indicator: bool = False
    alias_indicator: bool = False
    type_score: float = Field(default=0.0, ge=0.0, le=1.0)
    relation_score: float = Field(default=0.0, ge=0.0, le=1.0)
    context_score: float = Field(default=0.0, ge=0.0, le=1.0)
    ambiguity_entropy_nats: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def validate_authoritative_address(self) -> AddressProposal:
        if not self.canonical_title.strip():
            raise ValueError("canonical title must be non-empty")
        _validate_canonical_entity(self.entity_id, self.canonical_title)
        if self.source_subchannel is not address_subchannel_for_channel(self.channel):
            raise ValueError("source subchannel does not match the generation channel")
        expected_score = _bounded_channel_score(self.raw_channel_score, self.score_transform)
        if not math.isclose(self.channel_score, expected_score, abs_tol=1e-12):
            raise ValueError("bounded channel score disagrees with its raw-score transform")
        if self.source_document_count > self.support_count:
            raise ValueError("source-document support cannot exceed occurrence support")
        return self


def _emitted_records_sha256(proposals: tuple[AddressProposal, ...]) -> str:
    records = [
        {
            "source_record_id": item.source_record_id,
            "entity_id": item.entity_id,
            "canonical_title": item.canonical_title,
            "channel": item.channel.value,
            "channel_pre_cap_rank": item.channel_pre_cap_rank,
        }
        for item in sorted(proposals, key=lambda item: item.channel_pre_cap_rank)
    ]
    return _json_sha256(records)


class AddressChannelOutput(FrozenModel):
    """Uncapped output of one generation channel for one mention hypothesis."""

    schema_version: Literal["aethersparse.address-channel-output.v12"] = (
        "aethersparse.address-channel-output.v12"
    )
    mention: MentionHypothesis
    channel: AddressChannel
    proposals: tuple[AddressProposal, ...] = ()
    generated_candidate_count: int = Field(ge=0)
    emitted_candidate_count: int = Field(ge=0)
    channel_cap: int | None = Field(default=None, ge=1, le=1_000_000)
    complete_pre_cap_capture: bool
    source_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_schema_version: str = Field(min_length=1, max_length=128)
    emitted_records_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    unresolved_probability_mass: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_channel_output(self) -> AddressChannelOutput:
        if self.emitted_candidate_count != len(self.proposals):
            raise ValueError("emitted candidate count must equal serialized proposals")
        _validate_capture_boundary(
            generated=self.generated_candidate_count,
            emitted=self.emitted_candidate_count,
            cap=self.channel_cap,
            complete=self.complete_pre_cap_capture,
        )
        ids: set[str] = set()
        ranks: set[int] = set()
        source_record_ids: set[str] = set()
        for proposal in self.proposals:
            if proposal.mention != self.mention or proposal.channel != self.channel:
                raise ValueError("channel output proposals must match their mention and channel")
            if proposal.entity_id in ids or proposal.channel_pre_cap_rank in ranks:
                raise ValueError("channel output contains a duplicate entity or pre-cap rank")
            if proposal.source_record_id in source_record_ids:
                raise ValueError("channel output contains a duplicate source record ID")
            ids.add(proposal.entity_id)
            ranks.add(proposal.channel_pre_cap_rank)
            source_record_ids.add(proposal.source_record_id)
            if proposal.channel_pre_cap_rank > self.generated_candidate_count:
                raise ValueError("proposal rank exceeds the generated candidate count")
        if ranks != set(range(1, self.emitted_candidate_count + 1)):
            raise ValueError("emitted channel ranks must be contiguous")
        if self.emitted_records_sha256 != _emitted_records_sha256(self.proposals):
            raise ValueError("emitted-record SHA-256 disagrees with serialized proposals")
        return self


class ChannelProvenance(FrozenModel):
    """Lossless channel-local evidence retained after canonical-ID union."""

    schema_version: Literal["aethersparse.channel-provenance.v12"] = (
        "aethersparse.channel-provenance.v12"
    )
    channel: AddressChannel
    source_subchannel: AddressSubchannel
    source_record_id: str = Field(
        min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"
    )
    channel_pre_cap_rank: int = Field(ge=1)
    raw_channel_score: float
    score_transform: ScoreTransform
    channel_score: float = Field(ge=0.0, le=1.0)
    exact_score: float = Field(ge=0.0, le=1.0)
    fuzzy_score: float = Field(ge=0.0, le=1.0)
    semantic_score: float = Field(ge=0.0, le=1.0)
    anchor_prior: float = Field(ge=0.0, le=1.0)
    support_count: int = Field(ge=0)
    source_document_count: int = Field(ge=0)
    source_diversity: float = Field(ge=0.0, le=1.0)
    title_indicator: bool
    redirect_indicator: bool
    alias_indicator: bool
    type_score: float = Field(ge=0.0, le=1.0)
    relation_score: float = Field(ge=0.0, le=1.0)
    context_score: float = Field(ge=0.0, le=1.0)
    ambiguity_entropy_nats: float = Field(ge=0.0)
    generated_candidate_count: int = Field(ge=0)
    emitted_candidate_count: int = Field(ge=0)
    channel_cap: int | None = Field(default=None, ge=1, le=1_000_000)
    complete_pre_cap_capture: bool
    source_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_schema_version: str = Field(min_length=1, max_length=128)
    emitted_records_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ChannelCapture(FrozenModel):
    """Channel-level provenance retained even when a channel emits no address."""

    schema_version: Literal["aethersparse.channel-capture.v12"] = "aethersparse.channel-capture.v12"
    channel: AddressChannel
    generated_candidate_count: int = Field(ge=0)
    emitted_candidate_count: int = Field(ge=0)
    channel_cap: int | None = Field(default=None, ge=1, le=1_000_000)
    complete_pre_cap_capture: bool
    source_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_schema_version: str = Field(min_length=1, max_length=128)
    emitted_records_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    unresolved_probability_mass: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_capture_boundary(self) -> ChannelCapture:
        _validate_capture_boundary(
            generated=self.generated_candidate_count,
            emitted=self.emitted_candidate_count,
            cap=self.channel_cap,
            complete=self.complete_pre_cap_capture,
        )
        return self


class CanonicalAddressCandidate(FrozenModel):
    """One authoritative address with every generation channel still inspectable."""

    schema_version: Literal["aethersparse.canonical-address-candidate.v12"] = (
        "aethersparse.canonical-address-candidate.v12"
    )
    mention_hypothesis: MentionHypothesis
    entity_id: str
    canonical_title: str
    global_pre_cap_rank: int = Field(ge=1)
    provenance: tuple[ChannelProvenance, ...] = Field(min_length=1)
    best_channel_score: float = Field(ge=0.0, le=1.0)
    exact_score: float = Field(ge=0.0, le=1.0)
    fuzzy_score: float = Field(ge=0.0, le=1.0)
    semantic_score: float = Field(ge=0.0, le=1.0)
    anchor_prior: float = Field(ge=0.0, le=1.0)
    support_count: int = Field(ge=0)
    source_document_count: int = Field(ge=0)
    source_diversity: float = Field(ge=0.0, le=1.0)
    title_indicator: bool
    redirect_indicator: bool
    alias_indicator: bool
    type_score: float = Field(ge=0.0, le=1.0)
    relation_score: float = Field(ge=0.0, le=1.0)
    context_score: float = Field(ge=0.0, le=1.0)
    ambiguity_entropy_nats: float = Field(ge=0.0)

    @property
    def channels(self) -> tuple[AddressChannel, ...]:
        return tuple(item.channel for item in self.provenance)

    @model_validator(mode="after")
    def validate_canonical_identity_and_provenance(self) -> CanonicalAddressCandidate:
        _validate_canonical_entity(self.entity_id, self.canonical_title)
        if len(set(self.channels)) != len(self.channels):
            raise ValueError("candidate provenance channels must be unique")
        for item in self.provenance:
            if item.source_subchannel is not address_subchannel_for_channel(item.channel):
                raise ValueError("candidate provenance subchannel disagrees with its channel")
            expected_score = _bounded_channel_score(item.raw_channel_score, item.score_transform)
            if not math.isclose(item.channel_score, expected_score, abs_tol=1e-12):
                raise ValueError("candidate provenance bounded score disagrees with raw score")
            if item.source_document_count > item.support_count:
                raise ValueError("candidate provenance document support exceeds occurrences")
        expected_numeric = {
            "best_channel_score": max(item.channel_score for item in self.provenance),
            "exact_score": max(item.exact_score for item in self.provenance),
            "fuzzy_score": max(item.fuzzy_score for item in self.provenance),
            "semantic_score": max(item.semantic_score for item in self.provenance),
            "anchor_prior": max(item.anchor_prior for item in self.provenance),
            "support_count": max(item.support_count for item in self.provenance),
            "source_document_count": max(item.source_document_count for item in self.provenance),
            "source_diversity": max(item.source_diversity for item in self.provenance),
            "type_score": max(item.type_score for item in self.provenance),
            "relation_score": max(item.relation_score for item in self.provenance),
            "context_score": max(item.context_score for item in self.provenance),
            "ambiguity_entropy_nats": max(item.ambiguity_entropy_nats for item in self.provenance),
        }
        if any(
            not math.isclose(float(getattr(self, field)), float(value), abs_tol=1e-12)
            for field, value in expected_numeric.items()
        ):
            raise ValueError("candidate aggregate scores disagree with channel provenance")
        expected_indicators = {
            "title_indicator": any(item.title_indicator for item in self.provenance),
            "redirect_indicator": any(item.redirect_indicator for item in self.provenance),
            "alias_indicator": any(item.alias_indicator for item in self.provenance),
        }
        if any(getattr(self, field) is not value for field, value in expected_indicators.items()):
            raise ValueError("candidate indicators disagree with channel provenance")
        return self


class AddressCandidateUnion(FrozenModel):
    """Post-union bounded candidates plus an auditable pre-cap boundary."""

    schema_version: Literal["aethersparse.address-candidate-union.v12"] = (
        "aethersparse.address-candidate-union.v12"
    )
    mention_hypothesis: MentionHypothesis
    cap: int = Field(ge=1, le=64)
    pre_cap_candidate_count: int = Field(ge=0)
    candidates: tuple[CanonicalAddressCandidate, ...]
    pruned_candidates: tuple[CanonicalAddressCandidate, ...]
    channel_captures: tuple[ChannelCapture, ...] = Field(min_length=1)
    all_channels_complete_pre_cap: bool
    unresolved_probability_mass: float = Field(ge=0.0, le=1.0)
    channel_disagreement: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_cap_boundary(self) -> AddressCandidateUnion:
        if len(self.candidates) > self.cap:
            raise ValueError("address candidates exceed the global cap")
        all_candidates = (*self.candidates, *self.pruned_candidates)
        if self.pre_cap_candidate_count != len(all_candidates):
            raise ValueError("pre-cap candidate count does not match retained and pruned sidecars")
        retained = {item.entity_id for item in self.candidates}
        pruned = {item.entity_id for item in self.pruned_candidates}
        if retained & pruned:
            raise ValueError("retained and pruned canonical entity IDs must be disjoint")
        if len({item.entity_id for item in all_candidates}) != len(all_candidates):
            raise ValueError("canonical union contains duplicate entity IDs")
        if any(item.mention_hypothesis != self.mention_hypothesis for item in all_candidates):
            raise ValueError("all candidates must match the union mention hypothesis")
        if tuple(item.global_pre_cap_rank for item in all_candidates) != tuple(
            range(1, self.pre_cap_candidate_count + 1)
        ):
            raise ValueError("global pre-cap ranks must be contiguous across both sidecars")
        channels = tuple(item.channel for item in self.channel_captures)
        if len(set(channels)) != len(channels):
            raise ValueError("channel capture records must be unique")
        captures = {item.channel: item for item in self.channel_captures}
        capture_fields = (
            "generated_candidate_count",
            "emitted_candidate_count",
            "channel_cap",
            "complete_pre_cap_capture",
            "source_artifact_sha256",
            "source_bundle_sha256",
            "source_schema_version",
            "emitted_records_sha256",
        )
        for candidate in all_candidates:
            for provenance in candidate.provenance:
                capture = captures.get(provenance.channel)
                if capture is None:
                    raise ValueError("candidate provenance lacks channel capture authority")
                if any(
                    getattr(provenance, field) != getattr(capture, field)
                    for field in capture_fields
                ):
                    raise ValueError("candidate provenance disagrees with channel capture")
                if provenance.channel_pre_cap_rank > capture.emitted_candidate_count:
                    raise ValueError("candidate provenance rank exceeds emitted channel output")
        for capture in self.channel_captures:
            channel_rows = tuple(
                sorted(
                    (
                        (candidate, provenance)
                        for candidate in all_candidates
                        for provenance in candidate.provenance
                        if provenance.channel is capture.channel
                    ),
                    key=lambda item: item[1].channel_pre_cap_rank,
                )
            )
            if len(channel_rows) != capture.emitted_candidate_count:
                raise ValueError("candidate sidecars do not contain every emitted channel record")
            reconstructed = tuple(
                AddressProposal(
                    mention=candidate.mention_hypothesis,
                    entity_id=candidate.entity_id,
                    canonical_title=candidate.canonical_title,
                    channel=provenance.channel,
                    source_subchannel=provenance.source_subchannel,
                    source_record_id=provenance.source_record_id,
                    channel_pre_cap_rank=provenance.channel_pre_cap_rank,
                    raw_channel_score=provenance.raw_channel_score,
                    score_transform=provenance.score_transform,
                    channel_score=provenance.channel_score,
                    exact_score=provenance.exact_score,
                    fuzzy_score=provenance.fuzzy_score,
                    semantic_score=provenance.semantic_score,
                    anchor_prior=provenance.anchor_prior,
                    support_count=provenance.support_count,
                    source_document_count=provenance.source_document_count,
                    source_diversity=provenance.source_diversity,
                    title_indicator=provenance.title_indicator,
                    redirect_indicator=provenance.redirect_indicator,
                    alias_indicator=provenance.alias_indicator,
                    type_score=provenance.type_score,
                    relation_score=provenance.relation_score,
                    context_score=provenance.context_score,
                    ambiguity_entropy_nats=provenance.ambiguity_entropy_nats,
                )
                for candidate, provenance in channel_rows
            )
            if capture.emitted_records_sha256 != _emitted_records_sha256(reconstructed):
                raise ValueError("candidate sidecar source records disagree with channel capture")
        measured_complete = all(item.complete_pre_cap_capture for item in self.channel_captures)
        if self.all_channels_complete_pre_cap != measured_complete:
            raise ValueError("union completeness disagrees with channel capture provenance")
        maximum_unresolved = max(item.unresolved_probability_mass for item in self.channel_captures)
        if not math.isclose(self.unresolved_probability_mass, maximum_unresolved, abs_tol=1e-12):
            raise ValueError("union unresolved mass disagrees with channel captures")
        return self


def _provenance(proposal: AddressProposal, output: AddressChannelOutput) -> ChannelProvenance:
    values = proposal.model_dump(
        exclude={"schema_version", "mention", "entity_id", "canonical_title"}
    )
    return ChannelProvenance(
        **values,
        generated_candidate_count=output.generated_candidate_count,
        emitted_candidate_count=output.emitted_candidate_count,
        channel_cap=output.channel_cap,
        complete_pre_cap_capture=output.complete_pre_cap_capture,
        source_artifact_sha256=output.source_artifact_sha256,
        source_bundle_sha256=output.source_bundle_sha256,
        source_schema_version=output.source_schema_version,
        emitted_records_sha256=output.emitted_records_sha256,
    )


def _capture(output: AddressChannelOutput) -> ChannelCapture:
    return ChannelCapture(
        channel=output.channel,
        generated_candidate_count=output.generated_candidate_count,
        emitted_candidate_count=output.emitted_candidate_count,
        channel_cap=output.channel_cap,
        complete_pre_cap_capture=output.complete_pre_cap_capture,
        source_artifact_sha256=output.source_artifact_sha256,
        source_bundle_sha256=output.source_bundle_sha256,
        source_schema_version=output.source_schema_version,
        emitted_records_sha256=output.emitted_records_sha256,
        unresolved_probability_mass=output.unresolved_probability_mass,
    )


def _channel_disagreement(outputs: tuple[AddressChannelOutput, ...]) -> float:
    top_ids = [
        min(output.proposals, key=lambda item: item.channel_pre_cap_rank).entity_id
        for output in outputs
        if output.proposals
    ]
    if len(top_ids) <= 1:
        return 0.0
    most_common = Counter(top_ids).most_common(1)[0][1]
    return 1.0 - most_common / len(top_ids)


def _mention_key(mention: MentionHypothesis) -> tuple[str, str, str, int, int]:
    return (
        mention.hypothesis_id,
        mention.surface,
        mention.normalized_surface,
        mention.char_start,
        mention.char_end,
    )


def _merged_mention(outputs: tuple[AddressChannelOutput, ...]) -> MentionHypothesis:
    first = outputs[0].mention
    if any(_mention_key(output.mention) != _mention_key(first) for output in outputs):
        raise ValueError("one address union may contain only one mention hypothesis")
    channels = tuple(
        sorted(
            {channel for output in outputs for channel in output.mention.proposal_channels},
            key=lambda item: item.value,
        )
    )
    return first.model_copy(update={"proposal_channels": channels})


def union_address_channels(
    outputs: tuple[AddressChannelOutput, ...], *, cap: int
) -> AddressCandidateUnion:
    """Union every proposal by canonical ID before applying one global cap."""

    if not outputs:
        raise ValueError("address union requires at least one channel output")
    if not 1 <= cap <= 64:
        raise ValueError("global address cap must be in [1,64]")
    mention = _merged_mention(outputs)
    channels = [output.channel for output in outputs]
    if len(set(channels)) != len(channels):
        raise ValueError("address union accepts at most one output per channel")
    output_by_channel = {output.channel: output for output in outputs}

    grouped: dict[str, list[AddressProposal]] = {}
    titles: dict[str, str] = {}
    for output in outputs:
        for proposal in output.proposals:
            prior_title = titles.setdefault(proposal.entity_id, proposal.canonical_title)
            if prior_title != proposal.canonical_title:
                raise ValueError("one canonical entity ID cannot carry conflicting titles")
            grouped.setdefault(proposal.entity_id, []).append(proposal)

    ordered_ids = sorted(
        grouped,
        key=lambda entity_id: (
            -max(item.channel_score for item in grouped[entity_id]),
            -len(grouped[entity_id]),
            min(item.channel_pre_cap_rank for item in grouped[entity_id]),
            entity_id,
        ),
    )
    candidates: list[CanonicalAddressCandidate] = []
    for rank, entity_id in enumerate(ordered_ids, start=1):
        rows = sorted(
            grouped[entity_id],
            key=lambda item: (item.channel.value, item.channel_pre_cap_rank),
        )
        candidates.append(
            CanonicalAddressCandidate(
                mention_hypothesis=mention,
                entity_id=entity_id,
                canonical_title=titles[entity_id],
                global_pre_cap_rank=rank,
                provenance=tuple(
                    _provenance(item, output_by_channel[item.channel]) for item in rows
                ),
                best_channel_score=max(item.channel_score for item in rows),
                exact_score=max(item.exact_score for item in rows),
                fuzzy_score=max(item.fuzzy_score for item in rows),
                semantic_score=max(item.semantic_score for item in rows),
                anchor_prior=max(item.anchor_prior for item in rows),
                support_count=max(item.support_count for item in rows),
                source_document_count=max(item.source_document_count for item in rows),
                source_diversity=max(item.source_diversity for item in rows),
                title_indicator=any(item.title_indicator for item in rows),
                redirect_indicator=any(item.redirect_indicator for item in rows),
                alias_indicator=any(item.alias_indicator for item in rows),
                type_score=max(item.type_score for item in rows),
                relation_score=max(item.relation_score for item in rows),
                context_score=max(item.context_score for item in rows),
                ambiguity_entropy_nats=max(item.ambiguity_entropy_nats for item in rows),
            )
        )
    captures = tuple(
        sorted(
            (_capture(output) for output in outputs),
            key=lambda item: item.channel.value,
        )
    )
    return AddressCandidateUnion(
        mention_hypothesis=mention,
        cap=cap,
        pre_cap_candidate_count=len(ordered_ids),
        candidates=tuple(candidates[:cap]),
        pruned_candidates=tuple(candidates[cap:]),
        channel_captures=captures,
        all_channels_complete_pre_cap=all(item.complete_pre_cap_capture for item in captures),
        unresolved_probability_mass=max(item.unresolved_probability_mass for item in captures),
        channel_disagreement=_channel_disagreement(outputs),
    )


class AddressLabelledExample(FrozenModel):
    """Mention-aligned training/qualification record with sealed-split protection."""

    schema_version: Literal["aethersparse.address-labelled-example.v12"] = (
        "aethersparse.address-labelled-example.v12"
    )
    case_id: str
    partition: str
    corpus_tier: str
    training_eligible: bool
    channel_outputs: tuple[AddressChannelOutput, ...] = Field(min_length=1)
    correct_entity_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_training_boundary(self) -> AddressLabelledExample:
        if self.partition not in {"development", "tuning"}:
            raise ValueError("address fit/calibration records may not consume sealed partitions")
        if not self.training_eligible:
            raise ValueError("address fit/calibration records must be training eligible")
        if len(set(self.correct_entity_ids)) != len(self.correct_entity_ids):
            raise ValueError("correct entity IDs must be unique")
        if any(not _valid_entity_id(entity_id) for entity_id in self.correct_entity_ids):
            raise ValueError("correct entity labels must be authoritative canonical IDs")
        first = self.channel_outputs[0].mention
        if any(
            _mention_key(output.mention) != _mention_key(first) for output in self.channel_outputs
        ):
            raise ValueError("one labelled example must describe one mention hypothesis")
        channels = tuple(output.channel for output in self.channel_outputs)
        if len(set(channels)) != len(channels):
            raise ValueError("one labelled example may contain at most one output per channel")
        return self


class AddressFusionParameters(FrozenModel):
    """Development-fitted candidate weights plus tuning-selected temperature."""

    schema_version: Literal["aethersparse.address-fusion-parameters.v12"] = (
        "aethersparse.address-fusion-parameters.v12"
    )
    candidate_weights: tuple[float, ...] = Field(
        min_length=_FEATURE_COUNT, max_length=_FEATURE_COUNT
    )
    unresolved_bias: float
    unresolved_mass_weight: float
    ambiguity_weight: float
    disagreement_weight: float
    temperature: float = Field(default=1.0, gt=0.0)
    fitted_on: Literal["development"] = "development"
    temperature_selected_on: Literal["none", "tuning"] = "none"
    fit_case_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def fit_cases_are_unique(self) -> AddressFusionParameters:
        if len(set(self.fit_case_ids)) != len(self.fit_case_ids):
            raise ValueError("development fit case IDs must be unique")
        return self


def _candidate_features(candidate: CanonicalAddressCandidate) -> tuple[float, ...]:
    return (
        1.0,
        candidate.best_channel_score,
        candidate.exact_score,
        candidate.fuzzy_score,
        candidate.semantic_score,
        candidate.anchor_prior,
        candidate.support_count / (candidate.support_count + 10.0),
        candidate.source_diversity,
        float(candidate.title_indicator),
        float(candidate.redirect_indicator),
        float(candidate.alias_indicator),
        candidate.type_score,
        candidate.relation_score,
        candidate.context_score,
        1.0 / candidate.global_pre_cap_rank,
        len(candidate.provenance) / len(AddressChannel),
    )


def _unresolved_features(union: AddressCandidateUnion) -> tuple[float, float, float, float]:
    entropy = max((item.ambiguity_entropy_nats for item in union.candidates), default=0.0)
    normalized_entropy = entropy / (1.0 + entropy)
    return (1.0, union.unresolved_probability_mass, normalized_entropy, union.channel_disagreement)


def _softmax(logits: tuple[float, ...], temperature: float) -> tuple[float, ...]:
    scaled = tuple(value / temperature for value in logits)
    maximum = max(scaled)
    values = tuple(math.exp(value - maximum) for value in scaled)
    total = sum(values)
    return tuple(value / total for value in values)


class AddressBelief(FrozenModel):
    """Calibrated categorical belief over exact addresses plus unresolved mass."""

    schema_version: Literal["aethersparse.address-belief.v12"] = "aethersparse.address-belief.v12"
    union: AddressCandidateUnion
    distribution: CategoricalBelief
    normalized_entropy: float = Field(ge=0.0, le=1.0)
    channel_disagreement: float = Field(ge=0.0, le=1.0)


class PersistedAddressUnionEnvelope(FrozenModel):
    """Versioned, content-addressed wire envelope for a persisted candidate union."""

    schema_version: Literal["aethersparse.address-union-envelope.v12"] = (
        "aethersparse.address-union-envelope.v12"
    )
    union: AddressCandidateUnion
    union_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    architecture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_content_address(self) -> PersistedAddressUnionEnvelope:
        if self.union_sha256 != _model_sha256(self.union):
            raise ValueError("persisted address union SHA-256 mismatch")
        return self


class PersistedAddressBeliefEnvelope(FrozenModel):
    """Versioned, content-addressed wire envelope for a persisted address belief."""

    schema_version: Literal["aethersparse.address-belief-envelope.v12"] = (
        "aethersparse.address-belief-envelope.v12"
    )
    belief: AddressBelief
    belief_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fusion_parameters_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    architecture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_content_address(self) -> PersistedAddressBeliefEnvelope:
        if self.belief_sha256 != _model_sha256(self.belief):
            raise ValueError("persisted address belief SHA-256 mismatch")
        return self


class AddressFusionModel:
    """Pure scorer over a fixed canonical candidate union."""

    def __init__(self, parameters: AddressFusionParameters) -> None:
        self.parameters = parameters

    def predict(self, union: AddressCandidateUnion) -> AddressBelief:
        weights = self.parameters.candidate_weights
        candidate_logits = tuple(
            sum(
                weight * value
                for weight, value in zip(weights, _candidate_features(item), strict=True)
            )
            for item in union.candidates
        )
        unresolved = _unresolved_features(union)
        unresolved_logit = (
            self.parameters.unresolved_bias
            + self.parameters.unresolved_mass_weight * unresolved[1]
            + self.parameters.ambiguity_weight * unresolved[2]
            + self.parameters.disagreement_weight * unresolved[3]
        )
        labels = (*tuple(item.entity_id for item in union.candidates), UNRESOLVED_ADDRESS)
        probabilities = _softmax((*candidate_logits, unresolved_logit), self.parameters.temperature)
        distribution = CategoricalBelief(labels=labels, probabilities=probabilities)
        return AddressBelief(
            union=union,
            distribution=distribution,
            normalized_entropy=distribution.normalized_entropy,
            channel_disagreement=union.channel_disagreement,
        )


def _validate_examples(
    examples: tuple[AddressLabelledExample, ...], *, partition: Literal["development", "tuning"]
) -> None:
    if not examples:
        raise ValueError("address fitting/calibration requires at least one example")
    invalid = sorted({item.partition for item in examples if item.partition != partition})
    if invalid:
        raise ValueError(f"{partition} operation may not consume partitions: {invalid}")
    partitions_by_case: dict[str, set[str]] = {}
    for item in examples:
        partitions_by_case.setdefault(item.case_id, set()).add(item.partition)
    if any(len(values) != 1 for values in partitions_by_case.values()):
        raise ValueError("tier replicas may not cross partitions")


def _targets(union: AddressCandidateUnion, correct_ids: tuple[str, ...]) -> tuple[float, ...]:
    labels = tuple(item.entity_id for item in union.candidates)
    present = [index for index, label in enumerate(labels) if label in correct_ids]
    target = [0.0 for _ in range(len(labels) + 1)]
    if present:
        for index in present:
            target[index] = 1.0 / len(present)
    else:
        target[-1] = 1.0
    return tuple(target)


def fit_address_fusion(
    development: tuple[AddressLabelledExample, ...],
    *,
    cap: int = 64,
    iterations: int = 250,
    learning_rate: float = 0.08,
    l2: float = 1e-4,
) -> AddressFusionParameters:
    """Fit a compact multinomial scorer using development examples only."""

    _validate_examples(development, partition="development")
    if iterations < 1 or learning_rate <= 0.0 or l2 < 0.0:
        raise ValueError("invalid deterministic optimizer settings")
    weights = [0.0] * _FEATURE_COUNT
    unresolved_weights = [0.0, 1.0, 0.0, 0.0]
    case_counts = Counter(item.case_id for item in development)
    total_case_weight = float(len(case_counts))
    unions = tuple(union_address_channels(item.channel_outputs, cap=cap) for item in development)
    for _ in range(iterations):
        gradient = [0.0] * _FEATURE_COUNT
        unresolved_gradient = [0.0] * 4
        for example, union in zip(development, unions, strict=True):
            features = tuple(_candidate_features(item) for item in union.candidates)
            unresolved_features = _unresolved_features(union)
            logits = (
                *tuple(
                    sum(weight * value for weight, value in zip(weights, row, strict=True))
                    for row in features
                ),
                sum(
                    weight * value
                    for weight, value in zip(unresolved_weights, unresolved_features, strict=True)
                ),
            )
            probabilities = _softmax(logits, 1.0)
            targets = _targets(union, example.correct_entity_ids)
            case_weight = 1.0 / case_counts[example.case_id] / total_case_weight
            for probability, target, row in zip(
                probabilities[:-1], targets[:-1], features, strict=True
            ):
                residual = (probability - target) * case_weight
                for index, value in enumerate(row):
                    gradient[index] += residual * value
            unresolved_residual = (probabilities[-1] - targets[-1]) * case_weight
            for index, value in enumerate(unresolved_features):
                unresolved_gradient[index] += unresolved_residual * value
        for index in range(_FEATURE_COUNT):
            gradient[index] += l2 * weights[index]
            weights[index] -= learning_rate * gradient[index]
        for index in range(4):
            unresolved_gradient[index] += l2 * unresolved_weights[index]
            unresolved_weights[index] -= learning_rate * unresolved_gradient[index]
    return AddressFusionParameters(
        candidate_weights=tuple(weights),
        unresolved_bias=unresolved_weights[0],
        unresolved_mass_weight=unresolved_weights[1],
        ambiguity_weight=unresolved_weights[2],
        disagreement_weight=unresolved_weights[3],
        fit_case_ids=tuple(sorted(case_counts)),
    )


def _example_nll(
    examples: tuple[AddressLabelledExample, ...], parameters: AddressFusionParameters, cap: int
) -> float:
    model = AddressFusionModel(parameters)
    losses: list[float] = []
    for example in examples:
        union = union_address_channels(example.channel_outputs, cap=cap)
        belief = model.predict(union).distribution
        targets = _targets(union, example.correct_entity_ids)
        correct_mass = sum(
            probability * target
            for probability, target in zip(belief.probabilities, targets, strict=True)
        )
        target_count = sum(value > 0.0 for value in targets)
        losses.append(-math.log(max(_EPSILON, correct_mass * target_count)))
    return sum(losses) / len(losses)


def select_temperature(
    tuning: tuple[AddressLabelledExample, ...],
    fitted: AddressFusionParameters,
    *,
    cap: int = 64,
    grid: tuple[float, ...] = (0.5, 0.75, 1.0, 1.5, 2.0),
) -> AddressFusionParameters:
    """Select only a scalar temperature on tuning; feature weights stay frozen."""

    _validate_examples(tuning, partition="tuning")
    overlap = sorted(set(fitted.fit_case_ids) & {item.case_id for item in tuning})
    if overlap:
        raise ValueError(f"case IDs cross development/tuning partitions: {overlap[:5]}")
    if not grid or any(value <= 0.0 or not math.isfinite(value) for value in grid):
        raise ValueError("temperature grid must contain finite positive values")
    candidates = tuple(
        fitted.model_copy(update={"temperature": value, "temperature_selected_on": "tuning"})
        for value in grid
    )
    return min(candidates, key=lambda item: (_example_nll(tuning, item, cap), item.temperature))


class KRecall(FrozenModel):
    schema_version: Literal["aethersparse.address-k-recall.v12"] = (
        "aethersparse.address-k-recall.v12"
    )
    k: int
    entity_recall: float = Field(ge=0.0, le=1.0)
    multi_entity_completeness: float = Field(ge=0.0, le=1.0)


class RiskCoverage(FrozenModel):
    schema_version: Literal["aethersparse.address-risk-coverage.v12"] = (
        "aethersparse.address-risk-coverage.v12"
    )
    coverage: float = Field(ge=0.0, le=1.0)
    risk: float | None = Field(default=None, ge=0.0, le=1.0)


class AddressQualification(FrozenModel):
    schema_version: Literal["aethersparse.address-qualification.v12"] = (
        "aethersparse.address-qualification.v12"
    )
    partition: Literal["development", "tuning"]
    case_count: int = Field(ge=1)
    input_examples_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fusion_parameters_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_ids_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    channel_capture_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mention_alignment_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    channel_output_count: int = Field(ge=1)
    observed_generation_channels: tuple[AddressChannel, ...] = Field(min_length=1)
    source_artifact_sha256s: tuple[str, ...] = Field(min_length=1)
    source_bundle_sha256s: tuple[str, ...] = Field(min_length=1)
    source_schema_versions: tuple[str, ...] = Field(min_length=1)
    all_channel_outputs_complete_pre_cap: bool
    mention_alignment_record_count: int = Field(ge=0)
    k_recall: tuple[KRecall, ...]
    entity_top1_accuracy: float = Field(ge=0.0, le=1.0)
    state_top1_accuracy: float = Field(ge=0.0, le=1.0)
    availability_state_negative_log_likelihood: float = Field(ge=0.0)
    availability_state_multiclass_brier: float = Field(ge=0.0)
    availability_state_expected_calibration_error: float = Field(ge=0.0, le=1.0)
    resolved_address_expected_calibration_error: float | None = Field(default=None, ge=0.0, le=1.0)
    mean_normalized_entropy: float = Field(ge=0.0, le=1.0)
    mean_channel_disagreement: float = Field(ge=0.0, le=1.0)
    address_selective_risk_coverage: tuple[RiskCoverage, ...]
    availability_calibration_scope: Literal[
        "candidate_addresses_plus_unresolved_availability_state"
    ] = "candidate_addresses_plus_unresolved_availability_state"
    address_risk_scope: Literal["resolved_entity_predictions_only_unresolved_reduces_coverage"] = (
        "resolved_entity_predictions_only_unresolved_reduces_coverage"
    )

    @model_validator(mode="after")
    def validate_qualification_shape(self) -> AddressQualification:
        if tuple(item.k for item in self.k_recall) != _ALLOWED_K:
            raise ValueError("qualification must contain ordered K=8/16/32/64 metrics")
        if self.mention_alignment_record_count > self.case_count:
            raise ValueError("mention alignment records cannot exceed qualification cases")
        if self.observed_generation_channels != tuple(
            sorted(set(self.observed_generation_channels), key=lambda item: item.value)
        ):
            raise ValueError("observed qualification channels must be sorted and unique")
        if any(
            re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in (*self.source_artifact_sha256s, *self.source_bundle_sha256s)
        ):
            raise ValueError("qualification sources must use SHA-256 identities")
        if any(not value.strip() for value in self.source_schema_versions):
            raise ValueError("qualification sources must use versioned schemas")
        for identities in (
            self.source_artifact_sha256s,
            self.source_bundle_sha256s,
            self.source_schema_versions,
        ):
            if identities != tuple(sorted(set(identities))):
                raise ValueError("qualification source identities must be sorted and unique")
        return self


class VerifiedPreCapCaptureManifest(FrozenModel):
    """Verifier-produced proof that every tuning channel was captured before its cap."""

    schema_version: Literal["aethersparse.pre-cap-capture-manifest.v12"] = (
        "aethersparse.pre-cap-capture-manifest.v12"
    )
    partition: Literal["tuning"] = "tuning"
    input_examples_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    channel_capture_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    channel_output_count: int = Field(ge=1)
    observed_channels: tuple[AddressChannel, ...] = Field(min_length=1)
    all_channel_outputs_complete_pre_cap: Literal[True] = True
    verifier_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_channel_coverage(self) -> VerifiedPreCapCaptureManifest:
        expected = tuple(sorted(AddressChannel, key=lambda item: item.value))
        if self.observed_channels != expected:
            raise ValueError("pre-cap manifest must verify every address generation channel")
        return self


class VerifiedAddressSourceManifest(FrozenModel):
    """Verifier-produced source artifact/schema/bundle identity for tuning channels."""

    schema_version: Literal["aethersparse.address-source-manifest.v12"] = (
        "aethersparse.address-source-manifest.v12"
    )
    partition: Literal["tuning"] = "tuning"
    input_examples_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    channel_capture_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    channel_output_count: int = Field(ge=1)
    observed_channels: tuple[AddressChannel, ...] = Field(min_length=1)
    source_artifact_sha256s: tuple[str, ...] = Field(min_length=1)
    source_bundle_sha256s: tuple[str, ...] = Field(min_length=1)
    source_schema_versions: tuple[str, ...] = Field(min_length=1)
    development_examples_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    lawful_development_example_count: int = Field(ge=0)
    verifier_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_source_identities(self) -> VerifiedAddressSourceManifest:
        expected = tuple(sorted(AddressChannel, key=lambda item: item.value))
        if self.observed_channels != expected:
            raise ValueError("source manifest must verify every address generation channel")
        if any(
            re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in (*self.source_artifact_sha256s, *self.source_bundle_sha256s)
        ):
            raise ValueError("source manifest artifacts and bundles must use SHA-256")
        if any(not value.strip() for value in self.source_schema_versions):
            raise ValueError("source manifest schemas must be versioned")
        for identities in (
            self.source_artifact_sha256s,
            self.source_bundle_sha256s,
            self.source_schema_versions,
        ):
            if identities != tuple(sorted(set(identities))):
                raise ValueError("source manifest identities must be sorted and unique")
        return self


class VerifiedMentionAlignmentManifest(FrozenModel):
    """Verifier-produced proof that every tuning case has one aligned mention record."""

    schema_version: Literal["aethersparse.mention-alignment-manifest.v12"] = (
        "aethersparse.mention-alignment-manifest.v12"
    )
    partition: Literal["tuning"] = "tuning"
    input_examples_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_ids_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mention_alignment_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    aligned_record_count: int = Field(ge=1)
    expected_record_count: int = Field(ge=1)
    alignment_complete: Literal[True] = True
    verifier_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_alignment_count(self) -> VerifiedMentionAlignmentManifest:
        if self.aligned_record_count != self.expected_record_count:
            raise ValueError("mention alignment manifest is incomplete")
        return self


class VerifiedTuningAddressQualification(FrozenModel):
    """Content-addressed tuning metrics plus independently verified data manifests."""

    schema_version: Literal["aethersparse.verified-tuning-address-qualification.v12"] = (
        "aethersparse.verified-tuning-address-qualification.v12"
    )
    qualification: AddressQualification
    qualification_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_manifest: VerifiedAddressSourceManifest
    source_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pre_cap_capture_manifest: VerifiedPreCapCaptureManifest
    pre_cap_capture_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mention_alignment_manifest: VerifiedMentionAlignmentManifest
    mention_alignment_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    architecture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluator_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verify_bound_qualification(self) -> VerifiedTuningAddressQualification:
        if self.qualification.partition != "tuning":
            raise ValueError("specialist readiness requires a tuning qualification")
        if self.qualification_sha256 != _model_sha256(self.qualification):
            raise ValueError("tuning qualification SHA-256 mismatch")
        if self.source_manifest_sha256 != _model_sha256(self.source_manifest):
            raise ValueError("address source manifest SHA-256 mismatch")
        if self.pre_cap_capture_manifest_sha256 != _model_sha256(self.pre_cap_capture_manifest):
            raise ValueError("pre-cap capture manifest SHA-256 mismatch")
        if self.mention_alignment_manifest_sha256 != _model_sha256(self.mention_alignment_manifest):
            raise ValueError("mention alignment manifest SHA-256 mismatch")
        capture = self.pre_cap_capture_manifest
        source = self.source_manifest
        alignment = self.mention_alignment_manifest
        if not self.qualification.all_channel_outputs_complete_pre_cap:
            raise ValueError("verified qualification requires complete pre-cap channel output")
        if (
            capture.channel_output_count != self.qualification.channel_output_count
            or capture.channel_output_count != self.qualification.case_count * len(AddressChannel)
        ):
            raise ValueError("pre-cap manifest must cover every channel for every tuning case")
        if (
            set(self.qualification.observed_generation_channels) != set(AddressChannel)
            or capture.observed_channels != self.qualification.observed_generation_channels
        ):
            raise ValueError("pre-cap manifest channel set disagrees with qualification")
        if (
            capture.input_examples_sha256 != self.qualification.input_examples_sha256
            or capture.channel_capture_evidence_sha256
            != self.qualification.channel_capture_evidence_sha256
        ):
            raise ValueError("pre-cap manifest is not bound to the tuning qualification")
        if (
            source.input_examples_sha256 != self.qualification.input_examples_sha256
            or source.channel_capture_evidence_sha256
            != self.qualification.channel_capture_evidence_sha256
            or source.channel_output_count != self.qualification.channel_output_count
            or source.observed_channels != self.qualification.observed_generation_channels
            or source.source_artifact_sha256s != self.qualification.source_artifact_sha256s
            or source.source_bundle_sha256s != self.qualification.source_bundle_sha256s
            or source.source_schema_versions != self.qualification.source_schema_versions
        ):
            raise ValueError("source manifest is not bound to the tuning qualification")
        if (
            alignment.input_examples_sha256 != self.qualification.input_examples_sha256
            or alignment.case_ids_sha256 != self.qualification.case_ids_sha256
            or alignment.mention_alignment_evidence_sha256
            != self.qualification.mention_alignment_evidence_sha256
        ):
            raise ValueError("alignment manifest is not bound to the tuning qualification")
        if (
            alignment.aligned_record_count != self.qualification.case_count
            or alignment.expected_record_count != self.qualification.case_count
            or self.qualification.mention_alignment_record_count != self.qualification.case_count
        ):
            raise ValueError("alignment manifest count disagrees with tuning qualification")
        return self


def _address_coverage_curve(
    confidence_correct_and_resolved: list[tuple[float, bool, bool]],
) -> tuple[RiskCoverage, ...]:
    """Rank resolved addresses only; abstentions remain outside covered mass."""

    total = len(confidence_correct_and_resolved)
    ordered = sorted(
        (
            (confidence, correct)
            for confidence, correct, resolved in confidence_correct_and_resolved
            if resolved
        ),
        key=lambda item: (-item[0], not item[1]),
    )
    if not ordered:
        return (RiskCoverage(coverage=0.0, risk=None),)
    points: list[RiskCoverage] = []
    for target_coverage in (0.1, 0.25, 0.5, 0.75, 1.0):
        count = max(1, math.ceil(len(ordered) * target_coverage))
        retained = ordered[:count]
        points.append(
            RiskCoverage(
                coverage=count / total,
                risk=1.0 - sum(correct for _, correct in retained) / count,
            )
        )
    return tuple(points)


def _ece(rows: list[tuple[float, float]]) -> float | None:
    if not rows:
        return None
    result = 0.0
    for index in range(10):
        lower = index / 10.0
        upper = (index + 1) / 10.0
        members = [
            item for item in rows if lower <= item[0] <= upper and (index == 9 or item[0] < upper)
        ]
        if members:
            mean_confidence = sum(item[0] for item in members) / len(members)
            mean_correct = sum(item[1] for item in members) / len(members)
            result += len(members) / len(rows) * abs(mean_confidence - mean_correct)
    return result


def evaluate_address_fusion(
    examples: tuple[AddressLabelledExample, ...],
    model: AddressFusionModel,
    *,
    partition: Literal["development", "tuning"],
) -> AddressQualification:
    """Measure candidate generation separately from calibrated resolution."""

    _validate_examples(examples, partition=partition)
    k_hits = {k: 0 for k in _ALLOWED_K}
    k_total_entities = {k: 0 for k in _ALLOWED_K}
    k_complete = {k: 0 for k in _ALLOWED_K}
    top_entity_correct = 0
    top_state_correct = 0
    losses: list[float] = []
    briers: list[float] = []
    entropies: list[float] = []
    disagreements: list[float] = []
    address_confidence_correct_and_resolved: list[tuple[float, bool, bool]] = []
    availability_calibration_rows: list[tuple[float, float]] = []
    resolved_address_calibration_rows: list[tuple[float, float]] = []
    for example in examples:
        unions = {k: union_address_channels(example.channel_outputs, cap=k) for k in _ALLOWED_K}
        for k, union in unions.items():
            retained = {item.entity_id for item in union.candidates}
            hits = sum(entity_id in retained for entity_id in example.correct_entity_ids)
            k_hits[k] += hits
            k_total_entities[k] += len(example.correct_entity_ids)
            k_complete[k] += int(hits == len(example.correct_entity_ids))
        belief = model.predict(unions[64])
        target = _targets(unions[64], example.correct_entity_ids)
        present_gold = {
            item.entity_id
            for item in unions[64].candidates
            if item.entity_id in example.correct_entity_ids
        }
        top_label = belief.distribution.top_label
        entity_correct = top_label in example.correct_entity_ids
        state_correct = entity_correct or (not present_gold and top_label == UNRESOLVED_ADDRESS)
        top_entity_correct += int(entity_correct)
        top_state_correct += int(state_correct)
        correct_mass = sum(
            probability * target_value
            for probability, target_value in zip(
                belief.distribution.probabilities, target, strict=True
            )
        )
        target_count = sum(value > 0.0 for value in target)
        losses.append(-math.log(max(_EPSILON, correct_mass * target_count)))
        briers.append(
            sum(
                (probability - target_value) ** 2
                for probability, target_value in zip(
                    belief.distribution.probabilities, target, strict=True
                )
            )
        )
        confidence = belief.distribution.top_probability
        resolved = top_label != UNRESOLVED_ADDRESS
        availability_calibration_rows.append((confidence, float(state_correct)))
        if resolved:
            resolved_address_calibration_rows.append((confidence, float(entity_correct)))
        address_confidence_correct_and_resolved.append((confidence, entity_correct, resolved))
        entropies.append(belief.normalized_entropy)
        disagreements.append(belief.channel_disagreement)
    availability_ece = _ece(availability_calibration_rows)
    assert availability_ece is not None
    ordered_examples = sorted(
        examples,
        key=lambda item: (
            item.case_id,
            item.corpus_tier,
            item.channel_outputs[0].mention.hypothesis_id,
        ),
    )
    input_examples = [item.model_dump(mode="json") for item in ordered_examples]
    capture_evidence = [
        {
            "case_id": example.case_id,
            "corpus_tier": example.corpus_tier,
            "mention_hypothesis_id": example.channel_outputs[0].mention.hypothesis_id,
            "channels": [
                {
                    "channel": output.channel.value,
                    "generated_candidate_count": output.generated_candidate_count,
                    "emitted_candidate_count": output.emitted_candidate_count,
                    "channel_cap": output.channel_cap,
                    "complete_pre_cap_capture": output.complete_pre_cap_capture,
                    "source_artifact_sha256": output.source_artifact_sha256,
                    "source_bundle_sha256": output.source_bundle_sha256,
                    "source_schema_version": output.source_schema_version,
                    "emitted_records_sha256": output.emitted_records_sha256,
                }
                for output in sorted(example.channel_outputs, key=lambda item: item.channel.value)
            ],
        }
        for example in ordered_examples
    ]
    alignment_evidence = [
        {
            "case_id": example.case_id,
            "corpus_tier": example.corpus_tier,
            "mention": example.channel_outputs[0].mention.model_dump(mode="json"),
            "correct_entity_ids": sorted(example.correct_entity_ids),
        }
        for example in ordered_examples
    ]
    return AddressQualification(
        partition=partition,
        case_count=len(examples),
        input_examples_sha256=_json_sha256(input_examples),
        fusion_parameters_sha256=_model_sha256(model.parameters),
        case_ids_sha256=_json_sha256(sorted({item.case_id for item in examples})),
        channel_capture_evidence_sha256=_json_sha256(capture_evidence),
        mention_alignment_evidence_sha256=_json_sha256(alignment_evidence),
        channel_output_count=sum(len(item.channel_outputs) for item in examples),
        observed_generation_channels=tuple(
            sorted(
                {output.channel for example in examples for output in example.channel_outputs},
                key=lambda item: item.value,
            )
        ),
        source_artifact_sha256s=tuple(
            sorted(
                {
                    output.source_artifact_sha256
                    for example in examples
                    for output in example.channel_outputs
                }
            )
        ),
        source_bundle_sha256s=tuple(
            sorted(
                {
                    output.source_bundle_sha256
                    for example in examples
                    for output in example.channel_outputs
                }
            )
        ),
        source_schema_versions=tuple(
            sorted(
                {
                    output.source_schema_version
                    for example in examples
                    for output in example.channel_outputs
                }
            )
        ),
        all_channel_outputs_complete_pre_cap=all(
            output.complete_pre_cap_capture
            for example in examples
            for output in example.channel_outputs
        ),
        mention_alignment_record_count=len(examples),
        k_recall=tuple(
            KRecall(
                k=k,
                entity_recall=k_hits[k] / k_total_entities[k],
                multi_entity_completeness=k_complete[k] / len(examples),
            )
            for k in _ALLOWED_K
        ),
        entity_top1_accuracy=top_entity_correct / len(examples),
        state_top1_accuracy=top_state_correct / len(examples),
        availability_state_negative_log_likelihood=sum(losses) / len(losses),
        availability_state_multiclass_brier=sum(briers) / len(briers),
        availability_state_expected_calibration_error=availability_ece,
        resolved_address_expected_calibration_error=_ece(resolved_address_calibration_rows),
        mean_normalized_entropy=sum(entropies) / len(entropies),
        mean_channel_disagreement=sum(disagreements) / len(disagreements),
        address_selective_risk_coverage=_address_coverage_curve(
            address_confidence_correct_and_resolved
        ),
    )


class SpecialistReadiness(FrozenModel):
    schema_version: Literal["aethersparse.specialist-readiness.v12"] = (
        "aethersparse.specialist-readiness.v12"
    )
    decision: ReadinessDecision
    evidence_qualification_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    tuning_candidate_completeness: float | None = Field(default=None, ge=0.0, le=1.0)
    tuning_recall_at_16: float | None = Field(default=None, ge=0.0, le=1.0)
    mention_alignment_complete: bool
    pre_cap_provenance_available: bool
    source_manifest_verified: bool
    lawful_development_examples: int = Field(ge=0)
    lawful_tuning_examples: int = Field(ge=0)
    candidate_generation_ready: bool
    aspirational_recall_at_16_met: bool
    specialist_authorized: bool
    blockers: tuple[str, ...]


def assess_specialist_readiness(
    evidence: VerifiedTuningAddressQualification | None,
    *,
    unavailable_reasons: tuple[str, ...] = (),
) -> SpecialistReadiness:
    """Derive the gate only from content-addressed, verifier-bound tuning evidence."""

    if evidence is None:
        unavailable_blockers = tuple(
            dict.fromkeys(
                ("verified_tuning_address_qualification_unavailable", *unavailable_reasons)
            )
        )
        return SpecialistReadiness(
            decision=ReadinessDecision.ADDRESS_SUBSTRATE_INADEQUATE,
            evidence_qualification_sha256=None,
            tuning_candidate_completeness=None,
            tuning_recall_at_16=None,
            mention_alignment_complete=False,
            pre_cap_provenance_available=False,
            source_manifest_verified=False,
            lawful_development_examples=0,
            lawful_tuning_examples=0,
            candidate_generation_ready=False,
            aspirational_recall_at_16_met=False,
            specialist_authorized=False,
            blockers=unavailable_blockers,
        )

    qualification = evidence.qualification
    by_k = {item.k: item for item in qualification.k_recall}
    tuning_candidate_completeness = by_k[64].multi_entity_completeness
    tuning_recall_at_16 = by_k[16].entity_recall
    mention_alignment_complete = (
        evidence.mention_alignment_manifest.aligned_record_count == qualification.case_count
    )
    pre_cap_provenance_available = (
        evidence.pre_cap_capture_manifest.all_channel_outputs_complete_pre_cap
        and qualification.all_channel_outputs_complete_pre_cap
    )
    source_manifest_verified = True
    lawful_development_examples = evidence.source_manifest.lawful_development_example_count
    lawful_tuning_examples = qualification.case_count
    generation_ready = tuning_candidate_completeness >= 0.90
    aspirational = tuning_recall_at_16 >= 0.95
    blockers = list(unavailable_reasons)
    if not generation_ready:
        blockers.append("tuning_candidate_completeness_below_90_percent")
    if not mention_alignment_complete:
        blockers.append("mention_level_alignment_incomplete")
    if not pre_cap_provenance_available:
        blockers.append("pre_cap_channel_provenance_unavailable")
    if not source_manifest_verified:
        blockers.append("verified_source_manifest_unavailable")
    if lawful_development_examples == 0:
        blockers.append("no_lawful_development_examples")
    if lawful_tuning_examples == 0:
        blockers.append("no_lawful_tuning_examples")
    authorized = not blockers
    if authorized:
        decision = ReadinessDecision.CONTEXTUAL_SPECIALIST_JUSTIFIED
    elif tuning_candidate_completeness < 0.80:
        decision = ReadinessDecision.ADDRESS_SUBSTRATE_INADEQUATE
    else:
        decision = ReadinessDecision.ADDRESS_GENERATION_IMPROVED_BUT_NOT_READY
    return SpecialistReadiness(
        decision=decision,
        evidence_qualification_sha256=evidence.qualification_sha256,
        tuning_candidate_completeness=tuning_candidate_completeness,
        tuning_recall_at_16=tuning_recall_at_16,
        mention_alignment_complete=mention_alignment_complete,
        pre_cap_provenance_available=pre_cap_provenance_available,
        source_manifest_verified=source_manifest_verified,
        lawful_development_examples=lawful_development_examples,
        lawful_tuning_examples=lawful_tuning_examples,
        candidate_generation_ready=generation_ready,
        aspirational_recall_at_16_met=aspirational,
        specialist_authorized=authorized,
        blockers=tuple(blockers),
    )


class SuccessiveHalvingTrial(FrozenModel):
    schema_version: Literal["aethersparse.successive-halving-trial.v12"] = (
        "aethersparse.successive-halving-trial.v12"
    )
    parameter_count: Literal[250000, 1000000, 3000000, 5000000]
    initial_epoch_budget: int = Field(ge=1)
    promotion_metric: Literal["tuning_nll_then_recall_at_16"] = "tuning_nll_then_recall_at_16"


class SuccessiveHalvingPlan(FrozenModel):
    schema_version: Literal["aethersparse.successive-halving-plan.v12"] = (
        "aethersparse.successive-halving-plan.v12"
    )
    authorized: bool
    started: bool = False
    requested_parameter_counts: tuple[Literal[250000, 1000000, 3000000, 5000000], ...] = (
        250000,
        1000000,
        3000000,
        5000000,
    )
    trials: tuple[SuccessiveHalvingTrial, ...]
    blockers: tuple[str, ...]
    split_policy: Literal["fit_development_calibrate_select_tuning"] = (
        "fit_development_calibrate_select_tuning"
    )


def plan_successive_halving(readiness: SpecialistReadiness) -> SuccessiveHalvingPlan:
    """Produce the fixed sweep hook only after the ranking-problem gate opens."""

    trials = (
        SuccessiveHalvingTrial(parameter_count=250000, initial_epoch_budget=2),
        SuccessiveHalvingTrial(parameter_count=1000000, initial_epoch_budget=2),
        SuccessiveHalvingTrial(parameter_count=3000000, initial_epoch_budget=2),
        SuccessiveHalvingTrial(parameter_count=5000000, initial_epoch_budget=2),
    )
    return SuccessiveHalvingPlan(
        authorized=readiness.specialist_authorized,
        trials=trials if readiness.specialist_authorized else (),
        blockers=readiness.blockers,
    )
