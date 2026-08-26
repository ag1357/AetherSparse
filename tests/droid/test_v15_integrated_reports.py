from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "reports" / "droid" / "v15"


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_v15_integrated_classification_and_factory_boundary() -> None:
    report = _load(REPORTS / "aethercore-v15-operational-system-qualification.json")
    assert report["classification"] == "V15_READY_WITH_STORAGE_EXPERIMENT_PENDING"
    assert report["factory_handoff_complete"] is True
    handoff = _load(REPORTS / "factory-v15-device-deployment-handoff.json")
    devices = handoff["devices"]
    assert isinstance(devices, dict)
    assert devices["device_b"]["not_device_a"] is True
    assert devices["cardkb2"]["gpio_wiring"] == []
    assert handoff["transport"]["device_a_device_b_gpio"] == []
    assert handoff["long_term_storage_target_gb"] == 256
    assert handoff["test_medium_gb"] == 128


def test_v15_registry_selects_frozen_controller_and_pack_v2() -> None:
    registry = _load(ROOT / "config" / "architecture" / "aethercore-v15.registry.json")
    assert registry["controller"]["stored_parameters"] == 1292
    assert registry["controller"]["autonomous_total"] == "242/260"
    assert registry["deployment"]["selected_cache_bytes"] == 2_097_152
    assert registry["deployment"]["projected_resident_bytes"] == 6_421_665


def test_cleanup_preserves_history_and_defers_self_manual() -> None:
    cleanup = _load(REPORTS / "production-cleanup-inventory.json")
    assert cleanup["historical_reproducibility_retained"] is True
    assert cleanup["license_notice_modified"] is False
    assert cleanup["release_tag_created"] is False
    assert cleanup["final_self_manual_pack_created"] is False
