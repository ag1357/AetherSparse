from __future__ import annotations

import ctypes
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aethersparse.edge_runtime.reference import Action
from aethersparse.edge_runtime.reference import Candidate as PyCandidate
from aethersparse.edge_runtime.reference import Session as PySession
from aethersparse.edge_runtime.reference import Workspace as PyWorkspace

ROOT = Path(__file__).parents[2]
VECTOR_PATH = Path(__file__).parent / "vectors" / "runtime-v1.json"


class Candidate(ctypes.Structure):
    _fields_ = [
        ("entity_id", ctypes.c_uint64),
        ("score_q15", ctypes.c_int32),
        ("evidence_mask", ctypes.c_uint32),
    ]


class Workspace(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("candidate_count", ctypes.c_uint32),
        ("candidates", Candidate * 32),
        ("selected_count", ctypes.c_uint32),
        ("selected_padding", ctypes.c_uint32),
        ("selected_entity_ids", ctypes.c_uint64 * 8),
        ("last_action", ctypes.c_uint32),
        ("step_count", ctypes.c_uint32),
        ("invalid_action_count", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("terminal_disposition", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 8),
    ]


class Session(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("abi_version", ctypes.c_uint32),
        ("session_id", ctypes.c_char * 40),
        ("turn_id", ctypes.c_uint64),
        ("active_entity_ids", ctypes.c_uint64 * 8),
        ("active_entity_count", ctypes.c_uint32),
        ("pending_clarification_count", ctypes.c_uint32),
        ("pending_clarification_ids", ctypes.c_uint64 * 4),
        ("recent_utterance_hashes", ctypes.c_uint64 * 8),
        ("workspace", Workspace),
    ]


class LinearPolicy(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("feature_count", ctypes.c_uint32),
        ("action_count", ctypes.c_uint32),
        ("weights", ctypes.POINTER(ctypes.c_int8)),
        ("bias", ctypes.POINTER(ctypes.c_int32)),
    ]


def _compile(tmp_path: Path) -> ctypes.CDLL:
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
    library.ac_workspace_init_v1.argtypes = [ctypes.POINTER(Workspace)]
    library.ac_union_candidates_v1.argtypes = [
        ctypes.POINTER(Workspace),
        ctypes.POINTER(Candidate),
        ctypes.c_size_t,
    ]
    library.ac_policy_select_v1.argtypes = [
        ctypes.POINTER(LinearPolicy),
        ctypes.POINTER(ctypes.c_int16),
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_int64),
    ]
    library.ac_session_init_v1.argtypes = [ctypes.POINTER(Session), ctypes.c_char_p]
    library.ac_execute_action_v1.argtypes = [
        ctypes.POINTER(Workspace),
        ctypes.c_uint32,
        ctypes.c_uint64,
    ]
    library.ac_session_serialize_v1.argtypes = [
        ctypes.POINTER(Session),
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    library.ac_session_deserialize_v1.argtypes = [
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.POINTER(Session),
    ]
    return library


def test_native_runtime_is_bit_exact_with_frozen_python_reference(tmp_path: Path) -> None:
    library = _compile(tmp_path)
    vectors = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))
    assert library.ac_abi_version() == 1
    assert library.ac_workspace_size_v1() == ctypes.sizeof(Workspace) == 648
    assert library.ac_session_size_v1() == ctypes.sizeof(Session) == 872
    assert library.ac_session_serialized_size_v1() == 836

    union = vectors["candidate_union"]
    python_workspace = PyWorkspace(
        candidates=[PyCandidate(**item) for item in union["existing"]]
    )
    python_workspace.union_candidates([PyCandidate(**item) for item in union["incoming"]])
    native_workspace = Workspace()
    assert library.ac_workspace_init_v1(ctypes.byref(native_workspace)) == 0
    existing = (Candidate * len(union["existing"]))(
        *(Candidate(**item) for item in union["existing"])
    )
    incoming = (Candidate * len(union["incoming"]))(
        *(Candidate(**item) for item in union["incoming"])
    )
    assert library.ac_union_candidates_v1(
        ctypes.byref(native_workspace), existing, len(existing)
    ) == 0
    assert library.ac_union_candidates_v1(
        ctypes.byref(native_workspace), incoming, len(incoming)
    ) == 0
    native_candidates = [
        (
            native_workspace.candidates[index].entity_id,
            native_workspace.candidates[index].score_q15,
            native_workspace.candidates[index].evidence_mask,
        )
        for index in range(native_workspace.candidate_count)
    ]
    assert native_candidates == [
        (item.entity_id, item.score_q15, item.evidence_mask)
        for item in python_workspace.candidates
    ]

    policy_vector = vectors["policy"]
    flattened = [item for row in policy_vector["weights"] for item in row]
    weights = (ctypes.c_int8 * len(flattened))(*flattened)
    bias = (ctypes.c_int32 * len(policy_vector["bias"]))(*policy_vector["bias"])
    features = (ctypes.c_int16 * len(policy_vector["features"]))(*policy_vector["features"])
    policy = LinearPolicy(
        ctypes.sizeof(LinearPolicy), len(features), len(bias), weights, bias
    )
    action = ctypes.c_uint32()
    logit = ctypes.c_int64()
    assert library.ac_policy_select_v1(
        ctypes.byref(policy),
        features,
        policy_vector["legal_action_mask"],
        ctypes.byref(action),
        ctypes.byref(logit),
    ) == 0
    assert (action.value, logit.value) == (
        policy_vector["selected_action"],
        policy_vector["selected_logit"],
    )

    session_vector = vectors["session"]
    native_session = Session()
    assert library.ac_session_init_v1(
        ctypes.byref(native_session), session_vector["session_id"].encode()
    ) == 0
    native_session.turn_id = session_vector["turn_id"]
    native_session.active_entity_count = 1
    native_session.active_entity_ids[0] = 900
    for index, value in enumerate(session_vector["recent_utterance_hashes"]):
        native_session.recent_utterance_hashes[index] = value
    native_session.workspace = native_workspace
    for operation in session_vector["trajectory"]:
        assert library.ac_execute_action_v1(
            ctypes.byref(native_session.workspace),
            operation["action"],
            operation["argument_id"],
        ) == 0
        python_workspace.execute(Action(operation["action"]), operation["argument_id"])
    python_payload = PySession(
        session_id=session_vector["session_id"],
        turn_id=session_vector["turn_id"],
        active_entity_ids=[900],
        recent_utterance_hashes=session_vector["recent_utterance_hashes"],
        workspace=python_workspace,
    ).serialize()
    output = (ctypes.c_uint8 * 836)()
    written = ctypes.c_size_t()
    assert library.ac_session_serialize_v1(
        ctypes.byref(native_session), output, len(output), ctypes.byref(written)
    ) == 0
    assert written.value == 836
    assert bytes(output) == python_payload
    decoded = Session()
    assert library.ac_session_deserialize_v1(output, len(output), ctypes.byref(decoded)) == 0
    assert decoded.workspace.terminal_disposition == 1
