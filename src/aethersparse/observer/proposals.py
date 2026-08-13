"""Sandboxed offline optimization proposal construction and validation."""

from __future__ import annotations

import hashlib
import json

from aethersparse.observer.models import ArchitectureRegistry, OptimizationProposal


def proposal_id(fields: dict[str, object]) -> str:
    payload = json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str).encode()
    return "proposal:" + hashlib.sha256(payload).hexdigest()[:24]


def make_proposal(
    *,
    registry: ArchitectureRegistry,
    observed_weakness: str,
    affected_module: str,
    evidence: tuple[str, ...],
    proposed_intervention: str,
    expected_benefit: str,
    expected_compute_change_macs: int,
    expected_storage_change_bytes: int,
    tests_required: tuple[str, ...],
    candidate_version_id: str,
) -> OptimizationProposal:
    if affected_module not in {module.module_id for module in registry.modules}:
        raise ValueError("optimization proposal names an unknown module")
    fields: dict[str, object] = {
        "observed_weakness": observed_weakness,
        "affected_module": affected_module,
        "evidence": evidence,
        "proposed_intervention": proposed_intervention,
        "expected_benefit": expected_benefit,
        "expected_compute_change_macs": expected_compute_change_macs,
        "expected_storage_change_bytes": expected_storage_change_bytes,
        "tests_required": tests_required,
        "candidate_version_id": candidate_version_id,
    }
    return OptimizationProposal(
        proposal_id=proposal_id(fields),
        observed_weakness=observed_weakness,
        affected_module=affected_module,
        evidence=evidence,
        proposed_intervention=proposed_intervention,
        expected_benefit=expected_benefit,
        expected_compute_change_macs=expected_compute_change_macs,
        expected_storage_change_bytes=expected_storage_change_bytes,
        tests_required=tests_required,
        candidate_version_id=candidate_version_id,
    )
