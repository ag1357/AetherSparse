"""Independently audit the frozen natural-query set against immutable SQLite."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from common import (
    AUDITOR_IDENTITY,
    AUDITOR_PROCESS,
    BENCHMARK_IDENTITY,
    DEFINITION_RE,
    QUANTITY_RE,
    REQUIRED_CATEGORIES,
    canonical_cases_payload,
    connect_read_only,
    read_json,
    sha256_file,
    sha256_text,
    write_json,
)


def _audit_answer_binding(case: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    case_id = str(case["case_id"])
    answers = [str(item) for item in case["accepted_answers"]]
    evidence_texts = [str(item["exact_text"]) for item in case["gold_evidence"]]
    if case["accepted_disposition"] != "ANSWER":
        if answers:
            violations.append(f"{case_id}:non_answer_has_accepted_text")
        return violations
    if not answers or not evidence_texts:
        return [f"{case_id}:answer_lacks_text_or_evidence"]
    category = str(case["categories"][0])
    if category in {"two_source", "three_to_six_source"}:
        for evidence in evidence_texts:
            match = DEFINITION_RE.search(evidence)
            if match is None or match.group("answer").strip() not in answers[0]:
                violations.append(f"{case_id}:composition_surface_not_copied")
    elif category == "comparison":
        quantities: list[str] = []
        for evidence in evidence_texts:
            match = QUANTITY_RE.search(evidence)
            if match is None:
                violations.append(f"{case_id}:comparison_quantity_missing")
            else:
                quantities.append(match.group(0))
        if len(answers) != 2:
            violations.append(f"{case_id}:comparison_variants_missing")
        for answer in answers:
            if any(quantity not in answer for quantity in quantities):
                violations.append(f"{case_id}:comparison_surface_not_copied")
    elif not any(answer in evidence for answer in answers for evidence in evidence_texts):
        violations.append(f"{case_id}:accepted_answer_not_exactly_copied")
    return violations


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--blind-input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    benchmark = read_json(args.benchmark)
    manifest = read_json(args.manifest)
    blind_input = read_json(args.blind_input)
    cases = list(benchmark["cases"])
    role_violations: list[str] = []
    evidence_violations: list[str] = []
    answer_binding_violations: list[str] = []

    roles = [
        *benchmark["author_roles"],
        benchmark["adjudicator_role"],
        benchmark["evaluator_role"],
        benchmark["auditor_role"],
    ]
    identities = [str(item["identity"]) for item in roles]
    process_ids = [str(item["process_identity"]) for item in roles]
    if len(identities) != len(set(identities)):
        role_violations.append("role_identity_collision")
    if len(process_ids) != len(set(process_ids)):
        role_violations.append("role_process_collision")
    if any(bool(item["runtime_access"]) for item in benchmark["author_roles"]):
        role_violations.append("author_runtime_access")
    if len({str(case["author_identity"]) for case in cases}) < 3:
        role_violations.append("fewer_than_three_used_author_processes")
    if benchmark["auditor_role"]["identity"] != AUDITOR_IDENTITY:
        role_violations.append("unexpected_auditor_identity")
    if benchmark["auditor_role"]["process_identity"] != AUDITOR_PROCESS:
        role_violations.append("unexpected_auditor_process")

    case_ids = [str(case["case_id"]) for case in cases]
    normalized_questions = [" ".join(str(case["question"]).casefold().split()) for case in cases]
    structural_violations: list[str] = []
    if len(case_ids) != len(set(case_ids)):
        structural_violations.append("duplicate_case_id")
    if len(normalized_questions) != len(set(normalized_questions)):
        structural_violations.append("duplicate_normalized_question")
    case_id_set = set(case_ids)
    for case in cases:
        if any(str(prior) not in case_id_set for prior in case["prior_case_ids"]):
            structural_violations.append(f"{case['case_id']}:unknown_prior_case")
    categories = {category for case in cases for category in case["categories"]}
    missing_categories = sorted(REQUIRED_CATEGORIES - categories)
    if missing_categories:
        structural_violations.append("missing_required_categories")
    expected_hash = sha256_text(canonical_cases_payload(cases))
    if benchmark["content_sha256"] != expected_hash:
        structural_violations.append("benchmark_content_hash_mismatch")
    if len(cases) < 2_000:
        structural_violations.append("case_count_below_2000")

    connection = connect_read_only(args.corpus)
    source_cache: dict[str, Any] = {}
    try:
        for case in cases:
            for evidence in case["gold_evidence"]:
                document_id = str(evidence["document_id"])
                row = source_cache.get(document_id)
                if row is None:
                    row = connection.execute(
                        """SELECT document_id,revision_id,source_url,source_text_sha256,
                                  raw_wikitext
                             FROM documents WHERE document_id=?""",
                        (document_id,),
                    ).fetchone()
                    source_cache[document_id] = row
                span_id = str(evidence["span_id"])
                if row is None:
                    evidence_violations.append(f"{span_id}:missing_document")
                    continue
                raw = str(row["raw_wikitext"])
                start = int(evidence["char_start"])
                end = int(evidence["char_end"])
                copied = raw[start:end]
                if (
                    str(row["revision_id"]) != evidence["source_revision"]
                    or str(row["source_url"]) != evidence["source_url"]
                    or sha256_text(raw)
                    != str(evidence["document_hash"]).removeprefix("sha256:")
                    or copied != evidence["exact_text"]
                    or sha256_text(copied)
                    != str(evidence["exact_text_hash"]).removeprefix("sha256:")
                ):
                    evidence_violations.append(f"{span_id}:binding_mismatch")
            answer_binding_violations.extend(_audit_answer_binding(case))
    finally:
        connection.close()

    articles_by_partition: dict[str, set[str]] = defaultdict(set)
    for case in cases:
        articles_by_partition[str(case["partition"])].update(
            str(item["document_id"]) for item in case["gold_evidence"]
        )
    tuning = articles_by_partition["tuning"] | articles_by_partition["development"]
    evaluation = articles_by_partition["evaluation"] | articles_by_partition["final_held"]
    overlap = sorted(tuning & evaluation)
    if overlap:
        structural_violations.append("tuning_evaluation_document_overlap")

    forbidden_blind_fields = {
        "accepted_disposition",
        "accepted_answers",
        "required_entity_ids",
        "required_answer_shape",
        "required_facets",
        "gold_claim_ids",
        "gold_evidence",
    }
    exposed = sorted(
        forbidden_blind_fields
        & {key for item in blind_input["cases"] for key in item}
    )
    if exposed:
        structural_violations.append("blind_input_exposes_gold")
    if len(blind_input["cases"]) != len(cases):
        structural_violations.append("blind_input_case_count_mismatch")
    corpus_hash = sha256_file(args.corpus)
    if corpus_hash != manifest["source_pack_sha256"]:
        structural_violations.append("source_pack_hash_mismatch")

    category_counts = Counter(str(case["categories"][0]) for case in cases)
    partition_counts = Counter(str(case["partition"]) for case in cases)
    passed = not (
        role_violations
        or structural_violations
        or evidence_violations
        or answer_binding_violations
    )
    write_json(
        args.output,
        {
            "benchmark_identity": BENCHMARK_IDENTITY,
            "auditor_role": {
                "identity": AUDITOR_IDENTITY,
                "process_identity": AUDITOR_PROCESS,
                "runtime_access": False,
            },
            "passed": passed,
            "case_count": len(cases),
            "category_counts": dict(sorted(category_counts.items())),
            "partition_counts": dict(sorted(partition_counts.items())),
            "checked_categories": sorted(categories),
            "missing_categories": missing_categories,
            "source_document_count": len(source_cache),
            "evidence_span_count": sum(len(case["gold_evidence"]) for case in cases),
            "exact_source_binding_reproducible": not evidence_violations,
            "answer_surfaces_bound": not answer_binding_violations,
            "tuning_evaluation_article_overlap": overlap,
            "role_violations": role_violations,
            "structural_violations": structural_violations,
            "evidence_violations": evidence_violations,
            "answer_binding_violations": answer_binding_violations,
            "blind_input_exposed_gold_fields": exposed,
            "benchmark_content_sha256": expected_hash,
            "source_pack_sha256": corpus_hash,
        },
    )
    if not passed:
        raise SystemExit("provenance audit failed; inspect the emitted report")


if __name__ == "__main__":
    main()
