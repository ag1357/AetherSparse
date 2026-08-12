from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aethersparse.controller.micro_ops import MicroAction, execute_action, state_from_replay
from aethersparse.controller.reachability import qualify_reachability
from aethersparse.controller.replay import (
    export_replay_bundle,
    load_replay_bundle,
    verify_replay_bundle,
)
from aethersparse.controller.search import (
    SearchConfig,
    candidate_set_oracle,
    posthoc_reachable,
    search,
)


def _trace_payload(partition: str = "evaluation") -> dict[str, object]:
    text = "Ada Lovelace was born in 1815."
    frame = {
        "normalized_query": "when was ada lovelace born?",
        "entity_mentions": [],
        "candidate_entity_ids": ["entity:ada"],
        "requested_relation_families": ["birth"],
        "answer_shape": "date",
        "required_facets": ["subject", "relation", "time", "source"],
        "temporal_constraints": [],
        "location_constraints": [],
        "attribution_constraints": [],
        "comparison_targets": [],
        "premise_claims": [],
        "discourse_references": [],
        "uncertainty": 0.0,
        "clarification_need": False,
    }
    claims = [
        {
            "claim_id": "claim:wrong",
            "subject_entity_id": "entity:ada",
            "relation_family": "birth",
            "object_value": "1915",
            "answer_shape": "date",
            "source_span_ids": ["span:wrong"],
        },
        {
            "claim_id": "claim:birth",
            "subject_entity_id": "entity:ada",
            "relation_family": "birth",
            "object_value": "1815",
            "answer_shape": "date",
            "source_span_ids": ["span:birth"],
        },
    ]
    spans = [
        {
            "span_id": "span:wrong",
            "document_id": "doc:wrong",
            "source_title": "Distractor",
            "source_revision": "1",
            "source_url": "https://example.test/wrong",
            "source_family": "fixture",
            "source_class": "CORPUS",
            "char_start": 0,
            "char_end": 4,
            "text": "1915",
            "text_hash": "sha256:" + hashlib.sha256(b"1915").hexdigest(),
            "unused_article_text": "must be pruned",
        },
        {
            "span_id": "span:birth",
            "document_id": "doc:ada",
            "source_title": "Ada Lovelace",
            "source_revision": "1",
            "source_url": "https://example.test/ada",
            "source_family": "fixture",
            "source_class": "CORPUS",
            "char_start": 0,
            "char_end": len(text),
            "text": text,
            "text_hash": "sha256:" + hashlib.sha256(text.encode()).hexdigest(),
        },
    ]
    state = {
        "accepted_disposition": "ANSWER",
        "query_frame": frame,
        "structured_claims": claims,
        "source_spans": spans,
        "missing_facets": [],
    }
    return {
        "case_id": "case:ada",
        "partition": partition,
        "training_eligible": True,
        "outcome": "incorrect",
        "records": [
            {
                "case_id": "case:ada",
                "step_index": 0,
                "state_before": state,
                "legal_actions": [5, 6],
                "action_taken": 6,
                "arguments": {"claims": 2},
                "result": {"selected_claim_ids": ["claim:wrong"]},
                "state_after": state,
                "block_reads": 0,
                "wall_us": 1,
                "terminal": None,
            }
        ],
        "total_block_reads": 0,
        "total_steps": 1,
        "max_step_block_reads": 0,
        "wall_us": 1,
    }


def _write_trace(path: Path, partition: str = "evaluation") -> None:
    path.write_text(json.dumps(_trace_payload(partition), sort_keys=True) + "\n", encoding="utf-8")


def test_replay_export_is_deterministic_and_protects_evaluation(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    _write_trace(trace)
    first = export_replay_bundle((trace,), tmp_path / "first", corpus_tier="10k")
    second = export_replay_bundle((trace,), tmp_path / "second", corpus_tier="10k")

    assert first == second
    assert (tmp_path / "first" / "cases.jsonl.gz").read_bytes() == (
        tmp_path / "second" / "cases.jsonl.gz"
    ).read_bytes()
    manifest, cases = load_replay_bundle(tmp_path / "first")
    assert manifest.case_count == 1
    assert manifest.training_case_count == 0
    assert cases[0].training_eligible is False
    assert cases[0].replay_complete is True
    assert "unused_article_text" not in cases[0].decisions[0].source_spans[0]


def test_replay_integrity_detects_tampering(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    _write_trace(trace, partition="development")
    bundle = tmp_path / "bundle"
    export_replay_bundle((trace,), bundle, corpus_tier="25k")
    with (bundle / "cases.jsonl.gz").open("ab") as handle:
        handle.write(b"tamper")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_replay_bundle(bundle)


def test_micro_ops_and_gold_blind_search_never_invent_values(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    _write_trace(trace)
    bundle = tmp_path / "bundle"
    export_replay_bundle((trace,), bundle, corpus_tier="10k")
    _, cases = load_replay_bundle(bundle)
    initial = state_from_replay(cases[0])

    enumerated = execute_action(initial, MicroAction(operation_id=32))
    with pytest.raises(ValueError, match="not active"):
        execute_action(
            enumerated,
            MicroAction(operation_id=43, arguments={"claim_id": "claim:invented"}),
        )

    result = search(
        initial,
        SearchConfig(max_depth=6, max_expansions=5000, max_terminal_candidates=256),
    )
    assert result.gold_used_during_search is False
    assert posthoc_reachable(result, ("1815",)) is False
    oracle = search(
        initial,
        SearchConfig(max_depth=6, max_expansions=5000, max_terminal_candidates=256),
        accepted_answers=("1815",),
        allow_gold=True,
    )
    assert candidate_set_oracle(oracle, ("1815",)) is True
    emitted = {value for terminal in result.terminal_candidates for value in terminal.answer_values}
    assert emitted <= {"1815", "1915"}


def test_fixture_reachability_gate_uses_training_eligible_oracle_only(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    _write_trace(trace, "development")
    bundle = tmp_path / "bundle"
    export_replay_bundle((trace,), bundle, corpus_tier="10k")
    benchmark = tmp_path / "benchmark.json"
    benchmark.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "case:ada",
                        "partition": "development",
                        "accepted_answers": ["1815"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    report = qualify_reachability(
        bundle, benchmark, max_depth=6, max_expansions=5000, beam_width=128
    )
    assert report["status"] == "COMPLETE"
    assert report["control_decision"] == "AETHERCORE_POLICY_FEASIBLE"
    assert report["controller_failure_reachable_fraction"] == 1.0
    assert report["gold_policy"]["gold_leakage_detected"] is False
