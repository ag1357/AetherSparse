"""Versioned, deterministic controller replay bundles.

The replay format is deliberately independent of corpus retrieval.  It accepts
retained controller decision records, strips unneeded source surface, and emits
a content-addressed JSONL bundle suitable for exact micro-operation replay.
Evaluation and final-held cases are always marked non-training.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aethersparse.controller.trace import CaseTrace

REPLAY_SCHEMA_VERSION = "aethercore.controller-replay.v1"
NON_TRAINING_PARTITIONS = frozenset({"evaluation", "final_held"})


class ReplayModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReplayDecision(ReplayModel):
    """One controller decision state with its legal/action/outcome record."""

    step_index: int = Field(ge=0)
    query_frame: dict[str, Any] = Field(default_factory=dict)
    linked_entity_candidates: tuple[dict[str, Any], ...] = ()
    discourse_state: dict[str, Any] = Field(default_factory=dict)
    ranked_evidence_metadata: tuple[dict[str, Any], ...] = ()
    structured_claims: tuple[dict[str, Any], ...] = ()
    source_spans: tuple[dict[str, Any], ...] = ()
    candidate_values: tuple[str, ...] = ()
    required_facets: tuple[str, ...] = ()
    missing_facets: tuple[str, ...] = ()
    selection_state: dict[str, Any] = Field(default_factory=dict)
    plan_state: dict[str, Any] = Field(default_factory=dict)
    verification_state: dict[str, Any] = Field(default_factory=dict)
    disposition_state: dict[str, Any] = Field(default_factory=dict)
    legal_high_level_actions: tuple[int, ...] = ()
    legal_micro_actions: tuple[int, ...] = ()
    action_taken: int
    action_arguments: dict[str, Any] = Field(default_factory=dict)
    action_result: dict[str, Any] = Field(default_factory=dict)
    terminal: str | None = None
    provenance_ids: tuple[str, ...] = ()


class ReplayCase(ReplayModel):
    case_id: str
    partition: str
    corpus_tier: str
    training_eligible: bool
    outcome: Literal["correct", "incorrect", "aborted", "unknown"]
    decisions: tuple[ReplayDecision, ...]
    source_trace_sha256: str
    replay_complete: bool
    incompleteness_reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def protected_partitions_never_train(self) -> ReplayCase:
        if self.partition in NON_TRAINING_PARTITIONS and self.training_eligible:
            raise ValueError(f"{self.partition} records may not be training eligible")
        return self


class ReplayManifest(ReplayModel):
    schema_version: str = REPLAY_SCHEMA_VERSION
    case_count: int = Field(ge=0)
    decision_count: int = Field(ge=0)
    tier_counts: dict[str, int]
    partition_counts: dict[str, int]
    training_case_count: int = Field(ge=0)
    incomplete_case_count: int = Field(ge=0)
    input_trace_hashes: tuple[str, ...]
    cases_file: str
    cases_sha256: str
    bundle_sha256: str


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _objects(value: object) -> tuple[dict[str, Any], ...]:
    if isinstance(value, dict):
        return (dict(value),)
    if isinstance(value, (list, tuple)):
        return tuple(dict(item) for item in value if isinstance(item, dict))
    return ()


def _strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value)
    return ()


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _claim_values(claims: tuple[dict[str, Any], ...]) -> tuple[str, ...]:
    values: list[str] = []
    for claim in claims:
        for key in ("object_value", "quantity_value", "quotation"):
            value = claim.get(key)
            if isinstance(value, str) and value and value not in values:
                values.append(value)
    return tuple(values)


def _prune_spans(spans: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    """Keep identifiers/hashes and only the exact surface needed by verification."""

    allowed = {
        "span_id",
        "document_id",
        "source_title",
        "source_revision",
        "source_url",
        "source_family",
        "source_class",
        "char_start",
        "char_end",
        "text",
        "text_hash",
    }
    return tuple({key: span[key] for key in sorted(span) if key in allowed} for span in spans)


def _provenance_ids(
    claims: tuple[dict[str, Any], ...], spans: tuple[dict[str, Any], ...]
) -> tuple[str, ...]:
    values: set[str] = set()
    for claim in claims:
        for key in ("claim_id", "subject_entity_id", "object_entity_id"):
            value = claim.get(key)
            if isinstance(value, str) and value:
                values.add(value)
        values.update(_strings(claim.get("source_span_ids")))
    for span in spans:
        for key in ("span_id", "document_id", "source_revision"):
            value = span.get(key)
            if isinstance(value, str) and value:
                values.add(value)
    return tuple(sorted(values))


def decision_from_record(record: dict[str, Any]) -> ReplayDecision:
    """Project a rich v09 trace record into the stable replay schema."""

    before = _mapping(record.get("state_before"))
    after = _mapping(record.get("state_after"))
    merged = {**before, **after}
    frame = _mapping(merged.get("query_frame") or merged.get("frame"))
    claims = _objects(merged.get("structured_claims") or merged.get("claims"))
    spans = _prune_spans(_objects(merged.get("source_spans")))
    candidate_values = _strings(merged.get("candidate_values")) or _claim_values(claims)
    high_level_legal = tuple(
        sorted(int(item) for item in _strings(record.get("legal_actions", ())))
    )
    supplied_micro = tuple(
        sorted(int(item) for item in _strings(record.get("legal_micro_actions", ())))
    )
    if supplied_micro:
        micro_legal = supplied_micro
    else:
        # A v09 high-level trace cannot name v10 micro-actions. Compute the
        # legal initial micro-action set from its exact replay objects instead
        # of relabeling the legacy IDs.
        from aethersparse.controller.micro_ops import MicroState, legal_operation_specs

        micro_state = MicroState(
            case_id=str(record.get("case_id", "unknown")),
            frame=frame,
            claims=claims,
            source_spans=spans,
        )
        micro_legal = tuple(spec.operation_id for spec in legal_operation_specs(micro_state))
    return ReplayDecision(
        step_index=int(record.get("step_index", 0)),
        query_frame=frame,
        linked_entity_candidates=_objects(
            merged.get("linked_entity_candidates") or merged.get("entity_candidates")
        ),
        discourse_state=_mapping(merged.get("discourse_state")),
        ranked_evidence_metadata=_objects(
            merged.get("ranked_evidence_metadata") or merged.get("ranked_evidence")
        ),
        structured_claims=claims,
        source_spans=spans,
        candidate_values=candidate_values,
        required_facets=_strings(merged.get("required_facets") or frame.get("required_facets")),
        missing_facets=_strings(merged.get("missing_facets") or merged.get("facets_open")),
        selection_state=_mapping(merged.get("selection_state") or merged.get("selection")),
        plan_state=_mapping(merged.get("plan_state") or merged.get("plan")),
        verification_state=_mapping(merged.get("verification_state") or merged.get("verification")),
        disposition_state=_mapping(merged.get("disposition_state") or merged.get("disposition")),
        legal_high_level_actions=high_level_legal,
        legal_micro_actions=micro_legal,
        action_taken=int(record.get("action_taken", 0)),
        action_arguments=_mapping(record.get("arguments")),
        action_result=_mapping(record.get("result")),
        terminal=str(record["terminal"]) if record.get("terminal") is not None else None,
        provenance_ids=_provenance_ids(claims, spans),
    )


def replay_case_from_payload(
    payload: dict[str, Any], *, corpus_tier: str, source_trace_sha256: str
) -> ReplayCase:
    partition = str(payload.get("partition", "unknown")).removeprefix("Partition.").lower()
    records = payload.get("records", ())
    if not isinstance(records, list):
        raise ValueError("trace case records must be a list")
    decisions = tuple(decision_from_record(dict(record)) for record in records)
    accepted_dispositions = {
        str(state.get("accepted_disposition", ""))
        for record in records
        if isinstance(record, dict)
        for state in (record.get("state_before"), record.get("state_after"))
        if isinstance(state, dict)
    }
    answer_evidence_required = not accepted_dispositions or any(
        disposition.endswith("ANSWER") for disposition in accepted_dispositions
    )
    reasons: list[str] = []
    if not decisions:
        reasons.append("NO_DECISIONS")
    if (
        answer_evidence_required
        and decisions
        and not any(decision.structured_claims for decision in decisions)
    ):
        reasons.append("STRUCTURED_CLAIMS_ABSENT")
    if answer_evidence_required and decisions and not any(
        decision.source_spans for decision in decisions
    ):
        reasons.append("SOURCE_SPANS_ABSENT")
    if decisions and not any(decision.query_frame for decision in decisions):
        reasons.append("QUERY_FRAME_INCOMPLETE")
    requested_training = bool(payload.get("training_eligible", False))
    training_eligible = requested_training and partition not in NON_TRAINING_PARTITIONS
    raw_outcome = str(payload.get("outcome", "unknown"))
    outcome: Literal["correct", "incorrect", "aborted", "unknown"]
    if raw_outcome == "correct":
        outcome = "correct"
    elif raw_outcome == "incorrect":
        outcome = "incorrect"
    elif raw_outcome == "aborted":
        outcome = "aborted"
    else:
        outcome = "unknown"
    return ReplayCase(
        case_id=str(payload.get("case_id", "unknown")),
        partition=partition,
        corpus_tier=corpus_tier,
        training_eligible=training_eligible,
        outcome=outcome,
        decisions=decisions,
        source_trace_sha256=source_trace_sha256,
        replay_complete=not reasons,
        incompleteness_reasons=tuple(reasons),
    )


def load_trace_cases(paths: Iterable[Path], *, corpus_tier: str) -> tuple[ReplayCase, ...]:
    cases: list[ReplayCase] = []
    seen: set[str] = set()
    for path in sorted((Path(item) for item in paths), key=lambda item: str(item)):
        source_hash = sha256_path(path)
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError(f"{path}:{line_number}: trace record must be an object")
                case = replay_case_from_payload(
                    payload, corpus_tier=corpus_tier, source_trace_sha256=source_hash
                )
                if case.case_id in seen:
                    raise ValueError(f"duplicate replay case: {case.case_id}")
                seen.add(case.case_id)
                cases.append(case)
    return tuple(sorted(cases, key=lambda item: item.case_id))


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()


def export_replay_bundle(
    trace_paths: Iterable[Path], output: Path, *, corpus_tier: str
) -> ReplayManifest:
    """Write deterministic ``cases.jsonl.gz`` and ``manifest.json`` files."""

    paths = tuple(Path(item) for item in trace_paths)
    if not paths:
        raise ValueError("at least one trace path is required")
    cases = load_trace_cases(paths, corpus_tier=corpus_tier)
    return _write_replay_bundle(
        cases,
        output,
        input_trace_hashes=tuple(sorted({sha256_path(path) for path in paths})),
    )


def _write_replay_bundle(
    cases: tuple[ReplayCase, ...],
    output: Path,
    *,
    input_trace_hashes: tuple[str, ...],
) -> ReplayManifest:
    output.mkdir(parents=True, exist_ok=True)
    ordered_cases = tuple(sorted(cases, key=lambda item: (item.corpus_tier, item.case_id)))
    case_lines = b"".join(
        canonical_json_bytes(case.model_dump(mode="json")) for case in ordered_cases
    )
    cases_path = output / "cases.jsonl.gz"
    with (
        cases_path.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
    ):
        compressed.write(case_lines)
    cases_hash = sha256_path(cases_path)
    tier_counts: dict[str, int] = {}
    partition_counts: dict[str, int] = {}
    for case in ordered_cases:
        tier_counts[case.corpus_tier] = tier_counts.get(case.corpus_tier, 0) + 1
        partition_counts[case.partition] = partition_counts.get(case.partition, 0) + 1
    core = {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "case_count": len(ordered_cases),
        "decision_count": sum(len(case.decisions) for case in ordered_cases),
        "tier_counts": dict(sorted(tier_counts.items())),
        "partition_counts": dict(sorted(partition_counts.items())),
        "training_case_count": sum(case.training_eligible for case in ordered_cases),
        "incomplete_case_count": sum(not case.replay_complete for case in ordered_cases),
        "input_trace_hashes": list(input_trace_hashes),
        "cases_file": cases_path.name,
        "cases_sha256": cases_hash,
    }
    bundle_hash = sha256_bytes(canonical_json_bytes(core) + cases_path.read_bytes())
    manifest = ReplayManifest(
        schema_version=REPLAY_SCHEMA_VERSION,
        case_count=len(ordered_cases),
        decision_count=sum(len(case.decisions) for case in ordered_cases),
        tier_counts=dict(sorted(tier_counts.items())),
        partition_counts=dict(sorted(partition_counts.items())),
        training_case_count=sum(case.training_eligible for case in ordered_cases),
        incomplete_case_count=sum(not case.replay_complete for case in ordered_cases),
        input_trace_hashes=input_trace_hashes,
        cases_file=cases_path.name,
        cases_sha256=cases_hash,
        bundle_sha256=bundle_hash,
    )
    (output / "manifest.json").write_bytes(canonical_json_bytes(manifest.model_dump(mode="json")))
    return manifest


def merge_replay_bundles(inputs: Iterable[Path], output: Path) -> ReplayManifest:
    """Merge independently exported tiers into one deterministic four-tier bundle."""

    cases: list[ReplayCase] = []
    hashes: set[str] = set()
    identities: set[tuple[str, str]] = set()
    for bundle in sorted((Path(item) for item in inputs), key=lambda item: str(item)):
        manifest, tier_cases = load_replay_bundle(bundle)
        hashes.update(manifest.input_trace_hashes)
        for case in tier_cases:
            identity = (case.corpus_tier, case.case_id)
            if identity in identities:
                raise ValueError(f"duplicate tier/case replay: {identity}")
            identities.add(identity)
            cases.append(case)
    if not cases:
        raise ValueError("at least one replay bundle is required")
    return _write_replay_bundle(tuple(cases), output, input_trace_hashes=tuple(sorted(hashes)))


def verify_replay_bundle(bundle: Path) -> ReplayManifest:
    manifest_path = Path(bundle) / "manifest.json"
    manifest = ReplayManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    cases_path = Path(bundle) / manifest.cases_file
    if sha256_path(cases_path) != manifest.cases_sha256:
        raise ValueError("replay cases hash mismatch")
    core = manifest.model_dump(mode="json", exclude={"bundle_sha256"})
    expected = sha256_bytes(canonical_json_bytes(core) + cases_path.read_bytes())
    if expected != manifest.bundle_sha256:
        raise ValueError("replay bundle hash mismatch")
    with gzip.open(cases_path, "rt", encoding="utf-8") as handle:
        cases = [ReplayCase.model_validate_json(line) for line in handle if line.strip()]
    if len(cases) != manifest.case_count:
        raise ValueError("replay case count mismatch")
    if sum(len(case.decisions) for case in cases) != manifest.decision_count:
        raise ValueError("replay decision count mismatch")
    return manifest


def load_replay_bundle(bundle: Path) -> tuple[ReplayManifest, tuple[ReplayCase, ...]]:
    manifest = verify_replay_bundle(bundle)
    with gzip.open(Path(bundle) / manifest.cases_file, "rt", encoding="utf-8") as handle:
        cases = tuple(ReplayCase.model_validate_json(line) for line in handle if line.strip())
    return manifest, cases


def validate_trace_payload(payload: dict[str, Any]) -> CaseTrace:
    """Compatibility helper used by callers that want strict v09 validation."""

    return CaseTrace.model_validate(payload)
