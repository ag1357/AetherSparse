from __future__ import annotations

import json
from pathlib import Path

from aethersparse.observer.models import ArchitectureModule


def test_claim_address_registry_fragment_keeps_unqualified_path_inactive() -> None:
    path = Path("config/architecture/aethercore-v12-claim-address-p4.registry-fragment.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    module = ArchitectureModule.model_validate(payload["module"])

    assert payload["schema_version"] == "aethercore.architecture-registry-fragment.v1"
    assert payload["target_architecture_id"] == "aethercore-v12-semantic-address-v2"
    assert module.module_id == "aethercore.claim-address-direct"
    assert module.status == "inactive"
    assert module.parameter_count == 0
    assert module.calibration_artifact == ("reports/droid/v12/claim-address-p4-qualification.json")
    assert "recall" in payload["activation_condition"]
