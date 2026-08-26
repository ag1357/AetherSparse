import json
from pathlib import Path

ROOT = Path(__file__).parents[2]


def _json(path: str) -> dict[str, object]:
    return json.loads((ROOT / path).read_text())


def test_debt_ledger_retains_exact_dagger_negative() -> None:
    report = _json("reports/droid/v15/architecture-debt-ledger.json")
    entries = {item["id"]: item for item in report["entries"]}  # type: ignore[index]
    dagger = entries["dagger_roll_in"]
    assert dagger["status"] == "TESTED_REJECTED"
    assert "243" in dagger["result"]
    assert "231/260" in dagger["result"]
    assert "242/260" in dagger["result"]


def test_rejected_context_head_cannot_replace_working_v14_policy() -> None:
    report = _json("reports/droid/v15/specialist-capacity-qualification.json")
    assert report["status"] == "REJECTED_NO_CAPABILITY_BYTE_GAIN"
    candidate = report["candidate"]  # type: ignore[index]
    selected = report["selected"]  # type: ignore[index]
    assert candidate["autonomous"]["successful"] == 239
    assert candidate["autonomous"]["by_partition"]["tuning"]["successful"] == 129
    assert selected["autonomous"]["successful"] == 242
    assert selected["autonomous"]["by_partition"]["tuning"]["successful"] == 138


def test_input_observer_contract_never_logs_user_text() -> None:
    report = _json("reports/droid/v15/input-observer-qualification.json")
    assert report["natural_input"]["passed"] == 21  # type: ignore[index]
    assert report["observer"]["event_wire_bytes"] == 40  # type: ignore[index]
    assert report["observer"]["user_text_logged"] is False  # type: ignore[index]
    assert report["observer"]["factual_authority"] is False  # type: ignore[index]
