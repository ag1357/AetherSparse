from __future__ import annotations

import ctypes
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aethersparse.controller.adaptive_policy import (
    QuantizedAdaptivePolicy,
    quantized_action_features,
)
from aethersparse.controller.micro_ops import MicroAction, MicroState, execute_action, legal_actions
from aethersparse.edge_runtime.native_v14 import (
    PROGRESS_STAGNATED,
    five_c_digest,
    serialize_cognitive_runtime,
)
from aethersparse.edge_runtime.native_v14 import (
    CogSummary as PyCogSummary,
)
from aethersparse.edge_runtime.native_v14 import (
    FiveCConstraint as PyFiveCConstraint,
)
from aethersparse.edge_runtime.native_v14 import FiveCState as PyFiveCState
from aethersparse.edge_runtime.native_v14 import Int8PolicyV2 as PyInt8PolicyV2
from aethersparse.edge_runtime.native_v14 import Progress as PyProgress
from aethersparse.edge_runtime.native_v14 import SpecialistSummary as PySpecialistSummary

ROOT = Path(__file__).parents[2]
VECTOR_PATH = Path(__file__).parent / "vectors" / "runtime-v14.json"


class CogSummary(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("schema_version", ctypes.c_uint16),
        ("open_goals", ctypes.c_uint16),
        ("mandatory_open", ctypes.c_uint16),
        ("mandatory_satisfied", ctypes.c_uint16),
        ("blocked_or_failed", ctypes.c_uint16),
        ("invariant_violations", ctypes.c_uint16),
        ("active_hypotheses", ctypes.c_uint16),
        ("competing_hypotheses", ctypes.c_uint16),
        ("contradictions", ctypes.c_uint16),
        ("evidence_count", ctypes.c_uint16),
        ("unresolved_count", ctypes.c_uint16),
        ("open_frontier", ctypes.c_uint16),
        ("observed_state_count", ctypes.c_uint16),
        ("completion_permille", ctypes.c_uint16),
        ("stagnant_steps", ctypes.c_uint16),
        ("repeated_error_count", ctypes.c_uint16),
        ("repeated_action_count", ctypes.c_uint16),
        ("verifier_state_code", ctypes.c_uint16),
        ("halt_success_legal", ctypes.c_uint16),
        ("reserved", ctypes.c_uint16 * 3),
    ]


class FiveCState(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("schema_version", ctypes.c_uint16),
        ("constraint_count", ctypes.c_uint16),
        ("immutable_digest_low", ctypes.c_uint64),
        ("immutable_digest_high", ctypes.c_uint64),
        ("flags", ctypes.c_uint32),
        ("violation_count", ctypes.c_uint32),
        ("last_violation_id", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 7),
    ]


class FiveCConstraint(ctypes.Structure):
    _fields_ = [
        ("constraint_id", ctypes.c_uint32),
        ("kind", ctypes.c_uint8),
        ("effect", ctypes.c_uint8),
        ("flags", ctypes.c_uint8),
        ("reserved8", ctypes.c_uint8),
        ("action_mask", ctypes.c_uint64),
        ("capability_mask", ctypes.c_uint32),
        ("required_flags", ctypes.c_uint32),
        ("minimum_value", ctypes.c_int32),
        ("maximum_value", ctypes.c_int32),
    ]


class FiveCRequest(ctypes.Structure):
    _fields_ = [
        ("action", ctypes.c_uint32),
        ("capability_mask", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("metric_value", ctypes.c_int32),
    ]


class SpecialistDescriptor(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("kind", ctypes.c_uint16),
        ("activation_state", ctypes.c_uint16),
        ("specialist_id", ctypes.c_uint64),
        ("parameter_family_id", ctypes.c_uint64),
        ("input_schema_id", ctypes.c_uint32),
        ("output_schema_id", ctypes.c_uint32),
        ("activation_cost_ops", ctypes.c_uint32),
        ("ram_requirement_bytes", ctypes.c_uint32),
        ("storage_requirement_bytes", ctypes.c_uint32),
        ("expected_latency_us", ctypes.c_uint32),
        ("allowed_action_mask", ctypes.c_uint64),
        ("constraint_mask", ctypes.c_uint64),
        ("calibration_state_id", ctypes.c_uint32),
        ("provenance_behavior", ctypes.c_uint32),
    ]


class SpecialistSummary(ctypes.Structure):
    _fields_ = [
        ("cold_count", ctypes.c_uint32),
        ("warm_count", ctypes.c_uint32),
        ("hot_count", ctypes.c_uint32),
        ("resident_ram_bytes", ctypes.c_uint32),
    ]


class Progress(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("open_obligations", ctypes.c_uint16),
        ("completed_obligations", ctypes.c_uint16),
        ("new_evidence_count", ctypes.c_uint16),
        ("new_hypothesis_count", ctypes.c_uint16),
        ("frontier_expansion_count", ctypes.c_uint16),
        ("repeated_action_count", ctypes.c_uint16),
        ("verifier_state", ctypes.c_uint16),
        ("rollback_count", ctypes.c_uint16),
        ("repeated_error_signature", ctypes.c_uint32),
        ("stagnation_cycles", ctypes.c_uint16),
        ("flags", ctypes.c_uint16),
        ("last_action", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 4),
    ]


class Int8PolicyV2(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("feature_count", ctypes.c_uint16),
        ("action_count", ctypes.c_uint16),
        ("parameter_count", ctypes.c_uint32),
        ("state_schema_id", ctypes.c_uint32),
        ("model_id", ctypes.c_uint64),
        ("weights", ctypes.POINTER(ctypes.c_int8)),
        ("bias", ctypes.POINTER(ctypes.c_int32)),
    ]


def compile_runtime(tmp_path: Path) -> ctypes.CDLL:
    compiler = shutil.which("g++")
    if compiler is None:
        pytest.skip("g++ is unavailable")
    output = tmp_path / "libaethercore_runtime.so"
    subprocess.run(
        [
            compiler,
            "-I",
            str(ROOT / "native/aethercore_runtime/include"),
            "-std=c++17",
            "-O2",
            "-fno-exceptions",
            "-fno-rtti",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-fPIC",
            "-shared",
            str(ROOT / "native/aethercore_runtime/src/aethercore_runtime.cpp"),
            "-o",
            str(output),
        ],
        check=True,
    )
    library = ctypes.CDLL(str(output))
    library.ac_policy_select_i8_v2.argtypes = [
        ctypes.POINTER(Int8PolicyV2),
        ctypes.POINTER(ctypes.c_int16),
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_int64),
    ]
    library.ac_policy_score_candidate_i8_v2.argtypes = [
        ctypes.POINTER(Int8PolicyV2),
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_int16),
        ctypes.POINTER(ctypes.c_int64),
    ]
    library.ac_5c_check_v1.argtypes = [
        ctypes.POINTER(FiveCState),
        ctypes.POINTER(FiveCConstraint),
        ctypes.c_size_t,
        ctypes.POINTER(FiveCRequest),
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    library.ac_5c_digest_v1.argtypes = [
        ctypes.POINTER(FiveCConstraint),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_uint64),
        ctypes.POINTER(ctypes.c_uint64),
    ]
    library.ac_specialist_summarize_v1.argtypes = [
        ctypes.POINTER(SpecialistDescriptor),
        ctypes.c_size_t,
        ctypes.c_uint32,
        ctypes.POINTER(SpecialistSummary),
    ]
    library.ac_progress_record_v1.argtypes = [
        ctypes.POINTER(Progress),
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint16,
        ctypes.c_uint16,
        ctypes.c_uint16,
        ctypes.c_uint16,
        ctypes.c_uint16,
        ctypes.c_uint16,
        ctypes.c_uint16,
    ]
    library.ac_cog_runtime_serialize_v1.argtypes = [
        ctypes.POINTER(CogSummary),
        ctypes.POINTER(FiveCState),
        ctypes.POINTER(Progress),
        ctypes.POINTER(SpecialistSummary),
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    return library


def test_v14_policy_supports_all_34_actions_bit_exactly(tmp_path: Path) -> None:
    library = compile_runtime(tmp_path)
    vector = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))["policy"]
    python_policy = PyInt8PolicyV2(
        tuple(tuple(row) for row in vector["weights"]),
        tuple(vector["bias"]),
        state_schema_id=1,
        model_id=14,
    )
    flattened = [item for row in vector["weights"] for item in row]
    weights = (ctypes.c_int8 * len(flattened))(*flattened)
    bias = (ctypes.c_int32 * len(vector["bias"]))(*vector["bias"])
    features = (ctypes.c_int16 * len(vector["features"]))(*vector["features"])
    native_policy = Int8PolicyV2(
        ctypes.sizeof(Int8PolicyV2),
        len(vector["features"]),
        len(vector["bias"]),
        len(flattened),
        1,
        14,
        weights,
        bias,
    )
    selected = ctypes.c_uint32()
    logit = ctypes.c_int64()
    assert library.ac_policy_validate_i8_v2(ctypes.byref(native_policy)) == 0
    assert library.ac_policy_macs_i8_v2(ctypes.byref(native_policy)) == len(flattened)
    assert library.ac_policy_select_i8_v2(
        ctypes.byref(native_policy),
        features,
        vector["legal_action_mask"],
        ctypes.byref(selected),
        ctypes.byref(logit),
    ) == 0
    assert (selected.value, logit.value) == python_policy.select(
        tuple(vector["features"]), vector["legal_action_mask"]
    ) == (vector["selected_action"], vector["selected_logit"])

    zero_bias_policy = Int8PolicyV2(
        ctypes.sizeof(Int8PolicyV2),
        len(vector["features"]),
        len(vector["bias"]),
        len(flattened),
        1,
        15,
        weights,
        None,
    )
    zero_bias_python = PyInt8PolicyV2(
        tuple(tuple(row) for row in vector["weights"]),
        tuple(0 for _ in vector["bias"]),
        state_schema_id=1,
        model_id=15,
    )
    assert library.ac_policy_validate_i8_v2(ctypes.byref(zero_bias_policy)) == 0
    assert library.ac_policy_select_i8_v2(
        ctypes.byref(zero_bias_policy),
        features,
        vector["legal_action_mask"],
        ctypes.byref(selected),
        ctypes.byref(logit),
    ) == 0
    assert (selected.value, logit.value) == zero_bias_python.select(
        tuple(vector["features"]), vector["legal_action_mask"]
    )


def _selected_policy_state() -> MicroState:
    return MicroState(
        case_id="case:selected-native-binding",
        frame={
            "answer_shape": "definition",
            "candidate_entity_ids": [],
            "entity_mentions": [
                {
                    "selected_entity_id": "entity:good",
                    "selected_confidence": 0.95,
                    "candidates": [
                        {"entity_id": "entity:good", "confidence": 0.95},
                        {"entity_id": "entity:other", "confidence": 0.20},
                    ],
                }
            ],
            "requested_relation_families": ["definition"],
            "required_facets": ["subject", "relation", "object", "source"],
        },
        claims=(
            {
                "claim_id": "claim:bad",
                "subject_entity_id": "entity:other",
                "relation_family": "definition",
                "answer_shape": "definition",
                "object_value": "wrong value",
                "source_span_ids": ["span:bad"],
                "confidence": 0.99,
            },
            {
                "claim_id": "claim:good",
                "subject_entity_id": "entity:good",
                "relation_family": "definition",
                "answer_shape": "definition",
                "object_value": "grounded value",
                "source_span_ids": ["span:good"],
                "confidence": 0.80,
            },
        ),
        source_spans=(
            {"span_id": "span:bad", "text": "wrong value"},
            {"span_id": "span:good", "text": "The subject has a grounded value."},
        ),
    )


def test_exact_selected_int8_policy_scores_argument_aware_candidates_in_native(
    tmp_path: Path,
) -> None:
    """Bind the selected 1,292 bytes, not merely a same-shaped fixture."""

    library = compile_runtime(tmp_path)
    payload = json.loads(
        (ROOT / "reports/droid/v14/controller-selected-policy-int8.json").read_text(
            encoding="utf-8"
        )
    )
    selected_policy = QuantizedAdaptivePolicy.model_validate(payload)
    flattened = [item for row in selected_policy.weights_int8 for item in row]
    weights = (ctypes.c_int8 * len(flattened))(*flattened)
    native_policy = Int8PolicyV2(
        ctypes.sizeof(Int8PolicyV2),
        len(selected_policy.feature_names),
        len(selected_policy.operation_ids),
        len(flattened),
        14,
        0x987D28FC667044BE,
        weights,
        None,
    )
    assert library.ac_policy_validate_i8_v2(ctypes.byref(native_policy)) == 0
    assert library.ac_policy_macs_i8_v2(ctypes.byref(native_policy)) == 1292

    states = (
        _selected_policy_state(),
        execute_action(_selected_policy_state(), MicroAction(operation_id=32)),
    )
    for state in states:
        actions = legal_actions(state, argument_cap=64)
        native_scores: list[int] = []
        for action in actions:
            row = selected_policy.operation_ids.index(action.operation_id)
            features_value = quantized_action_features(state, action)
            features = (ctypes.c_int16 * len(features_value))(*features_value)
            score = ctypes.c_int64()
            assert library.ac_policy_score_candidate_i8_v2(
                ctypes.byref(native_policy), row, features, ctypes.byref(score)
            ) == 0
            expected = sum(
                weight * feature
                for weight, feature in zip(
                    selected_policy.weights_int8[row], features_value, strict=True
                )
            )
            assert score.value == expected
            native_scores.append(score.value)
        native_choice = max(
            enumerate(actions),
            key=lambda item: (
                native_scores[item[0]],
                -item[0],
                -item[1].operation_id,
                json.dumps(item[1].arguments, separators=(",", ":"), sort_keys=True),
            ),
        )[1]
        assert native_choice == selected_policy.select(state, argument_cap=64)


def test_native_5c_denies_bypass_without_mutating_root(tmp_path: Path) -> None:
    library = compile_runtime(tmp_path)
    constraint = FiveCConstraint(
        0x5C01,
        3,
        0,
        0,
        0,
        1 << 9,
        0,
        0,
        0,
        0,
    )
    digest_low = ctypes.c_uint64()
    digest_high = ctypes.c_uint64()
    assert library.ac_5c_digest_v1(
        ctypes.byref(constraint), 1, ctypes.byref(digest_low), ctypes.byref(digest_high)
    ) == 0
    assert (digest_low.value, digest_high.value) == five_c_digest(
        (
            PyFiveCConstraint(
                constraint_id=0x5C01,
                kind=3,
                effect=0,
                flags=0,
                action_mask=1 << 9,
                capability_mask=0,
                required_flags=0,
                minimum_value=0,
                maximum_value=0,
            ),
        )
    )
    state = FiveCState(
        ctypes.sizeof(FiveCState),
        1,
        1,
        digest_low.value,
        digest_high.value,
        7,
        0,
        0,
    )
    request = FiveCRequest(9, 0, 0, 0)
    allowed = ctypes.c_uint32()
    violation = ctypes.c_uint32()
    before = bytes(state)
    assert library.ac_5c_check_v1(
        ctypes.byref(state),
        ctypes.byref(constraint),
        1,
        ctypes.byref(request),
        ctypes.byref(allowed),
        ctypes.byref(violation),
    ) == 0
    assert allowed.value == 0
    assert violation.value == 0x5C01
    assert bytes(state) == before
    constraint.action_mask = 0
    assert library.ac_5c_check_v1(
        ctypes.byref(state),
        ctypes.byref(constraint),
        1,
        ctypes.byref(request),
        ctypes.byref(allowed),
        ctypes.byref(violation),
    ) == 5


def test_specialist_activation_and_progress_match_python(tmp_path: Path) -> None:
    library = compile_runtime(tmp_path)
    descriptors = (SpecialistDescriptor * 3)(
        SpecialistDescriptor(72, 2, 0, 1, 99, 1, 2, 10, 128, 0, 5, 1, 1, 1, 1),
        SpecialistDescriptor(72, 2, 1, 2, 99, 1, 2, 10, 256, 0, 5, 1, 1, 2, 1),
        SpecialistDescriptor(72, 2, 2, 3, 99, 1, 2, 10, 512, 0, 5, 1, 1, 3, 1),
    )
    native_summary = SpecialistSummary()
    assert library.ac_specialist_summarize_v1(
        descriptors, len(descriptors), 1024, ctypes.byref(native_summary)
    ) == 0
    assert (
        native_summary.cold_count,
        native_summary.warm_count,
        native_summary.hot_count,
        native_summary.resident_ram_bytes,
    ) == (1, 1, 1, 768)

    native = Progress()
    native.struct_size = ctypes.sizeof(Progress)
    python = PyProgress()
    arguments = dict(
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
    for _ in range(4):
        assert library.ac_progress_record_v1(ctypes.byref(native), *arguments.values()) == 0
        python.record(**arguments)
    assert native.flags & PROGRESS_STAGNATED
    assert bytes(native)[4:] == python.pack_without_struct_size()


def test_compact_cog_5c_progress_wire_is_bit_exact(tmp_path: Path) -> None:
    library = compile_runtime(tmp_path)
    vector = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))
    python_cog = PyCogSummary(**vector["cog"])
    assert PyCogSummary.from_packed_u16(python_cog.packed_u16()) == python_cog
    native_cog = CogSummary(ctypes.sizeof(CogSummary), 1, *vector["cog"].values())
    five_c_values = vector["five_c"]
    python_five_c = PyFiveCState(**five_c_values)
    native_five_c = FiveCState(
        ctypes.sizeof(FiveCState),
        1,
        five_c_values["constraint_count"],
        five_c_values["immutable_digest_low"],
        five_c_values["immutable_digest_high"],
        five_c_values["flags"],
        0,
        0,
    )
    python_progress = PyProgress(open_obligations=4, completed_obligations=7)
    native_progress = Progress()
    native_progress.struct_size = ctypes.sizeof(Progress)
    native_progress.open_obligations = 4
    native_progress.completed_obligations = 7
    specialist_values = vector["specialists"]
    python_specialists = PySpecialistSummary(**specialist_values)
    native_specialists = SpecialistSummary(*specialist_values.values())
    expected = serialize_cognitive_runtime(
        python_cog, python_five_c, python_progress, python_specialists
    )
    output = (ctypes.c_uint8 * len(expected))()
    written = ctypes.c_size_t()
    assert library.ac_cog_summary_size_v1() == ctypes.sizeof(CogSummary) == 48
    assert library.ac_5c_constraint_size_v1() == ctypes.sizeof(FiveCConstraint) == 32
    assert library.ac_5c_state_size_v1() == ctypes.sizeof(FiveCState) == 64
    assert library.ac_specialist_descriptor_size_v1() == ctypes.sizeof(SpecialistDescriptor) == 72
    assert library.ac_progress_size_v1() == ctypes.sizeof(Progress) == 48
    assert library.ac_cog_runtime_serialized_size_v1() == len(expected) == 180
    assert library.ac_cog_runtime_serialize_v1(
        ctypes.byref(native_cog),
        ctypes.byref(native_five_c),
        ctypes.byref(native_progress),
        ctypes.byref(native_specialists),
        output,
        len(output),
        ctypes.byref(written),
    ) == 0
    assert written.value == len(expected)
    assert bytes(output) == expected
