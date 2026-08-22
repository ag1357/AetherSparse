"""Contract test for the P4 on-device parity vector exporter.

The exported header is the on-device counterpart of the frozen host parity
suites; this test pins the values the firmware must reproduce.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).parents[2]
EXPORTER = ROOT / "scripts/droid/v14_p4_parity_vectors.py"


def _load_exporter():
    spec = importlib.util.spec_from_file_location("v14_p4_parity_vectors", EXPORTER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exported_vectors_match_frozen_references() -> None:
    module = _load_exporter()
    header, summary = module.build()

    v1 = json.loads((ROOT / "tests/edge_runtime/vectors/runtime-v1.json").read_text())
    v14 = json.loads((ROOT / "tests/edge_runtime/vectors/runtime-v14.json").read_text())

    # v1 frozen policy outcome
    assert f"kPolicyV1ExpectedAction = {v1['policy']['selected_action']}" in header
    assert f"kPolicyV1ExpectedLogit = {v1['policy']['selected_logit']}" in header
    # v1 frozen session payload round-trips against the recorded sha256
    assert "kSessionExpectedPayload[836]" in header

    # v14 vector policy outcome (bias and zero-bias variants)
    assert f"kPolicyV14ExpectedAction = {v14['policy']['selected_action']}" in header
    assert f"kPolicyV14ExpectedLogit = {v14['policy']['selected_logit']}" in header
    assert "kPolicyV14ZeroBiasExpectedAction" in header

    # selected policy: exactly 1,292 int8 weights, argument-aware per-state cases
    assert header.count("kSelectedWeights[1292]") == 1
    states = next(c for c in summary["cases"] if c["name"] == "selected_policy_argument_aware")
    assert len(states["states"]) == 2
    for state in states["states"]:
        assert state["actions"] > 0
    assert "kSelState0ExpectedChoiceOpId" in header
    assert "kSelState1ExpectedChoiceOpId" in header

    # 5C digest, progress packing, and the 180-byte cognitive wire image
    assert "kFiveCExpectedDigestLow" in header
    assert "kProgressExpectedPacked[44]" in header
    assert "kCogWireExpected[180]" in header

    # provenance records the frozen inputs
    provenance = summary["provenance"]
    assert set(provenance) == {
        "runtime-v1.json",
        "runtime-v14.json",
        "test_native_v14_parity.py",
        "controller-selected-policy-int8.json",
    }
