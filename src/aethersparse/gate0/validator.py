"""Independent deterministic validator for Gate 0 candidate packets."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from aethersparse.gate0.models import (
    CandidatePacket,
    CheckStatus,
    FrozenSourceSnapshot,
    ValidationDecision,
    ValidationRun,
    ValidatorCheck,
    ValidatorResult,
)
from aethersparse.gate0.sources import (
    SourceRepository,
    normalize_text,
    sha256_text,
    stable_json,
    verify_snapshot,
)
from aethersparse.models import PacketType

VALIDATOR_IDENTITY = "aethersparse_independent_deterministic_validator"
VALIDATOR_VERSION = "1.0.0"
VALIDATOR_CONFIG_VERSION = "gate0-validator-config-v1"
VALIDATOR_MONTHS = {
    month: index
    for index, month in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        start=1,
    )
}
VALIDATOR_DATE_PATTERN = re.compile(
    r"\b(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+"
    r"(?P<year>\d{4})\b"
)

RELATION_KEYWORDS = {
    "launched": ("launch", "lifted off"),
    "landed": ("landed", "touched down"),
    "returned": ("returned", "splashdown", "splashed down"),
    "entered orbit": ("orbit",),
    "separated": ("separated",),
    "conducted": ("conducted",),
    "said": ("said", "responded", "commented", "replied", "greeted"),
    "explicit description": (" was ", " were ", " is ", " are "),
    "has explicit quantity": (),
}


def _stable_id(kind: str, label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_") or "unknown"
    digest = hashlib.sha256(f"{kind}:{label.casefold()}".encode()).hexdigest()[:12]
    return f"as:{kind}:{slug}:{digest}"


def _relation_id(label: str) -> str:
    return _stable_id("rel", label)


def validator_configuration_hash() -> str:
    config = {
        "identity": VALIDATOR_IDENTITY,
        "version": VALIDATOR_VERSION,
        "config_version": VALIDATOR_CONFIG_VERSION,
        "relation_keywords": RELATION_KEYWORDS,
    }
    return f"sha256:{hashlib.sha256(stable_json(config)).hexdigest()}"


def _date_values(text: str) -> set[str]:
    values: set[str] = set()
    for match in VALIDATOR_DATE_PATTERN.finditer(text):
        month_name = match.group("month").casefold()
        if month_name not in VALIDATOR_MONTHS:
            continue
        month = VALIDATOR_MONTHS[month_name]
        values.add(f"{int(match.group('year')):04d}-{month:02d}-{int(match.group('day')):02d}")
    return values


def _token_set(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.casefold()))


def _jaccard(left: str, right: str) -> float:
    left_tokens = _token_set(left)
    right_tokens = _token_set(right)
    if not left_tokens and not right_tokens:
        return 1.0
    union = left_tokens | right_tokens
    return len(left_tokens & right_tokens) / len(union) if union else 0.0


def _check_alignment(
    candidate: CandidatePacket,
    snapshot: FrozenSourceSnapshot,
) -> ValidatorCheck:
    try:
        verify_snapshot(snapshot)
    except ValueError as error:
        return ValidatorCheck(
            check_id="source_integrity",
            status=CheckStatus.FAIL,
            detail=str(error),
        )
    for claim in candidate.atomic_claims:
        alignment = claim.alignment
        if alignment.source_content_hash != snapshot.raw_content_hash:
            return ValidatorCheck(
                check_id="source_integrity",
                status=CheckStatus.FAIL,
                detail="alignment source hash differs from frozen snapshot",
            )
        raw = snapshot.raw_text[alignment.raw_char_start : alignment.raw_char_end]
        if raw != alignment.raw_text or sha256_text(raw) != alignment.raw_text_hash:
            return ValidatorCheck(
                check_id="source_integrity",
                status=CheckStatus.FAIL,
                detail="raw span offsets or hash do not reproduce",
            )
        byte_start = len(snapshot.raw_text[: alignment.raw_char_start].encode("utf-8"))
        byte_end = len(snapshot.raw_text[: alignment.raw_char_end].encode("utf-8"))
        if (byte_start, byte_end) != (alignment.raw_byte_start, alignment.raw_byte_end):
            return ValidatorCheck(
                check_id="source_integrity",
                status=CheckStatus.FAIL,
                detail="raw byte offsets do not reproduce",
            )
        if normalize_text(raw) != alignment.normalized_text:
            return ValidatorCheck(
                check_id="source_integrity",
                status=CheckStatus.FAIL,
                detail="normalized span does not reproduce from raw offsets",
            )
    return ValidatorCheck(
        check_id="source_integrity",
        status=CheckStatus.PASS,
        detail="raw and normalized offsets and hashes reproduce",
    )


def _check_entailment(candidate: CandidatePacket) -> ValidatorCheck:
    evidence = " ".join(claim.alignment.normalized_text for claim in candidate.atomic_claims)
    subject_label = str(candidate.payload.get("subject_label", "")).strip()
    object_label = str(candidate.payload.get("object_label", "")).strip()
    if subject_label and normalize_text(subject_label).casefold() not in evidence.casefold():
        return ValidatorCheck(
            check_id="source_entailment",
            status=CheckStatus.FAIL,
            detail=f"subject surface is absent from aligned evidence: {subject_label}",
        )
    if (
        object_label
        and candidate.packet_type is not PacketType.EVENT
        and normalize_text(object_label).casefold() not in evidence.casefold()
    ):
        normalized_value = str(candidate.payload.get("normalized_value", ""))
        if not normalized_value or normalized_value not in candidate.temporal_values:
            return ValidatorCheck(
                check_id="source_entailment",
                status=CheckStatus.REVIEW,
                detail=f"object requires entailment review: {object_label}",
            )
    return ValidatorCheck(
        check_id="source_entailment",
        status=CheckStatus.PASS,
        detail="candidate surfaces are recoverable from aligned evidence",
    )


def _check_relation(candidate: CandidatePacket) -> ValidatorCheck:
    evidence = f" {' '.join(claim.alignment.normalized_text for claim in candidate.atomic_claims)} "
    known: dict[str, tuple[str, ...]] = {
        _relation_id(label): keywords for label, keywords in RELATION_KEYWORDS.items()
    }
    for role in ("commander", "command_module_pilot", "lunar_module_pilot"):
        known[_relation_id(f"served_as_{role}")] = tuple(role.split("_"))
    keywords = known.get(candidate.primary_relation)
    if keywords is None:
        return ValidatorCheck(
            check_id="relation_correctness",
            status=CheckStatus.REVIEW,
            detail="relation is not in the independent validator allowlist",
        )
    if keywords and not any(keyword in evidence.casefold() for keyword in keywords):
        return ValidatorCheck(
            check_id="relation_correctness",
            status=CheckStatus.FAIL,
            detail="aligned evidence lacks the relation trigger",
        )
    return ValidatorCheck(
        check_id="relation_correctness",
        status=CheckStatus.PASS,
        detail="relation trigger independently reproduced",
    )


def _check_negation(candidate: CandidatePacket) -> ValidatorCheck:
    evidence = " ".join(claim.alignment.normalized_text for claim in candidate.atomic_claims)
    has_negation = bool(re.search(r"\b(?:not|no|never|without|didn't|did not)\b", evidence, re.I))
    expected = "negative" if has_negation else "positive"
    return ValidatorCheck(
        check_id="negation",
        status=CheckStatus.PASS if candidate.polarity == expected else CheckStatus.FAIL,
        detail=f"evidence polarity={expected}; candidate polarity={candidate.polarity}",
    )


def _check_temporal(candidate: CandidatePacket) -> ValidatorCheck:
    if not candidate.temporal_values:
        return ValidatorCheck(
            check_id="temporal_value",
            status=CheckStatus.NOT_APPLICABLE,
            detail="candidate has no normalized temporal value",
        )
    evidence = " ".join(claim.alignment.normalized_text for claim in candidate.atomic_claims)
    recovered = _date_values(evidence)
    missing = set(candidate.temporal_values) - recovered
    return ValidatorCheck(
        check_id="temporal_value",
        status=CheckStatus.FAIL if missing else CheckStatus.PASS,
        detail=(
            f"normalized dates absent from evidence: {sorted(missing)}"
            if missing
            else "all normalized dates reproduce"
        ),
    )


def _check_quantities(candidate: CandidatePacket) -> ValidatorCheck:
    if not candidate.quantities:
        return ValidatorCheck(
            check_id="quantity_unit_ownership",
            status=CheckStatus.NOT_APPLICABLE,
            detail="candidate has no quantity",
        )
    evidence = " ".join(claim.alignment.normalized_text for claim in candidate.atomic_claims)
    missing = [
        quantity.surface
        for quantity in candidate.quantities
        if normalize_text(quantity.surface).casefold() not in evidence.casefold()
    ]
    no_owner = [
        quantity.surface for quantity in candidate.quantities if not quantity.owner_entity_id
    ]
    if missing:
        status = CheckStatus.FAIL
        detail = f"quantity surfaces absent from evidence: {missing}"
    elif no_owner:
        status = CheckStatus.REVIEW
        detail = f"quantity ownership is unresolved: {no_owner}"
    else:
        status = CheckStatus.PASS
        detail = "quantity surfaces, units, and owners are present"
    return ValidatorCheck(
        check_id="quantity_unit_ownership",
        status=status,
        detail=detail,
    )


def _check_attribution(
    candidate: CandidatePacket,
    snapshot: FrozenSourceSnapshot,
) -> ValidatorCheck:
    if candidate.packet_type is not PacketType.QUOTATION:
        return ValidatorCheck(
            check_id="quotation_attribution",
            status=CheckStatus.NOT_APPLICABLE,
            detail="candidate is not a quotation",
        )
    quotation = str(candidate.payload.get("quotation", ""))
    if quotation not in snapshot.raw_text:
        return ValidatorCheck(
            check_id="quotation_attribution",
            status=CheckStatus.FAIL,
            detail="quotation is not an exact raw substring",
        )
    if not candidate.attribution or candidate.attribution == "UNKNOWN_SPEAKER":
        return ValidatorCheck(
            check_id="quotation_attribution",
            status=CheckStatus.REVIEW,
            detail="quotation speaker requires human attribution",
        )
    quote_start = snapshot.raw_text.find(quotation)
    nearby = snapshot.raw_text[max(0, quote_start - 180) : quote_start]
    if candidate.attribution.casefold() not in nearby.casefold():
        return ValidatorCheck(
            check_id="quotation_attribution",
            status=CheckStatus.REVIEW,
            detail="speaker is not explicit in nearby raw context",
        )
    return ValidatorCheck(
        check_id="quotation_attribution",
        status=CheckStatus.PASS,
        detail="quotation and nearby speaker attribution reproduce",
    )


def _check_type(candidate: CandidatePacket) -> ValidatorCheck:
    allowed = {
        PacketType.PROPOSITION,
        PacketType.EVENT,
        PacketType.QUOTATION,
        PacketType.SOURCE_SPAN,
    }
    if candidate.packet_type not in allowed:
        return ValidatorCheck(
            check_id="type_compatibility",
            status=CheckStatus.FAIL,
            detail="packet type is outside Gate 0 Tier 1 policy",
        )
    if candidate.packet_type is PacketType.QUOTATION and "quotation" not in candidate.payload:
        return ValidatorCheck(
            check_id="type_compatibility",
            status=CheckStatus.FAIL,
            detail="quotation packet lacks quotation payload",
        )
    return ValidatorCheck(
        check_id="type_compatibility",
        status=CheckStatus.PASS,
        detail="packet type and payload are compatible",
    )


def _fingerprint(candidate: CandidatePacket) -> tuple[str, str, str, str]:
    return (
        candidate.primary_subject,
        candidate.primary_relation,
        normalize_text(candidate.primary_object).casefold(),
        candidate.polarity,
    )


class IndependentValidator:
    """Validator with separate rules and no authority to canonicalize."""

    configuration_hash = validator_configuration_hash()

    def validate_all(
        self,
        candidates: tuple[CandidatePacket, ...],
        source_repository: SourceRepository,
    ) -> tuple[ValidatorResult, ...]:
        snapshots = {snapshot.source_doc_id: snapshot for snapshot in source_repository.list()}
        exact_groups: dict[tuple[str, str, str, str], list[CandidatePacket]] = defaultdict(list)
        relation_groups: dict[tuple[str, str], list[CandidatePacket]] = defaultdict(list)
        for candidate in candidates:
            exact_groups[_fingerprint(candidate)].append(candidate)
            relation_groups[(candidate.primary_subject, candidate.primary_relation)].append(
                candidate
            )

        results: list[ValidatorResult] = []
        for candidate in candidates:
            snapshot = snapshots[candidate.source_doc_id]
            checks = [
                _check_alignment(candidate, snapshot),
                _check_entailment(candidate),
                _check_relation(candidate),
                _check_negation(candidate),
                _check_temporal(candidate),
                _check_quantities(candidate),
                _check_attribution(candidate, snapshot),
                _check_type(candidate),
            ]
            duplicates = tuple(
                sorted(
                    other.candidate_id
                    for other in exact_groups[_fingerprint(candidate)]
                    if other.candidate_id != candidate.candidate_id
                    and snapshots[other.source_doc_id].source_group == snapshot.source_group
                )
            )
            near_duplicates = tuple(
                sorted(
                    other.candidate_id
                    for other in candidates
                    if other.candidate_id != candidate.candidate_id
                    and other.primary_relation == candidate.primary_relation
                    and _jaccard(other.primary_object, candidate.primary_object) >= 0.9
                    and other.candidate_id not in duplicates
                )
            )
            contradictions = tuple(
                sorted(
                    other.candidate_id
                    for other in relation_groups[
                        (candidate.primary_subject, candidate.primary_relation)
                    ]
                    if other.candidate_id != candidate.candidate_id
                    and normalize_text(other.primary_object).casefold()
                    != normalize_text(candidate.primary_object).casefold()
                    and other.polarity == candidate.polarity
                )
            )
            checks.extend(
                [
                    ValidatorCheck(
                        check_id="duplicate_status",
                        status=CheckStatus.REVIEW if duplicates else CheckStatus.PASS,
                        detail=(
                            f"same-source duplicates: {list(duplicates)}"
                            if duplicates
                            else "no same-source exact duplicate"
                        ),
                    ),
                    ValidatorCheck(
                        check_id="near_duplicate_status",
                        status=(CheckStatus.REVIEW if near_duplicates else CheckStatus.PASS),
                        detail=(
                            f"near duplicates: {list(near_duplicates)}"
                            if near_duplicates
                            else "no near duplicate"
                        ),
                    ),
                    ValidatorCheck(
                        check_id="contradiction_status",
                        status=(CheckStatus.REVIEW if contradictions else CheckStatus.PASS),
                        detail=(
                            f"possible contradictions: {list(contradictions)}"
                            if contradictions
                            else "no contradiction candidate"
                        ),
                    ),
                ]
            )
            if any(check.status is CheckStatus.FAIL for check in checks):
                decision = ValidationDecision.FAIL
            elif any(check.status is CheckStatus.REVIEW for check in checks):
                decision = ValidationDecision.REVIEW
            else:
                decision = ValidationDecision.PASS
            unsigned = {
                "candidate_id": candidate.candidate_id,
                "validator_identity": VALIDATOR_IDENTITY,
                "validator_version": VALIDATOR_VERSION,
                "independent_from_extractor": (
                    candidate.extractor.extractor_identity != VALIDATOR_IDENTITY
                ),
                "decision": decision,
                "checks": [check.model_dump(mode="json") for check in checks],
                "duplicate_candidate_ids": duplicates,
                "near_duplicate_candidate_ids": near_duplicates,
                "contradiction_candidate_ids": contradictions,
            }
            result_hash = f"sha256:{hashlib.sha256(stable_json(unsigned)).hexdigest()}"
            results.append(ValidatorResult.model_validate({**unsigned, "result_hash": result_hash}))
        return tuple(sorted(results, key=lambda item: item.candidate_id))


def write_validation_set(results: tuple[ValidatorResult, ...], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = "\n".join(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        for result in results
    )
    output_path.write_text(rendered + ("\n" if rendered else ""), encoding="utf-8")


def read_validation_set(path: Path) -> tuple[ValidatorResult, ...]:
    if not path.exists():
        return ()
    return tuple(
        ValidatorResult.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def validate_repository(
    candidates: tuple[CandidatePacket, ...],
    source_repository: SourceRepository,
    output_path: Path,
    run_report_path: Path,
) -> tuple[tuple[ValidatorResult, ...], ValidationRun]:
    started = datetime.now(UTC)
    start_ns = time.perf_counter_ns()
    validator = IndependentValidator()
    results = validator.validate_all(candidates, source_repository)
    write_validation_set(results, output_path)
    result_set_hash = f"sha256:{hashlib.sha256(output_path.read_bytes()).hexdigest()}"
    completed = datetime.now(UTC)
    run = ValidationRun(
        run_id=f"validate:{result_set_hash.removeprefix('sha256:')[:20]}",
        started_at=started,
        completed_at=completed,
        candidate_count=len(results),
        pass_count=sum(result.decision is ValidationDecision.PASS for result in results),
        review_count=sum(result.decision is ValidationDecision.REVIEW for result in results),
        fail_count=sum(result.decision is ValidationDecision.FAIL for result in results),
        wall_clock_ms=(time.perf_counter_ns() - start_ns) / 1_000_000,
        configuration_hash=validator.configuration_hash,
        result_set_hash=result_set_hash,
    )
    run_report_path.parent.mkdir(parents=True, exist_ok=True)
    run_report_path.write_text(
        json.dumps(run.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return results, run
