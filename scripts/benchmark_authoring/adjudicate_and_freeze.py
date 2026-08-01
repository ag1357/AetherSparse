"""Independently adjudicate source evidence and freeze the v0.5 R1 benchmark."""

from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path
from typing import Any

from common import (
    ADJUDICATOR_IDENTITY,
    ADJUDICATOR_PROCESS,
    AUDITOR_IDENTITY,
    AUDITOR_PROCESS,
    AUTHOR_IDENTITIES,
    BENCHMARK_IDENTITY,
    DATE_RE,
    DEFINITION_RE,
    EVALUATOR_IDENTITY,
    EVALUATOR_PROCESS,
    QUANTITY_RE,
    QUOTATION_RE,
    REQUIRED_CATEGORIES,
    SCHEMA_VERSION,
    canonical_cases_payload,
    connect_read_only,
    corpus_identity,
    load_chunk,
    partition_for_case,
    partition_for_documents,
    read_json,
    sha256_file,
    sha256_text,
    stable_id,
    write_json,
)

ANSWER_INTENTS = {
    "extract_definition",
    "extract_quotation",
    "extract_date",
    "extract_quantity",
    "compare_quantities",
    "compose_definitions",
}

DISPOSITIONS = {
    "extract_definition": "ANSWER",
    "extract_quotation": "ANSWER",
    "extract_date": "ANSWER",
    "extract_quantity": "ANSWER",
    "compare_quantities": "ANSWER",
    "compose_definitions": "ANSWER",
    "reject_incorrect_premise": "INCORRECT_PREMISE",
    "request_clarification": "CLARIFY",
    "abstain_unknown_entity": "ABSTAIN",
    "abstain_missing_evidence": "ABSTAIN",
    "out_of_corpus": "OUT_OF_CORPUS",
}


def _expected_match(source_text: str, extractor: str) -> tuple[int, int]:
    pattern = {
        "definition": DEFINITION_RE,
        "date": DATE_RE,
        "quantity": QUANTITY_RE,
        "quotation": QUOTATION_RE,
    }.get(extractor)
    if pattern is None:
        raise ValueError(f"unsupported source extractor: {extractor}")
    match = pattern.search(source_text)
    if match is None:
        raise ValueError(f"adjudicator could not reproduce {extractor} candidate")
    if extractor == "definition":
        return match.start("answer"), match.end("answer")
    group = 1 if extractor == "quotation" else 0
    return match.start(group), match.end(group)


def _verify_candidate(connection: Any, candidate: dict[str, Any]) -> dict[str, Any]:
    row = load_chunk(connection, str(candidate["chunk_id"]))
    checks = {
        "document_id": str(row["document_id"]),
        "wiki_page_id": str(row["wiki_page_id"]),
        "revision_id": str(row["revision_id"]),
        "title": str(row["title"]),
        "source_url": str(row["source_url"]),
        "document_hash": str(row["source_text_sha256"]),
        "chunk_start": int(row["raw_start"]),
        "chunk_end": int(row["raw_end"]),
        "chunk_hash": str(row["source_span_sha256"]),
    }
    for key, expected in checks.items():
        if candidate.get(key) != expected:
            raise ValueError(f"candidate metadata mismatch for {key}: {candidate.get(key)!r}")
    raw_document = str(row["raw_wikitext"])
    raw_chunk = str(row["raw_text"])
    start = int(row["raw_start"])
    end = int(row["raw_end"])
    if raw_document[start:end] != raw_chunk:
        raise ValueError("chunk offsets do not reproduce exact source text")
    if sha256_text(raw_document) != str(row["source_text_sha256"]).removeprefix("sha256:"):
        raise ValueError("document SHA-256 does not match immutable source")
    if sha256_text(raw_chunk) != str(row["source_span_sha256"]).removeprefix("sha256:"):
        raise ValueError("chunk SHA-256 does not match exact source span")
    expected_start, expected_end = _expected_match(raw_chunk, str(candidate["extractor"]))
    if (
        int(candidate["candidate_start"]) != expected_start
        or int(candidate["candidate_end"]) != expected_end
    ):
        raise ValueError("candidate coordinates were not independently reproduced")
    answer_surface = raw_chunk[expected_start:expected_end].strip()
    if not answer_surface:
        raise ValueError("candidate answer surface is empty")
    span_id = stable_id("v050r1-span", row["document_id"], start, end)
    evidence = {
        "span_id": span_id,
        "document_id": str(row["document_id"]),
        "document_hash": f"sha256:{sha256_text(raw_document)}",
        "source_revision": str(row["revision_id"]),
        "source_url": str(row["source_url"]),
        "char_start": start,
        "char_end": end,
        "exact_text": raw_chunk,
        "exact_text_hash": f"sha256:{sha256_text(raw_chunk)}",
    }
    return {
        "evidence": evidence,
        "answer_surface": answer_surface,
        "title": str(row["title"]),
        "document_id": str(row["document_id"]),
        "quantity": _quantity_value(answer_surface),
    }


def _quantity_value(surface: str) -> float | None:
    # QUANTITY_RE ends in a word boundary, which deliberately accepts a percent
    # token in surrounding prose but cannot full-match an isolated trailing "%".
    # Reparse the independently copied value without trusting author metadata.
    match = re.fullmatch(r"(?P<value>\d+(?:[.,]\d+)?)\s*\D+", surface)
    if match is None:
        return None
    return float(match.group("value").replace(",", ""))


def _case_id(draft_id: str) -> str:
    return "v050r1-case:" + draft_id.rsplit(":", maxsplit=1)[-1]


def _adjudicate_case(
    connection: Any,
    draft: dict[str, Any],
    prior_ids: dict[str, str],
) -> dict[str, Any]:
    intent = str(draft["intent"])
    if intent not in DISPOSITIONS:
        raise ValueError(f"unknown adjudication intent: {intent}")
    verified = [
        _verify_candidate(connection, item) for item in draft["source_candidates"]
    ]
    disposition = DISPOSITIONS[intent]
    accepted_answers: list[str] = []
    if intent in {
        "extract_definition",
        "extract_quotation",
        "extract_date",
        "extract_quantity",
    }:
        if len(verified) != 1:
            raise ValueError(f"{intent} requires exactly one source")
        accepted_answers = [str(verified[0]["answer_surface"])]
    elif intent == "compose_definitions":
        if len(verified) < 2 or len(verified) > 6:
            raise ValueError("composition requires two through six exact sources")
        accepted_answers = [
            "; ".join(str(item["answer_surface"]) for item in verified)
        ]
    elif intent == "compare_quantities":
        if len(verified) != 2 or any(item["quantity"] is None for item in verified):
            raise ValueError("comparison requires two independently parsed quantities")
        left, right = verified
        if left["quantity"] == right["quantity"]:
            raise ValueError("comparison operands unexpectedly tie")
        operator = ">" if float(left["quantity"]) > float(right["quantity"]) else "<"
        accepted_answers = [
            f"{left['answer_surface']} compared with {right['answer_surface']}.",
            f"{left['answer_surface']} {operator} {right['answer_surface']}.",
        ]
    if disposition == "ANSWER" and not accepted_answers:
        raise ValueError("answer disposition lacks an adjudicated accepted answer")

    evidence = [dict(item["evidence"]) for item in verified]
    document_ids = [str(item["document_id"]) for item in verified]
    case_id = _case_id(str(draft["draft_id"]))
    partition = (
        partition_for_documents(document_ids)
        if document_ids
        else partition_for_case(case_id)
    )
    claims = [
        stable_id("v050r1-claim", case_id, item["span_id"], ordinal)
        for ordinal, item in enumerate(evidence)
    ]
    return {
        "case_id": case_id,
        "partition": partition,
        "question": str(draft["question"]),
        "categories": [str(draft["category"])],
        "author_identity": str(draft["author_identity"]),
        "adjudicator_identity": ADJUDICATOR_IDENTITY,
        "accepted_disposition": disposition,
        "accepted_answers": accepted_answers,
        "required_entity_ids": [str(item) for item in draft["required_entity_ids"]],
        "required_answer_shape": str(draft["required_answer_shape"]),
        "required_facets": [str(item) for item in draft["required_facets"]],
        "gold_claim_ids": claims,
        "gold_evidence": evidence,
        "prior_case_ids": [prior_ids[str(item)] for item in draft["prior_draft_ids"]],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--draft", type=Path, action="append", required=True)
    parser.add_argument("--benchmark-output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    parser.add_argument("--source-map-output", type=Path, required=True)
    args = parser.parse_args()
    shard_payloads = [read_json(path) for path in args.draft]
    expected_authors = {identity for identity, _ in AUTHOR_IDENTITIES.values()}
    supplied_authors = {
        str(payload["author_role"]["identity"]) for payload in shard_payloads
    }
    if supplied_authors != expected_authors:
        raise ValueError("adjudication requires all three registered author shards")
    drafts = [draft for payload in shard_payloads for draft in payload["drafts"]]
    prior_ids = {str(item["draft_id"]): _case_id(str(item["draft_id"])) for item in drafts}

    connection = connect_read_only(args.corpus)
    rejected: list[dict[str, str]] = []
    cases: list[dict[str, Any]] = []
    seen_questions: set[str] = set()
    try:
        source_identity = corpus_identity(connection)
        for draft in sorted(drafts, key=lambda item: str(item["draft_id"])):
            normalized_question = " ".join(str(draft["question"]).casefold().split())
            if normalized_question in seen_questions:
                rejected.append(
                    {
                        "draft_id": str(draft["draft_id"]),
                        "reason": "duplicate_normalized_question",
                    }
                )
                continue
            try:
                case = _adjudicate_case(connection, draft, prior_ids)
            except (KeyError, TypeError, ValueError) as error:
                rejected.append(
                    {"draft_id": str(draft["draft_id"]), "reason": str(error)}
                )
                continue
            cases.append(case)
            seen_questions.add(normalized_question)
    finally:
        connection.close()

    categories = {category for case in cases for category in case["categories"]}
    missing = REQUIRED_CATEGORIES - categories
    if len(cases) < 2_000:
        raise ValueError(f"only {len(cases)} adjudicated cases; 2,000 required")
    if missing:
        raise ValueError(f"frozen benchmark lacks categories: {sorted(missing)}")
    content_sha256 = sha256_text(canonical_cases_payload(cases))
    author_roles = [
        {
            "identity": identity,
            "role": "independent_question_author",
            "process_identity": process_identity,
            "runtime_access": False,
        }
        for identity, process_identity in AUTHOR_IDENTITIES.values()
    ]
    benchmark = {
        "benchmark_identity": BENCHMARK_IDENTITY,
        "schema_version": SCHEMA_VERSION,
        "author_roles": author_roles,
        "adjudicator_role": {
            "identity": ADJUDICATOR_IDENTITY,
            "role": "source_evidence_adjudicator",
            "process_identity": ADJUDICATOR_PROCESS,
            "runtime_access": False,
        },
        "evaluator_role": {
            "identity": EVALUATOR_IDENTITY,
            "role": "blind_runtime_evaluator",
            "process_identity": EVALUATOR_PROCESS,
            "runtime_access": True,
        },
        "auditor_role": {
            "identity": AUDITOR_IDENTITY,
            "role": "independent_provenance_auditor",
            "process_identity": AUDITOR_PROCESS,
            "runtime_access": False,
        },
        "cases": cases,
        "content_sha256": content_sha256,
    }
    write_json(args.benchmark_output, benchmark)

    source_map: dict[str, dict[str, str]] = {}
    for case in cases:
        for evidence in case["gold_evidence"]:
            source_map[str(evidence["document_id"])] = {
                "source_revision": str(evidence["source_revision"]),
                "source_url": str(evidence["source_url"]),
                "document_hash": str(evidence["document_hash"]),
            }
    write_json(
        args.source_map_output,
        {
            "benchmark_identity": BENCHMARK_IDENTITY,
            "source_document_count": len(source_map),
            "documents": source_map,
        },
    )
    counts = Counter(case["categories"][0] for case in cases)
    write_json(
        args.manifest_output,
        {
            "benchmark_identity": BENCHMARK_IDENTITY,
            "benchmark_schema_version": SCHEMA_VERSION,
            "historical_identity_reused": False,
            "lost_v041_r4_claimed": False,
            "source_corpus": source_identity,
            "source_pack_filename": args.corpus.name,
            "source_pack_sha256": sha256_file(args.corpus),
            "case_count": len(cases),
            "category_counts": dict(sorted(counts.items())),
            "partition_counts": dict(
                sorted(Counter(case["partition"] for case in cases).items())
            ),
            "source_document_count": len(source_map),
            "evidence_span_count": sum(len(case["gold_evidence"]) for case in cases),
            "content_sha256": content_sha256,
            "rejected_draft_count": len(rejected),
            "rejected_drafts": rejected,
            "role_separation": {
                "author_processes": [item["process_identity"] for item in author_roles],
                "adjudicator_process": ADJUDICATOR_PROCESS,
                "evaluator_process": EVALUATOR_PROCESS,
                "auditor_process": AUDITOR_PROCESS,
                "authors_received_runtime_outputs": False,
            },
        },
    )


if __name__ == "__main__":
    main()
