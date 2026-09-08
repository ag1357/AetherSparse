from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = (
    ROOT
    / "reports/droid/v15/experimental-shared-encoder-qualification.json"
)


def test_shadow_probe_is_bounded_and_cannot_replace_authority() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["authority"] == "SHADOW_ONLY"
    assert report["runtime_controller_replaced"] is False
    assert report["semantic_address_artifact"] is False
    assert report["deployment"]["enabled"] is False
    assert 2_000_000 <= report["encoder"]["parameter_count"] <= 4_000_000
    assert 250_000 <= report["cog_advisor"]["parameter_count"] <= 1_000_000
    assert report["benchmark"]["evaluation_cases_used"] == 0
    assert report["benchmark"]["final_held_cases_used"] == 0


def test_report_records_encoder_regression_and_int8_agreement() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    learned = report["encoder"]["tuning_metrics"]["recall_at_1"]
    baseline = report["encoder"]["static_hash_baseline"]["recall_at_1"]
    assert learned < baseline
    assert report["encoder"]["float_int8_top1_agreement"] >= 0.95
    assert report["cog_advisor"]["float_int8_disposition_agreement"] >= 0.99
