from __future__ import annotations

from copy import deepcopy
from typing import Any

from aethersparse.controller.value_trace import ValueTraceFailure, qualify_value_trace


def _case() -> dict[str, Any]:
    return {
        "case_id": "case:1",
        "partition": "development",
        "required_answer_shape": "quantity",
        "exact_target_bindings": [
            {
                "target": "10%",
                "occurrences": [
                    {
                        "document_id": "simplewiki:1:2",
                        "char_start": 5,
                        "char_end": 8,
                        "surface": "10%",
                    }
                ],
            }
        ],
    }


def _match(*, bound: bool = True) -> dict[str, Any]:
    return {
        "surface": "10%",
        "document_binding_success": bound,
        "exact_surface_bound": True,
    }


def _replica() -> dict[str, Any]:
    return {
        "case_id": "case:1",
        "partition": "development",
        "corpus_tier": "10k",
        "answer_shape": "quantity",
        "target_atomic_values": ["10%"],
        "runtime_candidate_values": [],
        "retrieved_document_ids": ["mw:1:2:hash"],
        "pack_capture": {
            "selected_chunks": [
                {
                    "chunk_id": "chunk:1",
                    "document_id": "mw:1:2:hash",
                    "raw_start": 0,
                    "raw_end": 8,
                    "complete_chunk_text": "text 10%",
                    "runtime_boundary": {
                        "all_matches_before_region_pruning": [_match()],
                        "top8_matches_before_deduplication": [_match()],
                        "post_dedup_values": ["10%"],
                        "post_cap_values": ["10%"],
                    },
                }
            ],
            "compiler_documents": [
                {
                    "document_id": "mw:1:2:hash",
                    "boundary": {
                        "all_typed_matches_before_type_caps": [
                            {"object_value": "10%"}
                        ],
                        "typed_matches_after_type_caps": [{"object_value": "10%"}],
                        "typed_matches_after_page_cap": [{"object_value": "10%"}],
                    },
                }
            ],
        },
    }


def test_value_trace_reports_available_exact_path() -> None:
    result = qualify_value_trace(_replica(), _case())

    assert result.failure is ValueTraceFailure.AVAILABLE_REQUIRES_CONTROLLER
    assert result.exact_rebinding_complete is True


def test_value_trace_prioritizes_already_enumerated_address_binding() -> None:
    replica = _replica()
    replica["runtime_candidate_values"] = ["10%"]

    result = qualify_value_trace(replica, _case())

    assert result.failure is ValueTraceFailure.VALUE_PRESENT_ADDRESS_BINDING_UNRESOLVED


def test_value_trace_distinguishes_chunk_absence_from_runtime_extraction() -> None:
    missing_chunk = _replica()
    missing_chunk["pack_capture"]["selected_chunks"][0].update(
        {"raw_start": 8, "raw_end": 16, "complete_chunk_text": "no value"}
    )
    missing_chunk["pack_capture"]["selected_chunks"][0]["runtime_boundary"].update(
        {
            "all_matches_before_region_pruning": [],
            "top8_matches_before_deduplication": [],
            "post_dedup_values": [],
            "post_cap_values": [],
        }
    )
    assert (
        qualify_value_trace(missing_chunk, _case()).failure
        is ValueTraceFailure.SOURCE_CHUNK_ABSENT
    )

    extraction = _replica()
    boundary = extraction["pack_capture"]["selected_chunks"][0]["runtime_boundary"]
    boundary.update(
        {
            "all_matches_before_region_pruning": [],
            "top8_matches_before_deduplication": [],
            "post_dedup_values": [],
            "post_cap_values": [],
        }
    )
    extraction["pack_capture"]["compiler_documents"][0]["boundary"].update(
        {
            "all_typed_matches_before_type_caps": [],
            "typed_matches_after_type_caps": [],
            "typed_matches_after_page_cap": [],
        }
    )
    assert (
        qualify_value_trace(extraction, _case()).failure
        is ValueTraceFailure.COMPILER_AND_RUNTIME_EXTRACTION
    )


def test_value_trace_distinguishes_region_and_rebinding_losses() -> None:
    region = _replica()
    boundary = region["pack_capture"]["selected_chunks"][0]["runtime_boundary"]
    boundary.update(
        {
            "top8_matches_before_deduplication": [],
            "post_dedup_values": [],
            "post_cap_values": [],
        }
    )
    assert qualify_value_trace(region, _case()).failure is ValueTraceFailure.REGION_PRUNING

    rebinding = deepcopy(_replica())
    boundary = rebinding["pack_capture"]["selected_chunks"][0]["runtime_boundary"]
    boundary["all_matches_before_region_pruning"] = [_match(bound=False)]
    assert qualify_value_trace(rebinding, _case()).failure is ValueTraceFailure.REBINDING
