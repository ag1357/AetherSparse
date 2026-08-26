from __future__ import annotations

import ctypes
import json
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from aethersparse.edge_runtime.reference import Action, RuntimeContractError
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


def _session_wire(*, terminal: bool = True) -> bytes:
    workspace = PyWorkspace(candidates=[PyCandidate(1, 100, 1), PyCandidate(2, 90, 1)])
    workspace.execute(Action.SELECT_EVIDENCE, 1)
    workspace.execute(Action.BUILD_PLAN)
    workspace.execute(Action.VERIFY_PLAN)
    if terminal:
        workspace.execute(Action.ANSWER)
    return PySession(session_id="v15-wire", workspace=workspace).serialize()


def _patch_crc_valid(payload: bytes, offset: int, format_: str, *values: int) -> bytes:
    forged = bytearray(payload)
    struct.pack_into(format_, forged, offset, *values)
    struct.pack_into("<I", forged, len(forged) - 4, zlib.crc32(forged[:-4]))
    return bytes(forged)


@pytest.mark.parametrize(
    ("name", "offset", "format_", "values"),
    [
        ("zero_candidate", 232, "<Q", (0,)),
        ("duplicate_candidate", 248, "<Q", (1,)),
        ("duplicate_selected", 744, "<I2Q", (2, 1, 1)),
        ("missing_selected_reference", 748, "<Q", (999,)),
        ("zero_evidence_selected", 244, "<I", (0,)),
        ("unknown_flag", 824, "<I", (1 << 12,)),
        ("verified_without_plan", 824, "<I", (2,)),
        ("plan_without_selection", 744, "<I8Q", (0, 0, 0, 0, 0, 0, 0, 0, 0)),
        ("terminal_without_disposition", 824, "<2I", (7, 0)),
        ("invalid_terminal_disposition", 828, "<I", (9,)),
        ("answer_without_verified", 824, "<2I", (5, 1)),
        ("step_count_65", 816, "<I", (65,)),
        ("bad_last_action", 812, "<I", (31,)),
        ("empty_session_id", 12, "<40s", (b"",)),
        ("nonzero_active_tail", 60, "<IQ", (0, 77)),
        ("nonzero_pending_tail", 128, "<IQ", (0, 77)),
        ("nonzero_candidate_tail", 264, "<Q", (77,)),
        ("nonzero_selected_tail", 756, "<Q", (77,)),
    ],
)
def test_native_rejects_crc_valid_session_semantic_forgery(
    tmp_path: Path,
    name: str,
    offset: int,
    format_: str,
    values: tuple[int, ...],
) -> None:
    del name
    library = _compile(tmp_path)
    payload = _patch_crc_valid(_session_wire(), offset, format_, *values)
    wire = (ctypes.c_uint8 * len(payload)).from_buffer_copy(payload)
    assert library.ac_session_deserialize_v1(
        wire, len(wire), ctypes.byref(Session())
    ) == 2
    with pytest.raises(RuntimeContractError):
        PySession.deserialize(payload)


def test_zero_active_and_pending_ids_are_currently_legal_wire_semantics(
    tmp_path: Path,
) -> None:
    library = _compile(tmp_path)
    payload = _patch_crc_valid(_session_wire(), 60, "<I", 1)
    payload = _patch_crc_valid(payload, 128, "<IQ", 1, 0)
    wire = (ctypes.c_uint8 * len(payload)).from_buffer_copy(payload)
    assert library.ac_session_deserialize_v1(
        wire, len(wire), ctypes.byref(Session())
    ) == 0


def test_selected_candidate_is_pinned_then_workspace_freezes(tmp_path: Path) -> None:
    library = _compile(tmp_path)
    workspace = Workspace()
    assert library.ac_workspace_init_v1(ctypes.byref(workspace)) == 0
    initial = (Candidate * 32)(
        *(Candidate(index + 1, 1000 - index, 1) for index in range(32))
    )
    assert library.ac_union_candidates_v1(ctypes.byref(workspace), initial, 32) == 0
    assert library.ac_execute_action_v1(
        ctypes.byref(workspace), int(Action.SELECT_EVIDENCE), 32
    ) == 0
    stronger = (Candidate * 1)(Candidate(99, 5000, 1))
    assert library.ac_union_candidates_v1(ctypes.byref(workspace), stronger, 1) == 0
    ids = {workspace.candidates[index].entity_id for index in range(workspace.candidate_count)}
    assert 32 in ids and 99 in ids and 31 not in ids
    assert library.ac_execute_action_v1(
        ctypes.byref(workspace), int(Action.BUILD_PLAN), 0
    ) == 0
    assert library.ac_execute_action_v1(
        ctypes.byref(workspace), int(Action.VERIFY_PLAN), 0
    ) == 0
    before_verified = bytes(workspace)
    assert library.ac_union_candidates_v1(ctypes.byref(workspace), stronger, 1) == 2
    assert bytes(workspace) == before_verified
    assert library.ac_execute_action_v1(ctypes.byref(workspace), int(Action.ANSWER), 0) == 0
    before_terminal = bytes(workspace)
    assert library.ac_union_candidates_v1(ctypes.byref(workspace), stronger, 1) == 2
    assert bytes(workspace) == before_terminal

    python_workspace = PyWorkspace(
        candidates=[PyCandidate(index + 1, 1000 - index, 1) for index in range(32)]
    )
    python_workspace.execute(Action.SELECT_EVIDENCE, 32)
    python_workspace.union_candidates([PyCandidate(99, 5000, 1)])
    assert {item.entity_id for item in python_workspace.candidates} == ids
    python_workspace.execute(Action.BUILD_PLAN)
    python_workspace.execute(Action.VERIFY_PLAN)
    with pytest.raises(ValueError, match="immutable"):
        python_workspace.union_candidates([PyCandidate(100, 6000, 1)])
