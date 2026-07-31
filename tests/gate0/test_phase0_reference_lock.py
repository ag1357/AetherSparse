from __future__ import annotations

import hashlib
import json
from pathlib import Path

from aethersparse.compiler import compile_pack
from aethersparse.evaluation import run_evaluation

ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = ROOT / "data" / "baseline_phase0" / "reference_lock.json"


def test_phase0_snapshot_hashes_and_manifest_are_frozen() -> None:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))

    for relative_path, expected_hash in lock["snapshot_sha256"].items():
        assert hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest() == expected_hash

    pack = compile_pack(output_file=None)
    assert pack.manifest.manifest_hash == lock["identity"]["manifest_hash"]
    assert pack.manifest.source_manifest_hash == lock["identity"]["source_manifest_hash"]
    assert pack.manifest.packet_count == lock["identity"]["packet_count"]
    assert pack.manifest.span_count == lock["identity"]["span_count"]


def test_phase0_public_smoke_oracle_remains_exact() -> None:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    report = run_evaluation()
    by_strategy = {result["strategy"]: result for result in report["baselines"]}

    assert (
        by_strategy["top1_template"]["passed"]
        == lock["public_smoke_oracle"]["top1_template_passed"]
    )
    assert (
        by_strategy["compiled_program"]["passed"]
        == lock["public_smoke_oracle"]["compiled_program_passed"]
    )
    assert all(not result["failures"] for result in by_strategy.values())
