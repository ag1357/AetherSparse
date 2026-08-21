from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aethersparse.edge_runtime.reference import (
    Action,
    Candidate,
    LinearPolicy,
    RuntimeContractError,
    Session,
    Terminal,
    Workspace,
)

VECTOR_PATH = Path(__file__).parent / "vectors" / "runtime-v1.json"


def _vectors() -> dict[str, object]:
    return json.loads(VECTOR_PATH.read_text(encoding="utf-8"))


def test_python_frozen_reference_vector() -> None:
    vectors = _vectors()
    union = vectors["candidate_union"]
    assert isinstance(union, dict)
    workspace = Workspace(candidates=[Candidate(**item) for item in union["existing"]])
    workspace.union_candidates([Candidate(**item) for item in union["incoming"]])
    assert len(workspace.candidates) == 32
    assert workspace.candidates[0] == Candidate(900, 13000, 5)
    assert Candidate(50, 2000, 24) in workspace.candidates
    assert workspace.candidates[-1].entity_id == 12

    policy_vector = vectors["policy"]
    assert isinstance(policy_vector, dict)
    policy = LinearPolicy(
        weights=tuple(tuple(row) for row in policy_vector["weights"]),
        bias=tuple(policy_vector["bias"]),
    )
    selected = policy.select(
        tuple(policy_vector["features"]), policy_vector["legal_action_mask"]
    )
    assert selected == (policy_vector["selected_action"], policy_vector["selected_logit"])

    session_vector = vectors["session"]
    assert isinstance(session_vector, dict)
    session = Session(
        session_id=session_vector["session_id"],
        turn_id=session_vector["turn_id"],
        active_entity_ids=session_vector["active_entity_ids"],
        recent_utterance_hashes=session_vector["recent_utterance_hashes"],
        workspace=workspace,
    )
    for operation in session_vector["trajectory"]:
        session.workspace.execute(Action(operation["action"]), operation["argument_id"])
    payload = session.serialize()
    assert hashlib.sha256(payload).hexdigest() == session_vector["serialized_sha256"]
    restored = Session.deserialize(payload)
    assert restored.workspace.terminal_disposition is Terminal.ANSWER
    assert restored.serialize() == payload


def test_verifier_and_legal_mask_cannot_be_bypassed() -> None:
    workspace = Workspace(candidates=[Candidate(5, 1000, 0)])
    with pytest.raises(RuntimeContractError):
        workspace.execute(Action.SELECT_EVIDENCE, 5)
    with pytest.raises(RuntimeContractError):
        workspace.execute(Action.ANSWER)
    assert workspace.invalid_action_count == 2
