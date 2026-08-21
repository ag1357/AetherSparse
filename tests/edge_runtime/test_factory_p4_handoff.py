from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_factory_handoff_cannot_target_tactility_or_redefine_long_term_storage() -> None:
    handoff = json.loads(
        (ROOT / "reports/droid/v14/factory-p4-handoff.json").read_text(encoding="utf-8")
    )
    assert handoff["target_device"] == {
        "role": "AETHERCORE_ACCESSORY_COMPUTE",
        "board": "SECOND_ESP32_P4_DEVELOPMENT_BOARD",
        "is_tactility_display_appliance": False,
    }
    assert handoff["temporary_storage"]["capacity_gb"] == 128
    assert handoff["temporary_storage"]["purpose"] == "PHYSICAL_QUALIFICATION_TEST_MEDIUM"
    assert handoff["long_term_storage"] == {
        "deployment_class_gb": 256,
        "redefined_by_test_medium": False,
    }
    assert len(handoff["measurements"]) == 10
    assert handoff["gate"] == "READY_FOR_FACTORY_P4"
    assert all(
        row["actual"] is None for row in handoff["prediction_actual_comparison"].values()
    )


def test_factory_handoff_schema_freezes_accessory_and_media_constants() -> None:
    schema = json.loads(
        (
            ROOT / "config/deployment/aethercore-v14-factory-p4-handoff.schema.json"
        ).read_text(encoding="utf-8")
    )
    properties = schema["properties"]
    assert properties["target_device"]["properties"]["is_tactility_display_appliance"] == {
        "const": False
    }
    assert properties["temporary_storage"]["properties"]["capacity_gb"] == {"const": 128}
    assert properties["long_term_storage"]["properties"]["deployment_class_gb"] == {
        "const": 256
    }
