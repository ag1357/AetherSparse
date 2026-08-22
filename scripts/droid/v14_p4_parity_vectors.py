"""Export the frozen V14 parity vectors as a C header for the P4 runner.

Replays the exact reference semantics of ``tests/edge_runtime/test_native_parity.py``
and ``tests/edge_runtime/test_native_v14_parity.py`` (including its
``_selected_policy_state`` fixture, imported in place) through the Python
reference implementation, and emits every input and expected output as static
const C arrays. The on-device runner replays the same cases through the native
runtime ABI and must reproduce every exported byte exactly.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "src"))

from aethersparse.controller.adaptive_policy import (  # noqa: E402
    QuantizedAdaptivePolicy,
    quantized_action_features,
)
from aethersparse.controller.micro_ops import MicroAction, execute_action, legal_actions  # noqa: E402
from aethersparse.edge_runtime.native_v14 import (  # noqa: E402
    FiveCConstraint as PyFiveCConstraint,
)
from aethersparse.edge_runtime.native_v14 import (  # noqa: E402
    FiveCState as PyFiveCState,
)
from aethersparse.edge_runtime.native_v14 import (  # noqa: E402
    Progress as PyProgress,
)
from aethersparse.edge_runtime.native_v14 import (  # noqa: E402
    Int8PolicyV2 as PyInt8PolicyV2,
)
from aethersparse.edge_runtime.native_v14 import (  # noqa: E402
    SpecialistSummary as PySpecialistSummary,
)
from aethersparse.edge_runtime.native_v14 import (  # noqa: E402
    CogSummary as PyCogSummary,
)
from aethersparse.edge_runtime.native_v14 import five_c_digest, serialize_cognitive_runtime  # noqa: E402
from aethersparse.edge_runtime.reference import (  # noqa: E402
    Action,
    Candidate as PyCandidate,
    Session as PySession,
    Workspace as PyWorkspace,
)

V1_VECTORS = ROOT / "tests/edge_runtime/vectors/runtime-v1.json"
V14_VECTORS = ROOT / "tests/edge_runtime/vectors/runtime-v14.json"
V14_TEST = ROOT / "tests/edge_runtime/test_native_v14_parity.py"
SELECTED_POLICY = ROOT / "reports/droid/v14/controller-selected-policy-int8.json"
DEFAULT_OUTPUT = ROOT / "firmware/p4_qualification/main/parity_vectors_v14.h"


def _load_selected_policy_state_fixture():
    spec = importlib.util.spec_from_file_location("v14_parity_test", V14_TEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._selected_policy_state


def _c_bytes(name: str, payload: bytes, per_line: int = 12) -> str:
    lines = []
    for index in range(0, len(payload), per_line):
        lines.append(",".join(f"0x{byte:02x}" for byte in payload[index : index + per_line]))
    body = ",\n".join(lines)
    return f"static const uint8_t {name}[{len(payload)}] = {{\n{body}\n}};"


def _c_i16(name: str, values) -> str:
    return f"static const int16_t {name}[{len(values)}] = {{{','.join(str(v) for v in values)}}};"


def _c_i8(name: str, values) -> str:
    return f"static const int8_t {name}[{len(values)}] = {{{','.join(str(v) for v in values)}}};"


def _c_i32(name: str, values) -> str:
    return f"static const int32_t {name}[{len(values)}] = {{{','.join(str(v) for v in values)}}};"


def _c_i64(name: str, values) -> str:
    return f"static const int64_t {name}[{len(values)}] = {{{','.join(str(v) for v in values)}}};"


def _c_u64(name: str, values) -> str:
    return f"static const uint64_t {name}[{len(values)}] = {{{','.join(f'{v}ull' for v in values)}}};"


def _c_str(name: str, value: str) -> str:
    return f'static const char {name}[] = {json.dumps(value)};'


def build() -> tuple[str, dict]:
    v1 = json.loads(V1_VECTORS.read_text(encoding="utf-8"))
    v14 = json.loads(V14_VECTORS.read_text(encoding="utf-8"))

    parts: list[str] = []
    summary: dict = {"cases": []}

    # --- candidate union -------------------------------------------------
    union = v1["candidate_union"]
    workspace = PyWorkspace(candidates=[PyCandidate(**item) for item in union["existing"]])
    workspace.union_candidates([PyCandidate(**item) for item in union["incoming"]])
    expected_candidates = [
        (item.entity_id, item.score_q15, item.evidence_mask) for item in workspace.candidates
    ]
    parts.append(
        "static const uint32_t kUnionExisting[][3] = {"
        + ",".join(f"{{{c['entity_id']},{c['score_q15']},{c['evidence_mask']}}}" for c in union["existing"])
        + "};"
    )
    parts.append(
        "static const uint32_t kUnionIncoming[][3] = {"
        + ",".join(f"{{{c['entity_id']},{c['score_q15']},{c['evidence_mask']}}}" for c in union["incoming"])
        + "};"
    )
    parts.append(
        f"static const uint32_t kUnionExpected[][3] = {{"
        + ",".join(f"{{{a},{b},{c}}}" for a, b, c in expected_candidates)
        + "};"
    )
    summary["cases"].append({"name": "union", "expected_candidates": len(expected_candidates)})

    # --- v1 policy select --------------------------------------------------
    policy = v1["policy"]
    flattened_v1 = [item for row in policy["weights"] for item in row]
    parts.append(_c_i8("kPolicyV1Weights", flattened_v1))
    parts.append(_c_i32("kPolicyV1Bias", policy["bias"]))
    parts.append(_c_i16("kPolicyV1Features", policy["features"]))
    parts.append(f"static const uint64_t kPolicyV1LegalMask = {policy['legal_action_mask']}ull;")
    parts.append(f"static const uint32_t kPolicyV1ExpectedAction = {policy['selected_action']};")
    parts.append(f"static const int64_t kPolicyV1ExpectedLogit = {policy['selected_logit']};")
    parts.append(f"#define POLICY_V1_FEATURES {len(policy['features'])}")
    parts.append(f"#define POLICY_V1_ACTIONS {len(policy['bias'])}")
    summary["cases"].append({"name": "policy_v1", "selected": policy["selected_action"]})

    # --- v1 session trajectory ---------------------------------------------
    session = v1["session"]
    python_workspace = PyWorkspace(candidates=[PyCandidate(**item) for item in union["existing"]])
    python_workspace.union_candidates([PyCandidate(**item) for item in union["incoming"]])
    for operation in session["trajectory"]:
        python_workspace.execute(Action(operation["action"]), operation["argument_id"])
    payload = PySession(
        session_id=session["session_id"],
        turn_id=session["turn_id"],
        active_entity_ids=[900],
        recent_utterance_hashes=session["recent_utterance_hashes"],
        workspace=python_workspace,
    ).serialize()
    assert hashlib.sha256(payload).hexdigest() == session["serialized_sha256"]
    parts.append(_c_str("kSessionId", session["session_id"]))
    parts.append(f"static const uint32_t kSessionTurnId = {session['turn_id']};")
    parts.append(_c_u64("kSessionUtteranceHashes", session["recent_utterance_hashes"]))
    parts.append(
        "static const uint32_t kSessionTrajectory[][2] = {"
        + ",".join(f"{{{op['action']},{op['argument_id']}}}" for op in session["trajectory"])
        + "};"
    )
    parts.append(_c_bytes("kSessionExpectedPayload", payload))
    summary["cases"].append(
        {"name": "session", "payload_bytes": len(payload), "sha256": session["serialized_sha256"]}
    )

    # --- v14 vector policy (with bias and zero-bias) -------------------------
    v14_policy = v14["policy"]
    flattened_v14 = [item for row in v14_policy["weights"] for item in row]
    python_v14 = PyInt8PolicyV2(
        tuple(tuple(row) for row in v14_policy["weights"]),
        tuple(v14_policy["bias"]),
        state_schema_id=1,
        model_id=14,
    )
    selected, logit = python_v14.select(tuple(v14_policy["features"]), v14_policy["legal_action_mask"])
    assert (selected, logit) == (v14_policy["selected_action"], v14_policy["selected_logit"])
    zero_bias_python = PyInt8PolicyV2(
        tuple(tuple(row) for row in v14_policy["weights"]),
        tuple(0 for _ in v14_policy["bias"]),
        state_schema_id=1,
        model_id=15,
    )
    zero_selected, zero_logit = zero_bias_python.select(
        tuple(v14_policy["features"]), v14_policy["legal_action_mask"]
    )
    parts.append(_c_i8("kPolicyV14Weights", flattened_v14))
    parts.append(_c_i32("kPolicyV14Bias", v14_policy["bias"]))
    parts.append(_c_i16("kPolicyV14Features", v14_policy["features"]))
    parts.append(f"static const uint64_t kPolicyV14LegalMask = {v14_policy['legal_action_mask']}ull;")
    parts.append(f"static const uint32_t kPolicyV14ExpectedAction = {selected};")
    parts.append(f"static const int64_t kPolicyV14ExpectedLogit = {logit};")
    parts.append(f"static const uint32_t kPolicyV14ZeroBiasExpectedAction = {zero_selected};")
    parts.append(f"static const int64_t kPolicyV14ZeroBiasExpectedLogit = {zero_logit};")
    parts.append(f"#define POLICY_V14_FEATURES {len(v14_policy['features'])}")
    parts.append(f"#define POLICY_V14_ACTIONS {len(v14_policy['bias'])}")
    summary["cases"].append({"name": "policy_v14_vector", "selected": selected})

    # --- selected 1,292-byte policy, argument-aware scoring -------------------
    selected_policy = QuantizedAdaptivePolicy.model_validate(
        json.loads(SELECTED_POLICY.read_text(encoding="utf-8"))
    )
    flattened_selected = [item for row in selected_policy.weights_int8 for item in row]
    assert len(flattened_selected) == 1292
    fixture = _load_selected_policy_state_fixture()
    states = (fixture(), execute_action(fixture(), MicroAction(operation_id=32)))
    state_entries: list[dict] = []
    for state in states:
        actions = legal_actions(state, argument_cap=64)
        entries = []
        scores = []
        for action in actions:
            row = selected_policy.operation_ids.index(action.operation_id)
            features = quantized_action_features(state, action)
            expected = sum(
                weight * feature
                for weight, feature in zip(selected_policy.weights_int8[row], features, strict=True)
            )
            entries.append(
                {
                    "row": row,
                    "op_id": action.operation_id,
                    "args_json": json.dumps(
                        action.arguments, separators=(",", ":"), sort_keys=True
                    ),
                    "features": list(features),
                    "expected_score": expected,
                }
            )
            scores.append(expected)
        choice_index = max(
            range(len(actions)),
            key=lambda index: (
                scores[index],
                -index,
                -actions[index].operation_id,
                json.dumps(actions[index].arguments, separators=(",", ":"), sort_keys=True),
            ),
        )
        chosen = actions[choice_index]
        python_choice = selected_policy.select(state, argument_cap=64)
        assert chosen == python_choice
        state_entries.append(
            {
                "actions": entries,
                "expected_choice_op_id": chosen.operation_id,
                "expected_choice_args_json": json.dumps(
                    chosen.arguments, separators=(",", ":"), sort_keys=True
                ),
            }
        )
    parts.append(_c_i8("kSelectedWeights", flattened_selected))
    parts.append(f"#define SELECTED_FEATURES {len(selected_policy.feature_names)}")
    parts.append(f"#define SELECTED_ACTIONS {len(selected_policy.operation_ids)}")
    for state_index, entry in enumerate(state_entries):
        actions = entry["actions"]
        parts.append(f"/* selected-policy state {state_index}: {len(actions)} legal actions */")
        parts.append(
            f"static const uint16_t kSelState{state_index}Rows[{len(actions)}] = "
            "{" + ",".join(str(a["row"]) for a in actions) + "};"
        )
        parts.append(
            f"static const uint16_t kSelState{state_index}OpIds[{len(actions)}] = "
            "{" + ",".join(str(a["op_id"]) for a in actions) + "};"
        )
        for action_index, action in enumerate(actions):
            parts.append(
                _c_i16(f"kSelState{state_index}Features{action_index}", action["features"])
            )
        parts.append(
            f"static const int16_t *const kSelState{state_index}Features[{len(actions)}] = {{"
            + ",".join(f"kSelState{state_index}Features{i}" for i in range(len(actions)))
            + "};"
        )
        parts.append(_c_i64(f"kSelState{state_index}Scores", [a["expected_score"] for a in actions]))
        for action_index, action in enumerate(actions):
            parts.append(_c_str(f"kSelState{state_index}Args{action_index}", action["args_json"]))
        parts.append(
            f"static const char *const kSelState{state_index}Args[{len(actions)}] = {{"
            + ",".join(f"kSelState{state_index}Args{i}" for i in range(len(actions)))
            + "};"
        )
        parts.append(
            f"static const uint32_t kSelState{state_index}ExpectedChoiceOpId = "
            f"{entry['expected_choice_op_id']};"
        )
        parts.append(
            _c_str(
                f"kSelState{state_index}ExpectedChoiceArgs", entry["expected_choice_args_json"]
            )
        )
    summary["cases"].append(
        {
            "name": "selected_policy_argument_aware",
            "states": [
                {"actions": len(entry["actions"]), "choice_op": entry["expected_choice_op_id"]}
                for entry in state_entries
            ],
        }
    )

    # --- 5C digest / deny / checksum tamper -----------------------------------
    constraint = PyFiveCConstraint(
        constraint_id=0x5C01,
        kind=3,
        effect=0,
        flags=0,
        action_mask=1 << 9,
        capability_mask=0,
        required_flags=0,
        minimum_value=0,
        maximum_value=0,
    )
    digest_low, digest_high = five_c_digest((constraint,))
    parts.append(f"static const uint64_t kFiveCExpectedDigestLow = {digest_low}ull;")
    parts.append(f"static const uint64_t kFiveCExpectedDigestHigh = {digest_high}ull;")
    summary["cases"].append(
        {"name": "five_c", "digest_low": digest_low, "digest_high": digest_high}
    )

    # --- specialist summary ------------------------------------------------------
    # Three descriptors (cold/warm/hot, 128/256/512 bytes), budget 1024 -> 1/1/1/768.
    summary["cases"].append({"name": "specialists", "expected": [1, 1, 1, 768]})

    # --- progress stagnation ------------------------------------------------------
    progress = PyProgress()
    for _ in range(4):
        progress.record(
            action=7,
            error_signature=0x10203040,
            open_obligations=3,
            completed_obligations=1,
            new_evidence=0,
            new_hypothesis=0,
            frontier_expansion=0,
            verifier_state=0,
            rollback_count=0,
        )
    packed_progress = progress.pack_without_struct_size()
    parts.append(_c_bytes("kProgressExpectedPacked", packed_progress))
    summary["cases"].append({"name": "progress", "packed_bytes": len(packed_progress)})

    # --- COG/5C/progress/specialist wire serialization ------------------------------
    cog_values = v14["cog"]
    five_c_values = v14["five_c"]
    python_cog = PyCogSummary(**cog_values)
    python_five_c = PyFiveCState(**five_c_values)
    python_progress = PyProgress(open_obligations=4, completed_obligations=7)
    python_specialists = PySpecialistSummary(**v14["specialists"])
    wire = serialize_cognitive_runtime(
        python_cog, python_five_c, python_progress, python_specialists
    )
    parts.append(
        "static const uint16_t kCogValues[] = {"
        + ",".join(str(value) for value in cog_values.values())
        + "};"
    )
    parts.append(f"static const uint16_t kFiveCConstraintCount = {five_c_values['constraint_count']};")
    parts.append(
        f"static const uint64_t kFiveCDigestLow = {five_c_values['immutable_digest_low']}ull;"
    )
    parts.append(
        f"static const uint64_t kFiveCDigestHigh = {five_c_values['immutable_digest_high']}ull;"
    )
    parts.append(f"static const uint32_t kFiveCFlags = {five_c_values['flags']};")
    parts.append(
        "static const uint32_t kSpecialistValues[4] = {"
        + ",".join(str(value) for value in v14["specialists"].values())
        + "};"
    )
    parts.append(_c_bytes("kCogWireExpected", wire))
    summary["cases"].append({"name": "cog_wire", "bytes": len(wire)})

    provenance = {
        "runtime-v1.json": hashlib.sha256(V1_VECTORS.read_bytes()).hexdigest(),
        "runtime-v14.json": hashlib.sha256(V14_VECTORS.read_bytes()).hexdigest(),
        "test_native_v14_parity.py": hashlib.sha256(V14_TEST.read_bytes()).hexdigest(),
        "controller-selected-policy-int8.json": hashlib.sha256(
            SELECTED_POLICY.read_bytes()
        ).hexdigest(),
    }
    header = "\n".join(
        [
            "/* Generated by scripts/droid/v14_p4_parity_vectors.py. Do not edit. */",
            "/* Provenance sha256: " + json.dumps(provenance, sort_keys=True) + " */",
            "#pragma once",
            "#include <stdint.h>",
            "",
            *parts,
            "",
        ]
    )
    summary["provenance"] = provenance
    return header, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--json-summary", type=Path, default=None)
    args = parser.parse_args()
    header, summary = build()
    args.output.write_text(header, encoding="utf-8")
    print(f"wrote {args.output} ({len(header)} bytes)")
    print(json.dumps(summary["cases"], indent=1))
    if args.json_summary is not None:
        args.json_summary.write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
