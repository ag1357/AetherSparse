from __future__ import annotations

from aethersparse.autonomy.learned import (
    AliasExample,
    ContradictionProbe,
    EntityAliasLinker,
    EvidenceCandidate,
    EvidenceExample,
    EvidenceGapProbe,
    EvidenceReranker,
    ProbeExample,
    QuantizedLinearArtifact,
    QueryFrameParser,
    TextExample,
)


def parser_examples() -> list[TextExample]:
    return [
        TextExample("Who was on the Apollo crew?", "fact"),
        TextExample("Name the lunar mission astronaut.", "fact"),
        TextExample("Which person piloted Eagle?", "fact"),
        TextExample("When did Apollo land?", "temporal"),
        TextExample("What date was the lunar touchdown?", "temporal"),
        TextExample("Which day did the mission launch?", "temporal"),
        TextExample("Bake a chocolate cake.", "unknown"),
        TextExample("Play some jazz music.", "unknown"),
        TextExample("Will it rain tomorrow?", "unknown"),
    ]


def test_parser_is_deterministic_and_copies_unknown_utf8_span() -> None:
    first = QueryFrameParser(
        ("fact", "temporal", "unknown"),
        known_terms=("Apollo", "Eagle"),
        feature_dim=256,
    )
    second = QueryFrameParser(
        ("fact", "temporal", "unknown"),
        known_terms=("Apollo", "Eagle"),
        feature_dim=256,
    )
    first.fit(parser_examples())
    second.fit(parser_examples())

    held_out = first.predict("What date did Apollo touch down near ZXQ-91?")
    assert held_out.frame_label == "temporal"
    assert held_out.unknown_spans[0].surface == "ZXQ-91"
    assert held_out.unknown_spans[0].char_start == 37
    assert held_out.unknown_spans[0].char_end == 43

    first_export = first.export_int8()
    second_export = second.export_int8()
    assert first_export.to_json() == second_export.to_json()
    assert first_export.training_cache_hash == second_export.training_cache_hash
    assert first_export.artifact_hash == second_export.artifact_hash
    assert first_export.parameter_count == 3 * (256 + 1)


def test_quantized_export_round_trip_and_profile_counts() -> None:
    parser = QueryFrameParser(("fact", "temporal", "unknown"), feature_dim=128)
    parser.fit(parser_examples())
    artifact = parser.export_int8()

    restored = QuantizedLinearArtifact.from_json(artifact.to_json())
    restored_prediction = restored.predict_text("What date did Apollo touch down?")
    assert restored_prediction.label == parser.predict(
        "What date did Apollo touch down?"
    ).frame_label

    profile = parser.profile("What date did Apollo touch down?")
    assert profile.parameter_count == 3 * (128 + 1)
    assert profile.float_parameter_bytes == profile.parameter_count * 4
    assert profile.dense_worst_case_macs == 3 * 128
    assert profile.active_macs == profile.active_feature_count * 3
    assert profile.quantized_parameter_bytes == 3 * 128 + 3 * 4 + 3 * 4
    assert profile.measured_python_peak_working_ram_bytes > 0


def test_entity_alias_linker_uses_learned_fallback_then_copies_unknown() -> None:
    linker = EntityAliasLinker(
        ("entity:moon", "entity:mars"),
        learned_threshold=0.65,
        feature_dim=256,
    )
    linker.fit(
        [
            AliasExample("Moon", "entity:moon"),
            AliasExample("Luna", "entity:moon"),
            AliasExample("lunar surface", "entity:moon"),
            AliasExample("Earth's satellite", "entity:moon"),
            AliasExample("Mars", "entity:mars"),
            AliasExample("red planet", "entity:mars"),
            AliasExample("Martian surface", "entity:mars"),
            AliasExample("fourth planet", "entity:mars"),
        ]
    )

    learned = linker.link("lunar world")
    assert learned.entity_id == "entity:moon"
    assert learned.method == "learned_fallback"

    source = "Ask about Δ-ZXQ-91 next."
    unknown = linker.link("ZXQ-91", source_text=source, char_start=12)
    assert unknown.entity_id is None
    assert unknown.method == "unknown_copy"
    assert unknown.unknown_span is not None
    assert unknown.unknown_span.surface == "ZXQ-91"
    assert unknown.unknown_span.byte_start == len(source[:12].encode("utf-8"))
    assert unknown.unknown_span.byte_end == len(source[:18].encode("utf-8"))


def test_evidence_reranker_orders_held_out_relevant_evidence() -> None:
    reranker = EvidenceReranker(feature_dim=256)
    reranker.fit(
        [
            EvidenceExample(
                "When did Apollo land?",
                "Apollo made its lunar landing on July 20, 1969.",
                True,
            ),
            EvidenceExample(
                "What date was lunar touchdown?",
                "The lunar module touched down on July 20, 1969.",
                True,
            ),
            EvidenceExample(
                "Who piloted Eagle?",
                "Armstrong served as commander and piloted Eagle.",
                True,
            ),
            EvidenceExample(
                "When did Apollo land?",
                "Chocolate cake is baked at 180 degrees.",
                False,
            ),
            EvidenceExample(
                "What date was lunar touchdown?",
                "Jazz uses syncopation and improvisation.",
                False,
            ),
            EvidenceExample(
                "Who piloted Eagle?",
                "Rain is expected late tomorrow.",
                False,
            ),
        ]
    )

    ranked = reranker.rank(
        "Which date did the Apollo lunar landing happen?",
        (
            EvidenceCandidate("irrelevant", "A cake recipe needs flour and eggs."),
            EvidenceCandidate(
                "relevant",
                "Apollo completed the lunar landing on July 20, 1969.",
            ),
        ),
    )
    assert ranked[0].candidate_id == "relevant"
    assert ranked[0].score > ranked[1].score


def test_independent_contradiction_and_gap_probes_have_separate_artifacts() -> None:
    contradiction = ContradictionProbe(feature_dim=256)
    contradiction.fit(
        [
            ProbeExample(
                "Apollo landed in 1969.",
                "Apollo did not land in 1969.",
                True,
            ),
            ProbeExample(
                "The tank held 10 liters.",
                "The tank held 12 liters.",
                True,
            ),
            ProbeExample(
                "Apollo landed in 1969.",
                "The lunar landing happened during 1969.",
                False,
            ),
            ProbeExample(
                "The tank held 10 liters.",
                "Its capacity was ten liters.",
                False,
            ),
        ]
    )
    gap = EvidenceGapProbe(feature_dim=256)
    gap.fit(
        [
            ProbeExample(
                "When did Apollo land?",
                "The evidence discusses cake recipes.",
                True,
            ),
            ProbeExample(
                "Who piloted Eagle?",
                "The evidence only gives the launch date.",
                True,
            ),
            ProbeExample(
                "When did Apollo land?",
                "Apollo landed on July 20, 1969.",
                False,
            ),
            ProbeExample(
                "Who piloted Eagle?",
                "Armstrong piloted the lunar module Eagle.",
                False,
            ),
        ]
    )

    assert contradiction.predict(
        "The sample measured 8 kilograms.",
        "The sample did not measure 8 kilograms.",
    ).detected
    assert gap.predict(
        "When did Apollo land?",
        "This paragraph describes a jazz concert.",
    ).detected
    assert contradiction.export_int8().component_kind == "contradiction_probe"
    assert gap.export_int8().component_kind == "evidence_gap_probe"
    assert contradiction.export_int8().artifact_hash != gap.export_int8().artifact_hash
