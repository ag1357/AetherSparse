from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aethersparse.agent.capabilities import host_capability_model
from aethersparse.agent.contracts import AnswerKind, EvidenceHandle
from aethersparse.agent.operational import AetherCoreOperationalService
from aethersparse.agent.protocol import (
    CapabilitiesPayload,
    FramedJsonCodec,
    MessageType,
    MockTactilityClient,
    ProtocolMessage,
    ResetPayload,
    SessionOpenPayload,
    SessionResumePayload,
    UserTextPayload,
)
from aethersparse.agent.server import create_operational_app
from aethersparse.agent.vertical import (
    AetherCoreVerticalSlice,
    GroundedKnowledgeRecord,
    load_selected_policy_json,
)
from aethersparse.controller.semantic_address import canonical_entity_id
from aethersparse.memory.persistence import AuthoritativeStateStore
from aethersparse.memory.user import UserMemoryService

ROOT = Path(__file__).resolve().parents[2]


def _service(path: Path) -> AetherCoreOperationalService:
    evidence = EvidenceHandle(
        handle_id="evidence:turing",
        source_namespace="encyclopedia",
        canonical_object_id="wiki:Turing",
        source_version="1",
        source_locator="pack://encyclopedia/Turing",
        exact_text="Alan Turing was an English mathematician.",
        supported_values=("an English mathematician",),
    )
    record = GroundedKnowledgeRecord(
        entity_id=canonical_entity_id("Alan Turing"),
        canonical_title="Alan Turing",
        address_surfaces=("Alan Turing",),
        relation="description",
        relation_terms=("who",),
        relation_text="was",
        answer_kind=AnswerKind.FACTUAL_VALUE,
        values=("an English mathematician",),
        evidence=evidence,
    )
    policy = load_selected_policy_json(
        (ROOT / "reports/droid/v14/controller-selected-policy-int8.json").read_bytes()
    )
    store = AuthoritativeStateStore(path)
    return AetherCoreOperationalService(
        AetherCoreVerticalSlice((record,), policy, store),
        store,
        UserMemoryService(store.restore_memory()),
        host_capability_model("test-tree"),
    )


def test_protocol_negotiation_memory_lifecycle_restart_and_session_reset(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    service = _service(path)
    client = MockTactilityClient("allan", service.handle)
    opened = client.send(
        MessageType.SESSION_OPEN,
        SessionOpenPayload(
            client_version="tactility-0.8.0-dev",
            requested_capabilities=("memory", "streaming"),
        ),
    )
    assert [item.type for item in opened] == [MessageType.HEALTH, MessageType.CAPABILITIES]
    assert isinstance(opened[1].payload, CapabilitiesPayload)

    written = client.send(
        MessageType.USER_TEXT,
        UserTextPayload(text="remember that my preferred color is green"),
    )[0]
    assert written.type is MessageType.MEMORY_STATUS
    memory_id = written.payload.memory_ids[0]
    assert memory_id == "mem-00000001"

    # A fresh process resumes the authoritative session and user memory.
    restarted = _service(path)
    resumed_client = MockTactilityClient("allan", restarted.handle)
    resumed = resumed_client.send(
        MessageType.SESSION_RESUME,
        SessionResumePayload(client_version="tactility-0.8.0-dev"),
    )
    assert resumed[0].type is MessageType.HEALTH
    recall = resumed_client.send(
        MessageType.USER_TEXT,
        UserTextPayload(text="what do you remember about preferred color?"),
    )[0]
    assert "USER_ASSERTED" in recall.payload.detail
    assert "green" in recall.payload.detail

    edited = resumed_client.send(
        MessageType.USER_TEXT,
        UserTextPayload(text=f"edit memory {memory_id} to my preferred color is blue"),
    )[0]
    assert edited.payload.success
    recall = resumed_client.send(
        MessageType.USER_TEXT,
        UserTextPayload(text="recall preferred color"),
    )[0]
    assert "blue" in recall.payload.detail and "green" not in recall.payload.detail

    resumed_client.send(MessageType.RESET, ResetPayload())
    assert restarted.state_store.load("allan").current_query is None
    assert restarted.user_memory.read("allan", memory_id).success

    deleted = resumed_client.send(
        MessageType.USER_TEXT,
        UserTextPayload(text=f"delete memory {memory_id}"),
    )[0]
    assert deleted.payload.success
    assert restarted.user_memory.search("allan", "preferred").records == ()


def test_multiple_sessions_are_isolated_and_ordinary_text_is_not_memorized(tmp_path: Path) -> None:
    service = _service(tmp_path / "state.json")
    first = MockTactilityClient("first", service.handle)
    second = MockTactilityClient("second", service.handle)
    first.send(
        MessageType.USER_TEXT,
        UserTextPayload(text="remember that the project label is alpha"),
    )
    second.send(
        MessageType.USER_TEXT,
        UserTextPayload(text="Who was Alan Turing?"),
    )
    assert service.user_memory.search("first", "project alpha").records
    assert service.user_memory.search("second", "project alpha").records == ()
    assert service.user_memory.list("second").records == ()


def test_http_service_and_malformed_protocol_rejection(tmp_path: Path) -> None:
    service = _service(tmp_path / "state.json")
    with TestClient(create_operational_app(service)) as client:
        assert client.get("/v15/health").status_code == 200
        assert client.get("/v15/capabilities").json()["hardware_class"] == "HOST"
    message = ProtocolMessage(
        message_id="bad-version",
        request_id="r1",
        session_id="session",
        sequence=0,
        type=MessageType.USER_TEXT,
        payload=UserTextPayload(text="hello"),
        protocol_version="unsupported",
    )
    with pytest.raises(ValueError, match="protocol version"):
        service.handle(message)
    frame = FramedJsonCodec.encode(message)
    with pytest.raises(ValueError, match="length"):
        FramedJsonCodec.decode(frame + b"x")
