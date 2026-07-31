from __future__ import annotations

from pathlib import Path

import pytest

from aethersparse.gate0.pipeline import (
    build_candidate_and_validation_sets,
    freeze_rules,
    ingest_source_seed,
)


def test_gate0_source_candidate_and_validation_identities_reproduce(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "gate0"
    repository, manifest = ingest_source_seed(
        Path("data/gate0/source_seed.json"),
        data_root,
    )
    first = build_candidate_and_validation_sets(data_root)
    second = build_candidate_and_validation_sets(data_root)

    assert len(repository.list()) == 26
    assert manifest["source_manifest_hash"] == repository.manifest_hash()
    assert first["candidate_count"] == 85
    assert first["validator_pass"] == 38
    assert first["validator_review"] == 46
    assert first["validator_fail"] == 1
    assert first["extraction_run_id"] == second["extraction_run_id"]
    assert first["validation_run_id"] == second["validation_run_id"]


def test_sealed_permission_cannot_bypass_human_development_review(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "gate0"
    ingest_source_seed(Path("data/gate0/source_seed.json"), data_root)
    build_candidate_and_validation_sets(data_root)
    freeze_rules(data_root, sealed_evaluation_permitted=False)

    with pytest.raises(ValueError, match="100 calibration"):
        freeze_rules(data_root, sealed_evaluation_permitted=True)
