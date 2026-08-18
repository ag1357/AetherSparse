from __future__ import annotations

import json
from pathlib import Path

import pytest

from aethersparse.controller.fuzzy_address import (
    AddressSurfaceRecord,
    FuzzyAddressDataError,
    FuzzyAddressIndex,
    FuzzyChannel,
    logical_index_bytes,
    union_address_results,
)
from aethersparse.controller.semantic_address import canonical_entity_id


def _record(
    surface: str,
    title: str | None,
    *,
    support: int = 1,
) -> AddressSurfaceRecord:
    return AddressSurfaceRecord(
        surface=surface,
        entity_id=canonical_entity_id(title) if title is not None else None,
        canonical_title=title,
        support_count=support,
        source_document_count=1,
    )


def _index() -> FuzzyAddressIndex:
    return FuzzyAddressIndex(
        (
            _record("Alpha Centauri", "Alpha Centauri", support=5),
            _record("Bette Davis", "Bette Davis", support=7),
            _record("New York City", "New York City", support=4),
            _record("Mercury", "Mercury (planet)", support=3),
            _record("Mercury", "Mercury (element)", support=2),
            _record("Lost target", None),
        )
    )


def test_exact_and_char_channels_preserve_copied_spans_and_partial_aliases() -> None:
    index = _index()
    exact = index.lookup("Who studied Alpha Centauri?", FuzzyChannel.EXACT)
    fuzzy = index.lookup("Who studied Alpha?", FuzzyChannel.CHAR_NGRAM)

    assert exact.address_proposals[0].entity_id == canonical_entity_id("Alpha Centauri")
    assert exact.address_proposals[0].observed_text == "Alpha Centauri"
    assert exact.address_proposals[0].char_start == 12
    assert exact.address_proposals[0].char_end == 26
    assert canonical_entity_id("Alpha Centauri") in {
        proposal.entity_id for proposal in fuzzy.address_proposals
    }


def test_edit_distance_recovers_a_typo_without_minting_an_id() -> None:
    result = _index().lookup("Which awards did Betta Davis win?", FuzzyChannel.EDIT_DISTANCE)

    assert canonical_entity_id("Bette Davis") in {
        proposal.entity_id for proposal in result.address_proposals
    }
    assert result.cost.distance_evaluations > 0
    assert result.cost.posting_list_lookups > 0
    assert result.cost.peak_accumulator_entries > 0
    assert all(
        proposal.entity_id.startswith("as:v050:entity:") for proposal in result.address_proposals
    )


def test_char_and_simhash_are_bounded_independent_proposal_channels() -> None:
    index = _index()
    char = index.lookup("How large is NewYork City?", FuzzyChannel.CHAR_NGRAM)
    simhash = index.lookup(
        "How large is NewYork City?",
        FuzzyChannel.SIMHASH_LSH,
        simhash_max_hamming=24,
    )
    union = union_address_results((char, simhash), address_cap=8)

    target = canonical_entity_id("New York City")
    assert target in {item.entity_id for item in char.address_proposals}
    assert target in {item.entity_id for item in simhash.address_proposals}
    combined = next(item for item in union if item.entity_id == target)
    assert combined.channels == (FuzzyChannel.CHAR_NGRAM, FuzzyChannel.SIMHASH_LSH)


def test_alias_collision_retains_both_exact_addresses_until_global_cap() -> None:
    result = _index().lookup("Is Mercury dense?", FuzzyChannel.EXACT, address_cap=8)

    assert {item.entity_id for item in result.address_proposals} == {
        canonical_entity_id("Mercury (planet)"),
        canonical_entity_id("Mercury (element)"),
    }
    assert result.pre_cap_address_count == 2
    assert result.address_cap_saturated is False


def test_unresolved_target_supports_a_mention_but_not_an_address() -> None:
    result = _index().lookup("Explain the Lost target", FuzzyChannel.EXACT)

    hypothesis = next(
        item for item in result.mention_hypotheses if item.matched_surface == "lost target"
    )
    assert hypothesis.unresolved_target is True
    assert hypothesis.entity_ids == ()
    assert result.address_proposals == ()


def test_caps_are_deterministic_and_report_saturation() -> None:
    records = tuple(_record("same alias", f"Entity {index}") for index in range(6))
    index = FuzzyAddressIndex(records)

    first = index.lookup("same alias", FuzzyChannel.EXACT, address_cap=2)
    second = index.lookup("same alias", FuzzyChannel.EXACT, address_cap=2)

    assert first == second
    assert first.pre_cap_address_count == 6
    assert first.address_cap_saturated is True
    assert len(first.address_proposals) == 2
    assert len(first.pre_cap_address_proposals) == 6
    assert len(first.pruned_address_proposals) == 4
    assert first.pruned_address_ids == tuple(
        item.entity_id for item in first.pruned_address_proposals
    )
    assert first.cap_accounting.pruned_address_count == 4


def test_global_union_uses_complete_channel_proposals_before_one_cap() -> None:
    records = tuple(_record("same alias", f"Entity {index}") for index in range(6))
    index = FuzzyAddressIndex(records)
    exact = index.lookup("same alias", FuzzyChannel.EXACT, address_cap=1)
    char = index.lookup("same alias", FuzzyChannel.CHAR_NGRAM, address_cap=1)

    union = union_address_results((exact, char), address_cap=2)

    assert len(exact.address_proposals) == len(char.address_proposals) == 1
    assert len(exact.pre_cap_address_proposals) == 6
    assert union.pre_cap_address_count == 6
    assert len(union.address_proposals) == 2
    assert len(union.pruned_address_proposals) == 4
    assert union.pruned_address_ids == tuple(
        item.entity_id for item in union.pruned_address_proposals
    )
    assert union.global_cap_saturated is True
    assert dict(union.channel_locally_pruned_counts) == {
        FuzzyChannel.EXACT: 5,
        FuzzyChannel.CHAR_NGRAM: 5,
    }
    assert all(
        item.channels == (FuzzyChannel.CHAR_NGRAM, FuzzyChannel.EXACT)
        for item in union.pre_cap_address_proposals
    )


def test_unresolved_mass_support_and_normalization_mapping_are_explicit() -> None:
    entity_id = canonical_entity_id("AC/DC")
    index = FuzzyAddressIndex(
        (
            AddressSurfaceRecord(
                "AC/DC",
                entity_id,
                "AC/DC",
                support_count=3,
                source_document_count=2,
                source_document_ids=("doc:1", "doc:2"),
                support_provenance_ids=("obs:1", "obs:2", "obs:3"),
                source_channels=("title",),
                source_provenance=("pack:1",),
            ),
            AddressSurfaceRecord(
                "AC/DC",
                None,
                None,
                support_count=1,
                source_document_count=1,
                source_document_ids=("doc:3",),
                support_provenance_ids=("obs:4",),
                source_channels=("anchor",),
                source_provenance=("pack:1",),
            ),
        )
    )

    result = index.lookup("About AC/DC?", FuzzyChannel.EXACT)
    hypothesis = next(item for item in result.mention_hypotheses if item.observed_text == "AC/DC")
    proposal = result.address_proposals[0]

    assert (hypothesis.char_start, hypothesis.char_end) == (6, 11)
    assert hypothesis.exact_normalized_mention == "ac/dc"
    assert hypothesis.fuzzy_lookup_normalization == "ac dc"
    assert hypothesis.unresolved_support_count == 1
    assert hypothesis.total_support_count == 4
    assert hypothesis.unresolved_probability_mass == pytest.approx(0.25)
    assert hypothesis.omitted_probability_mass == 0.0
    assert hypothesis.source_document_ids == ("doc:1", "doc:2", "doc:3")
    assert hypothesis.exact_subchannels == ("fuzzy_normalized_title_surface",)
    assert proposal.source_channels == ("title",)
    assert proposal.source_provenance == ("pack:1",)


def test_union_deduplicates_support_and_source_ids_across_generators() -> None:
    entity_id = canonical_entity_id("Alpha")
    index = FuzzyAddressIndex(
        (
            AddressSurfaceRecord(
                "Alpha",
                entity_id,
                "Alpha",
                support_count=2,
                source_document_count=2,
                source_document_ids=("doc:1", "doc:2"),
                support_provenance_ids=("obs:1", "obs:2"),
                source_channels=("title",),
                source_provenance=("pack:1",),
            ),
        )
    )
    exact = index.lookup("Alpha", FuzzyChannel.EXACT)
    char = index.lookup("Alpha", FuzzyChannel.CHAR_NGRAM)

    proposal = union_address_results((exact, char), address_cap=8).address_proposals[0]

    assert proposal.support_count == 2
    assert proposal.support_provenance_ids == ("obs:1", "obs:2")
    assert proposal.source_document_ids == ("doc:1", "doc:2")
    assert proposal.source_document_count == 2
    assert proposal.source_diversity == 1.0
    assert proposal.support_aggregation == "deduplicated_support_provenance_ids"
    assert proposal.channels == (FuzzyChannel.CHAR_NGRAM, FuzzyChannel.EXACT)
    assert proposal.exact_subchannels == ("fuzzy_normalized_title_surface",)


def test_serialization_is_deterministic_and_runtime_tables_are_verified() -> None:
    index = _index()
    first = index.to_bytes()
    second = FuzzyAddressIndex.from_bytes(first).to_bytes()

    assert first == second
    sizes = logical_index_bytes(index)
    assert sizes["serialized_json_bytes"] == len(first)
    assert 0 < sizes["serialized_gzip_bytes"] < sizes["serialized_json_bytes"]


def test_artifact_loader_rejects_corrupt_manifest_and_payload(tmp_path: Path) -> None:
    payload = tmp_path / "fuzzy-address.json.gz"
    manifest = tmp_path / "fuzzy-address.manifest.json"
    index = _index()
    index.write_artifact(payload, manifest)
    assert FuzzyAddressIndex.from_artifact(payload, manifest).to_bytes() == index.to_bytes()

    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["payload_json_sha256"] = "0" * 64
    manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(FuzzyAddressDataError, match="JSON hash"):
        FuzzyAddressIndex.from_artifact(payload, manifest)


def test_index_rejects_noncanonical_address_bands_and_bad_support() -> None:
    with pytest.raises(FuzzyAddressDataError, match="corpus entity IDs"):
        FuzzyAddressIndex((AddressSurfaceRecord("alias", "user:1", "Alias"),))
    with pytest.raises(FuzzyAddressDataError, match="support"):
        FuzzyAddressIndex(
            (AddressSurfaceRecord("alias", canonical_entity_id("Alias"), "Alias", 1, 2),)
        )


def test_registry_rejects_hash_title_mismatch_and_conflicting_titles() -> None:
    alpha_id: str = str(canonical_entity_id("Alpha"))
    with pytest.raises(FuzzyAddressDataError, match="does not match"):
        FuzzyAddressIndex((AddressSurfaceRecord("alias", alpha_id, "Beta"),))

    class _ConflictingRegistry:
        def entity_id_for_title(self, canonical_title: str) -> str:
            del canonical_title
            return alpha_id

    with pytest.raises(FuzzyAddressDataError, match="conflicting titles"):
        FuzzyAddressIndex(
            (
                AddressSurfaceRecord("alpha alias", alpha_id, "Alpha"),
                AddressSurfaceRecord("beta alias", alpha_id, "Beta"),
            ),
            registry=_ConflictingRegistry(),
        )
