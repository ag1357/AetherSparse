"""Exact stage attribution for targeted value-enumeration traces.

The classifier is intentionally an offline qualification primitive.  It uses
accepted development/tuning targets only to locate the first observed loss in
an immutable capture; it is not imported by the production controller.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ValueTraceFailure(StrEnum):
    """First observed loss in the source-bound value path."""

    ANSWER_SHAPE_INCORRECT = "ANSWER_SHAPE_INCORRECT"
    VALUE_PRESENT_ADDRESS_BINDING_UNRESOLVED = "VALUE_PRESENT_ADDRESS_BINDING_UNRESOLVED"
    SOURCE_DOCUMENT_ABSENT = "SOURCE_DOCUMENT_ABSENT"
    SOURCE_DOCUMENT_OUTSIDE_TOP8 = "SOURCE_DOCUMENT_OUTSIDE_TOP8"
    SOURCE_CHUNK_ABSENT = "SOURCE_CHUNK_ABSENT"
    COMPILER_AND_RUNTIME_EXTRACTION = "COMPILER_AND_RUNTIME_EXTRACTION"
    COMPILER_EXTRACTION = "COMPILER_EXTRACTION"
    RUNTIME_EXTRACTION = "RUNTIME_EXTRACTION"
    REGION_PRUNING = "REGION_PRUNING"
    DEDUPLICATION = "DEDUPLICATION"
    VALUE_CAP = "VALUE_CAP"
    REBINDING = "REBINDING"
    AVAILABLE_REQUIRES_CONTROLLER = "AVAILABLE_REQUIRES_CONTROLLER"


@dataclass(frozen=True)
class ValueTraceQualification:
    """Exact stage-presence facts for one tier replica."""

    failure: ValueTraceFailure
    answer_shape_valid: bool
    replay_values_complete: bool
    source_documents_retrieved: bool
    source_documents_top8: bool
    target_spans_in_selected_chunks: bool
    compiler_pre_complete: bool
    compiler_type_complete: bool
    compiler_page_complete: bool
    runtime_pre_region_complete: bool
    runtime_post_region_complete: bool
    runtime_post_dedup_complete: bool
    runtime_post_cap_complete: bool
    exact_rebinding_complete: bool | None


def _document_key(document_id: object) -> tuple[str, str] | None:
    parts = str(document_id).split(":")
    if len(parts) >= 3 and parts[0] in {"mw", "simplewiki"}:
        return parts[1], parts[2]
    return None


def _all_targets(targets: tuple[str, ...], values: list[str]) -> bool:
    present = set(values)
    return bool(targets) and all(target in present for target in targets)


def _target_occurrences(
    case: dict[str, Any], targets: tuple[str, ...]
) -> dict[str, tuple[dict[str, Any], ...]]:
    by_target: dict[str, tuple[dict[str, Any], ...]] = {}
    for binding in case.get("exact_target_bindings", []):
        if not isinstance(binding, dict):
            continue
        target = str(binding.get("target", ""))
        occurrences = binding.get("occurrences", [])
        if target in targets and isinstance(occurrences, list):
            by_target[target] = tuple(item for item in occurrences if isinstance(item, dict))
    missing = [target for target in targets if not by_target.get(target)]
    if missing:
        raise ValueError(f"target lacks exact source occurrence: {missing}")
    return by_target


def _target_documents(
    occurrences: dict[str, tuple[dict[str, Any], ...]]
) -> dict[str, set[tuple[str, str]]]:
    result: dict[str, set[tuple[str, str]]] = {}
    for target, rows in occurrences.items():
        keys = {
            key
            for row in rows
            if (key := _document_key(row.get("document_id", ""))) is not None
        }
        if not keys:
            raise ValueError(f"target lacks a canonical document key: {target}")
        result[target] = keys
    return result


def _target_in_selected_chunk(
    target: str,
    occurrences: tuple[dict[str, Any], ...],
    chunks: tuple[dict[str, Any], ...],
) -> bool:
    for occurrence in occurrences:
        occurrence_document = _document_key(occurrence.get("document_id", ""))
        start = int(occurrence.get("char_start", -1))
        end = int(occurrence.get("char_end", -1))
        for chunk in chunks:
            if _document_key(chunk.get("document_id", "")) != occurrence_document:
                continue
            chunk_start = int(chunk.get("raw_start", -1))
            chunk_end = int(chunk.get("raw_end", -1))
            text = str(chunk.get("complete_chunk_text", ""))
            if chunk_start <= start < end <= chunk_end:
                local_start = start - chunk_start
                local_end = end - chunk_start
                if text[local_start:local_end] == target:
                    return True
    return False


def _compiler_values(
    documents: tuple[dict[str, Any], ...],
    document_keys: set[tuple[str, str]],
    field: str,
) -> list[str]:
    values: list[str] = []
    for document in documents:
        if _document_key(document.get("document_id", "")) not in document_keys:
            continue
        boundary = document.get("boundary", {})
        if not isinstance(boundary, dict):
            continue
        matches = boundary.get(field, [])
        if not isinstance(matches, list):
            continue
        values.extend(
            str(match.get("object_value", ""))
            for match in matches
            if isinstance(match, dict)
        )
    return values


def _runtime_values(
    chunks: tuple[dict[str, Any], ...],
    target_documents: dict[str, set[tuple[str, str]]],
    field: str,
) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {target: [] for target in target_documents}
    for chunk in chunks:
        document_key = _document_key(chunk.get("document_id", ""))
        boundary = chunk.get("runtime_boundary", {})
        if not isinstance(boundary, dict):
            continue
        raw_values = boundary.get(field, [])
        if not isinstance(raw_values, list):
            continue
        for target, document_keys in target_documents.items():
            if document_key not in document_keys:
                continue
            values[target].extend(
                str(item.get("surface", "")) if isinstance(item, dict) else str(item)
                for item in raw_values
            )
    return values


def _runtime_complete(targets: tuple[str, ...], values: dict[str, list[str]]) -> bool:
    return all(target in values[target] for target in targets)


def qualify_value_trace(
    replica: dict[str, Any], case: dict[str, Any]
) -> ValueTraceQualification:
    """Locate the first exact loss for one protected training replica."""

    targets = tuple(str(item) for item in replica.get("target_atomic_values", ()))
    if not targets:
        raise ValueError("replica has no target atomic values")
    occurrences = _target_occurrences(case, targets)
    target_documents = _target_documents(occurrences)
    capture = replica.get("pack_capture")
    if not isinstance(capture, dict):
        raise ValueError("replica lacks pack capture")
    chunks = tuple(
        item
        for item in capture.get("selected_chunks", ())
        if isinstance(item, dict) and not item.get("missing_from_pack")
    )
    compiler_documents = tuple(
        item
        for item in capture.get("compiler_documents", ())
        if isinstance(item, dict) and not item.get("missing_from_pack")
    )
    retrieved_documents = {
        key
        for item in replica.get("retrieved_document_ids", ())
        if (key := _document_key(item)) is not None
    }
    selected_documents = {
        key
        for chunk in chunks
        if (key := _document_key(chunk.get("document_id", ""))) is not None
    }
    source_documents_retrieved = all(
        bool(target_documents[target] & retrieved_documents) for target in targets
    )
    source_documents_top8 = all(
        bool(target_documents[target] & selected_documents) for target in targets
    )
    target_spans_in_selected_chunks = all(
        _target_in_selected_chunk(target, occurrences[target], chunks) for target in targets
    )
    compiler_pre_complete = all(
        target
        in _compiler_values(
            compiler_documents,
            target_documents[target],
            "all_typed_matches_before_type_caps",
        )
        for target in targets
    )
    compiler_type_complete = all(
        target
        in _compiler_values(
            compiler_documents,
            target_documents[target],
            "typed_matches_after_type_caps",
        )
        for target in targets
    )
    compiler_page_complete = all(
        target
        in _compiler_values(
            compiler_documents,
            target_documents[target],
            "typed_matches_after_page_cap",
        )
        for target in targets
    )
    runtime_pre = _runtime_values(
        chunks, target_documents, "all_matches_before_region_pruning"
    )
    runtime_post_region = _runtime_values(
        chunks, target_documents, "top8_matches_before_deduplication"
    )
    runtime_post_dedup = _runtime_values(chunks, target_documents, "post_dedup_values")
    runtime_post_cap = _runtime_values(chunks, target_documents, "post_cap_values")
    runtime_pre_region_complete = _runtime_complete(targets, runtime_pre)
    runtime_post_region_complete = _runtime_complete(targets, runtime_post_region)
    runtime_post_dedup_complete = _runtime_complete(targets, runtime_post_dedup)
    runtime_post_cap_complete = _runtime_complete(targets, runtime_post_cap)
    exact_rebinding_complete: bool | None = None
    if runtime_pre_region_complete:
        bound_by_target = {target: False for target in targets}
        for chunk in chunks:
            document_key = _document_key(chunk.get("document_id", ""))
            boundary = chunk.get("runtime_boundary", {})
            if not isinstance(boundary, dict):
                continue
            for match in boundary.get("all_matches_before_region_pruning", ()):
                if not isinstance(match, dict):
                    continue
                surface = str(match.get("surface", ""))
                if (
                    surface in bound_by_target
                    and document_key in target_documents[surface]
                    and bool(match.get("document_binding_success"))
                ):
                    bound_by_target[surface] = True
        exact_rebinding_complete = all(bound_by_target.values())

    answer_shape_valid = str(replica.get("answer_shape", "")) == str(
        case.get("required_answer_shape", "")
    )
    replay_values_complete = _all_targets(
        targets, [str(item) for item in replica.get("runtime_candidate_values", ())]
    )
    if not answer_shape_valid:
        failure = ValueTraceFailure.ANSWER_SHAPE_INCORRECT
    elif replay_values_complete:
        failure = ValueTraceFailure.VALUE_PRESENT_ADDRESS_BINDING_UNRESOLVED
    elif not source_documents_retrieved:
        failure = ValueTraceFailure.SOURCE_DOCUMENT_ABSENT
    elif not source_documents_top8:
        failure = ValueTraceFailure.SOURCE_DOCUMENT_OUTSIDE_TOP8
    elif not target_spans_in_selected_chunks:
        failure = ValueTraceFailure.SOURCE_CHUNK_ABSENT
    elif not compiler_pre_complete and not runtime_pre_region_complete:
        failure = ValueTraceFailure.COMPILER_AND_RUNTIME_EXTRACTION
    elif not compiler_pre_complete:
        failure = ValueTraceFailure.COMPILER_EXTRACTION
    elif not runtime_pre_region_complete:
        failure = ValueTraceFailure.RUNTIME_EXTRACTION
    elif not runtime_post_region_complete:
        failure = ValueTraceFailure.REGION_PRUNING
    elif not runtime_post_dedup_complete:
        failure = ValueTraceFailure.DEDUPLICATION
    elif not runtime_post_cap_complete:
        failure = ValueTraceFailure.VALUE_CAP
    elif exact_rebinding_complete is False:
        failure = ValueTraceFailure.REBINDING
    else:
        failure = ValueTraceFailure.AVAILABLE_REQUIRES_CONTROLLER
    return ValueTraceQualification(
        failure=failure,
        answer_shape_valid=answer_shape_valid,
        replay_values_complete=replay_values_complete,
        source_documents_retrieved=source_documents_retrieved,
        source_documents_top8=source_documents_top8,
        target_spans_in_selected_chunks=target_spans_in_selected_chunks,
        compiler_pre_complete=compiler_pre_complete,
        compiler_type_complete=compiler_type_complete,
        compiler_page_complete=compiler_page_complete,
        runtime_pre_region_complete=runtime_pre_region_complete,
        runtime_post_region_complete=runtime_post_region_complete,
        runtime_post_dedup_complete=runtime_post_dedup_complete,
        runtime_post_cap_complete=runtime_post_cap_complete,
        exact_rebinding_complete=exact_rebinding_complete,
    )
