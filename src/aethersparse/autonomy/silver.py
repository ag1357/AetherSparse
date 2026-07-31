"""Autonomous real-source silver compilation over frozen Gate 0 snapshots."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from aethersparse.gate0.extractor import read_candidate_set
from aethersparse.gate0.models import CheckStatus, ValidationDecision
from aethersparse.gate0.pipeline import DEFAULT_DATA_ROOT, paths
from aethersparse.gate0.sources import SourceRepository, sha256_text, stable_json
from aethersparse.gate0.validator import read_validation_set

SILVER_POLICY_VERSION = "autonomous-real-source-silver-v1"


def _entry_hash(value: dict[str, object]) -> str:
    return f"sha256:{hashlib.sha256(stable_json(value)).hexdigest()}"


def compile_real_source_silver(
    *,
    gate0_root: Path = DEFAULT_DATA_ROOT,
    output_root: Path = Path("data/autonomy/release/real_source_silver"),
) -> dict[str, object]:
    """Canonicalize only independent all-pass candidates; quarantine the rest."""

    item_paths = paths(gate0_root)
    candidates = read_candidate_set(item_paths["candidates"])
    validations = {
        result.candidate_id: result
        for result in read_validation_set(item_paths["validation"])
    }
    repository = SourceRepository(item_paths["source_root"])
    snapshots = {
        snapshot.source_doc_id: snapshot
        for snapshot in repository.list()
    }
    previous = "GENESIS"
    journal: list[dict[str, object]] = []
    canonical: list[dict[str, object]] = []
    counts: Counter[str] = Counter()
    for sequence, candidate in enumerate(candidates, start=1):
        validation = validations[candidate.candidate_id]
        snapshot = snapshots[candidate.source_doc_id]
        exact_alignment = all(
            snapshot.raw_text[
                claim.alignment.raw_char_start : claim.alignment.raw_char_end
            ]
            == claim.alignment.raw_text
            and sha256_text(claim.alignment.raw_text)
            == claim.alignment.raw_text_hash
            and claim.alignment.source_content_hash == snapshot.raw_content_hash
            for claim in candidate.atomic_claims
        )
        checks_pass = all(
            check.status in {CheckStatus.PASS, CheckStatus.NOT_APPLICABLE}
            for check in validation.checks
        )
        independent = (
            validation.independent_from_extractor
            and validation.validator_identity != candidate.extractor.extractor_identity
        )
        is_canonical = (
            exact_alignment
            and independent
            and validation.decision is ValidationDecision.PASS
            and checks_pass
        )
        disposition = "CANONICAL" if is_canonical else "QUARANTINE"
        reasons = (
            ("ALL_INDEPENDENT_CHECKS_PASS",)
            if is_canonical
            else tuple(
                reason
                for passed, reason in (
                    (exact_alignment, "ATOMIC_ALIGNMENT_NOT_EXACT"),
                    (independent, "VALIDATOR_NOT_INDEPENDENT"),
                    (
                        validation.decision is ValidationDecision.PASS,
                        f"VALIDATOR_{validation.decision.value}",
                    ),
                    (checks_pass, "ONE_OR_MORE_CHECKS_NOT_PASS"),
                )
                if not passed
            )
        )
        unsigned: dict[str, object] = {
            "sequence": sequence,
            "policy_version": SILVER_POLICY_VERSION,
            "candidate_id": candidate.candidate_id,
            "source_revision": candidate.source_revision,
            "source_content_hash": candidate.source_content_hash,
            "disposition": disposition,
            "reasons": reasons,
            "extractor_identity": candidate.extractor.extractor_identity,
            "validator_identity": validation.validator_identity,
            "previous_entry_hash": previous,
        }
        entry = {**unsigned, "entry_hash": _entry_hash(unsigned)}
        previous = str(entry["entry_hash"])
        journal.append(entry)
        counts[disposition] += 1
        if is_canonical:
            canonical.append(candidate.model_dump(mode="json"))

    output_root.mkdir(parents=True, exist_ok=True)
    journal_path = output_root / "autonomous_adjudication_journal.jsonl"
    journal_path.write_text(
        "".join(
            json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
            for entry in journal
        ),
        encoding="utf-8",
    )
    canonical_path = output_root / "canonical_packets.jsonl"
    canonical_path.write_text(
        "".join(
            json.dumps(packet, ensure_ascii=False, sort_keys=True) + "\n"
            for packet in canonical
        ),
        encoding="utf-8",
    )
    manifest = {
        "policy_version": SILVER_POLICY_VERSION,
        "source_manifest_hash": repository.manifest_hash(),
        "source_revision_count": len(snapshots),
        "candidate_count": len(candidates),
        "canonical_count": counts["CANONICAL"],
        "quarantine_count": counts["QUARANTINE"],
        "human_review_required": False,
        "optional_spot_check_available": True,
        "journal_tail_hash": previous,
        "journal_hash": _entry_hash({"entries": journal}),
        "canonical_packet_hash": _entry_hash({"packets": canonical}),
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


__all__ = ["compile_real_source_silver"]
