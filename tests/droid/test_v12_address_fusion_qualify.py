from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.droid.v12_address_fusion_qualify import qualify

REPOSITORY = Path(__file__).resolve().parents[2]


def _inputs() -> tuple[Path, Path, Path]:
    root = REPOSITORY / "reports" / "droid" / "v11"
    return (
        root / "semantic-address-plane-qualification.json",
        root / "specialist-readiness.json",
        root / "entity-specialist-baselines.json",
    )


def test_qualification_records_current_gate_without_inventing_k_metrics() -> None:
    report = qualify(*_inputs())

    assert report["decision"] == "ADDRESS_SUBSTRATE_INADEQUATE"
    assert report["truth_boundary"]["sealed_partitions_consumed"] == []
    measurement = report["current_lawful_measurement"]
    assert measurement["candidate_completeness"]["retained_post_cap_at_most_8"] == {
        "complete": 37,
        "rate": 37 / 193,
    }
    assert measurement["candidate_completeness"]["k32"] is None
    assert measurement["mention_aligned_entity_recall"]["at16"] is None
    assert report["successive_halving"]["started"] is False
    assert report["successive_halving"]["requested_parameter_counts"] == [
        250000,
        1000000,
        3000000,
        5000000,
    ]


def test_qualification_rejects_protected_partition_evidence(tmp_path: Path) -> None:
    semantic_path, readiness_path, baseline_path = _inputs()
    semantic = json.loads(semantic_path.read_text(encoding="utf-8"))
    semantic["integrity"]["split_audit"]["partitions_present"].append("evaluation")
    corrupt = tmp_path / "semantic.json"
    corrupt.write_text(json.dumps(semantic), encoding="utf-8")

    with pytest.raises(ValueError, match="development and tuning only"):
        qualify(corrupt, readiness_path, baseline_path)
