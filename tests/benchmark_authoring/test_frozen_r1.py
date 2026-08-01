from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_PATH = (
    ROOT / "data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json"
)
MANIFEST_PATH = (
    ROOT / "data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.manifest.json"
)
SOURCE_MAP_PATH = (
    ROOT / "data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.source-map.json"
)
BLIND_PATH = (
    ROOT / "data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.blind-input.json"
)
AUDIT_PATH = (
    ROOT
    / "reports/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1_PROVENANCE_AUDIT.json"
)
ROLES_PATH = ROOT / "data/v050/benchmark/roles-and-invocations.json"
REQUIRED_CATEGORIES = {
    "direct_fact",
    "alias",
    "redirect",
    "misspelling",
    "quotation",
    "date",
    "quantity",
    "incorrect_premise",
    "comparison",
    "two_source",
    "three_to_six_source",
    "ambiguous_entity",
    "unknown_entity",
    "out_of_corpus",
    "pronoun",
    "follow_up",
    "incomplete",
    "clarification",
    "abstention",
}
DEFINITION_SUBJECT_RE = re.compile(
    r"'''(?P<subject>[^'\n]{1,100})'''\s+(?:is|are|was|were)\s+"
    r"(?P<answer>[^\n.]{15,260})\.",
    re.IGNORECASE,
)


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value.replace("_", " "))
    return " ".join(normalized.strip().split()).casefold()


def test_frozen_benchmark_has_full_independent_contract() -> None:
    benchmark = _read(BENCHMARK_PATH)
    cases = benchmark["cases"]
    assert benchmark["benchmark_identity"] == "INDEPENDENT_NATURAL_QUERY_SET_V050_R1"
    assert len(cases) == 2_050
    assert {category for case in cases for category in case["categories"]} == (
        REQUIRED_CATEGORIES
    )
    assert len({case["case_id"] for case in cases}) == len(cases)
    assert len({" ".join(case["question"].casefold().split()) for case in cases}) == len(
        cases
    )
    authors = benchmark["author_roles"]
    assert len(authors) == 3
    assert not any(role["runtime_access"] for role in authors)
    assert {case["author_identity"] for case in cases} == {
        role["identity"] for role in authors
    }
    roles = [
        *authors,
        benchmark["adjudicator_role"],
        benchmark["evaluator_role"],
        benchmark["auditor_role"],
    ]
    assert len({role["identity"] for role in roles}) == len(roles)
    assert len({role["process_identity"] for role in roles}) == len(roles)


def test_case_content_and_exact_span_hashes_are_frozen() -> None:
    benchmark = _read(BENCHMARK_PATH)
    cases = benchmark["cases"]
    canonical = json.dumps(
        sorted(cases, key=lambda item: item["case_id"]),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    assert _sha256_text(canonical) == benchmark["content_sha256"]
    for case in cases:
        for evidence in case["gold_evidence"]:
            assert _sha256_text(evidence["exact_text"]) == evidence[
                "exact_text_hash"
            ].removeprefix("sha256:")
            assert evidence["char_end"] > evidence["char_start"]
        if case["accepted_disposition"] == "ANSWER":
            assert case["accepted_answers"]
            assert case["gold_evidence"]
        else:
            assert not case["accepted_answers"]


def test_direct_fact_gold_defines_the_question_subject() -> None:
    benchmark = _read(BENCHMARK_PATH)
    direct_cases = [
        case for case in benchmark["cases"] if case["categories"] == ["direct_fact"]
    ]
    assert len(direct_cases) == 220
    for case in direct_cases:
        title = _normalize(case["question"].removeprefix("What is ").removesuffix("?"))
        matching_subjects = [
            _normalize(match.group("subject"))
            for match in DEFINITION_SUBJECT_RE.finditer(
                case["gold_evidence"][0]["exact_text"]
            )
            if match.group("answer").strip() in case["accepted_answers"]
        ]
        assert any(
            subject == title or title.startswith(f"{subject} (")
            for subject in matching_subjects
        )


def test_tuning_and_evaluation_articles_are_disjoint() -> None:
    benchmark = _read(BENCHMARK_PATH)
    articles: dict[str, set[str]] = {
        "tuning": set(),
        "development": set(),
        "evaluation": set(),
        "final_held": set(),
    }
    for case in benchmark["cases"]:
        articles[case["partition"]].update(
            evidence["document_id"] for evidence in case["gold_evidence"]
        )
    tuning = articles["tuning"] | articles["development"]
    evaluation = articles["evaluation"] | articles["final_held"]
    assert tuning.isdisjoint(evaluation)


def test_source_map_and_audit_cover_all_evidence() -> None:
    benchmark = _read(BENCHMARK_PATH)
    source_map = _read(SOURCE_MAP_PATH)
    evidence = [
        item for case in benchmark["cases"] for item in case["gold_evidence"]
    ]
    assert len(evidence) == 1_770
    assert {item["document_id"] for item in evidence} == set(source_map["documents"])
    for item in evidence:
        metadata = source_map["documents"][item["document_id"]]
        assert metadata["source_revision"] == item["source_revision"]
        assert metadata["source_url"] == item["source_url"]
        assert metadata["document_hash"] == item["document_hash"]
    audit = _read(AUDIT_PATH)
    assert audit["passed"] is True
    assert audit["exact_source_binding_reproducible"] is True
    assert audit["answer_surfaces_bound"] is True
    assert audit["tuning_evaluation_article_overlap"] == []
    assert audit["evidence_violations"] == []


def test_blind_runtime_input_contains_no_gold() -> None:
    blind = _read(BLIND_PATH)
    benchmark = _read(BENCHMARK_PATH)
    forbidden = {
        "accepted_disposition",
        "accepted_answers",
        "required_entity_ids",
        "required_answer_shape",
        "required_facets",
        "gold_claim_ids",
        "gold_evidence",
    }
    assert len(blind["cases"]) == len(benchmark["cases"])
    assert not any(forbidden & set(case) for case in blind["cases"])
    assert blind["gold_fields_exposed"] == []


def test_manifest_does_not_claim_lost_r4_identity() -> None:
    manifest = _read(MANIFEST_PATH)
    assert manifest["historical_identity_reused"] is False
    assert manifest["lost_v041_r4_claimed"] is False
    assert manifest["source_pack_sha256"] == (
        "cb93db732eaf314806700b38ef7ec9d5cf85dea69f32deb51f97a3aa890023e5"
    )
    assert manifest["case_count"] == 2_050


def test_recorded_output_hashes_match_committed_bytes() -> None:
    roles = _read(ROLES_PATH)
    outputs = roles["frozen_outputs"]
    assert outputs["benchmark_sha256"] == _sha256_file(BENCHMARK_PATH)
    assert outputs["blind_input_sha256"] == _sha256_file(BLIND_PATH)
    assert outputs["manifest_sha256"] == _sha256_file(MANIFEST_PATH)
    assert outputs["source_map_sha256"] == _sha256_file(SOURCE_MAP_PATH)
    assert outputs["provenance_audit_sha256"] == _sha256_file(AUDIT_PATH)
