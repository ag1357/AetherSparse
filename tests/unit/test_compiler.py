from __future__ import annotations

import json
from pathlib import Path

import pytest

from aethersparse.compiler import CompilationError, compile_pack
from aethersparse.models import PacketStatus


def test_compilation_is_reproducible_and_quarantine_is_excluded() -> None:
    first = compile_pack(output_file=None)
    second = compile_pack(output_file=None)

    assert first.manifest.manifest_hash == second.manifest.manifest_hash
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.manifest.packet_count == 6
    assert all(packet.header.status is PacketStatus.CANONICAL for packet in first.packets)
    assert all("teacher_candidate" not in packet.header.packet_id for packet in first.packets)


def test_every_atomic_claim_maps_to_an_exact_hashed_span() -> None:
    pack = compile_pack(output_file=None)
    spans = {span.source_span_id: span for span in pack.source_spans}

    for packet in pack.packets:
        for claim in packet.atomic_claims:
            assert claim.aligned_span_ids
            for span_id in claim.aligned_span_ids:
                assert span_id in spans
                assert spans[span_id].text
                assert spans[span_id].text_hash.startswith("sha256:")


def test_bad_alignment_stops_compilation(tmp_path: Path) -> None:
    original = json.loads(
        Path("data/gold_packets/tier1_reviewed.json").read_text(encoding="utf-8")
    )
    original["packets"][0]["evidence_text"] = "This sentence is not in the source."
    bad_gold = tmp_path / "bad_gold.json"
    bad_gold.write_text(json.dumps(original), encoding="utf-8")

    with pytest.raises(CompilationError, match="exact substring"):
        compile_pack(gold_file=bad_gold, output_file=None)


def test_teacher_candidate_cannot_self_promote(tmp_path: Path) -> None:
    original = json.loads(
        Path("data/gold_packets/tier1_reviewed.json").read_text(encoding="utf-8")
    )
    candidate = original["packets"][-1]
    candidate["status"] = "CANONICAL"
    candidate["tier"] = 1
    bad_gold = tmp_path / "teacher_promoted.json"
    bad_gold.write_text(json.dumps(original), encoding="utf-8")

    with pytest.raises(CompilationError, match="teacher candidates"):
        compile_pack(gold_file=bad_gold, output_file=None)

