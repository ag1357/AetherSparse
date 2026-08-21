from __future__ import annotations

from collections.abc import Sequence

import pytest

from aethersparse.agent.protocol import (
    AssistantTextDeltaPayload,
    EvidenceSummaryPayload,
    FramedJsonCodec,
    HealthPayload,
    MessageType,
    MockTactilityClient,
    ProtocolMessage,
    SessionOpenPayload,
    UserTextPayload,
    response,
)


def _accessory(request: ProtocolMessage) -> Sequence[ProtocolMessage]:
    if request.type is MessageType.SESSION_OPEN:
        return (
            response(
                request,
                MessageType.HEALTH,
                HealthPayload(status="ready", runtime_version="v13"),
            ),
        )
    if request.type is MessageType.USER_TEXT:
        return (
            response(
                request,
                MessageType.EVIDENCE_SUMMARY,
                EvidenceSummaryPayload(handle_ids=("e1",), summary="one exact source"),
                suffix="evidence",
            ),
            response(
                request,
                MessageType.ASSISTANT_TEXT_DELTA,
                AssistantTextDeltaPayload(text="Grounded answer.", final=True),
                suffix="answer",
            ),
        )
    return ()


def test_mock_tactility_round_trip_is_transport_independent() -> None:
    terminal = MockTactilityClient("session-1", _accessory)
    health = terminal.send(
        MessageType.SESSION_OPEN, SessionOpenPayload(client_version="tactility-test")
    )
    assert health[0].type is MessageType.HEALTH

    result = terminal.send(MessageType.USER_TEXT, UserTextPayload(text="Who was Turing?"))
    assert [item.type for item in result] == [
        MessageType.EVIDENCE_SUMMARY,
        MessageType.ASSISTANT_TEXT_DELTA,
    ]
    assert result[-1].payload.text == "Grounded answer."
    assert result[-1].session_id == "session-1"


def test_codec_rejects_truncation_and_payload_type_mismatch() -> None:
    message = ProtocolMessage(
        message_id="m1",
        session_id="s1",
        sequence=0,
        type=MessageType.USER_TEXT,
        payload=UserTextPayload(text="hello"),
    )
    frame = FramedJsonCodec.encode(message)
    assert FramedJsonCodec.decode(frame) == message
    with pytest.raises(ValueError, match="length"):
        FramedJsonCodec.decode(frame[:-1])
    with pytest.raises(ValueError):
        ProtocolMessage(
            message_id="bad",
            session_id="s1",
            sequence=0,
            type=MessageType.USER_TEXT,
            payload=HealthPayload(status="ready", runtime_version="v13"),
        )
