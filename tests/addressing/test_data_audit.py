from __future__ import annotations

import json
from pathlib import Path


def test_committed_address_audit_reproduces_strict_training_only_baseline() -> None:
    path = Path("reports/droid/v12/address-data-evaluation.json")
    document = json.loads(path.read_text(encoding="utf-8"))

    assert document["status"] == "STRICT_BASELINE_EVIDENCE_REPRODUCED"
    assert document["strict_baseline"]["eligible"] == 695
    assert document["strict_baseline"]["reachable"] == 324
    assert document["strict_baseline"]["residual"] == {
        "EVIDENCE_RETRIEVAL": 8,
        "SEMANTIC_ADDRESS_GENERATION": 355,
        "TOOLSET_CONTROLLER": 1,
        "VALUE_AVAILABILITY": 7,
    }
    assert document["partition_policy"]["sealed_rows_in_qualification"] == 0
    assert document["partition_policy"][
        "evaluation_final_labels_used_for_design_fitting_calibration"
    ] is False
