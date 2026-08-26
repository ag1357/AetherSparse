"""Transport-independent protocol between Tactility and the AetherCore accessory."""

from __future__ import annotations

import struct
from collections.abc import Callable, Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MessageType(StrEnum):
    SESSION_OPEN = "SESSION_OPEN"
    SESSION_RESUME = "SESSION_RESUME"
    USER_TEXT = "USER_TEXT"
    USER_CANCEL = "USER_CANCEL"
    RESET = "RESET"
    ASSISTANT_TEXT_DELTA = "ASSISTANT_TEXT_DELTA"
    CLARIFICATION_REQUEST = "CLARIFICATION_REQUEST"
    TASK_STATUS = "TASK_STATUS"
    TOOL_ACTIVITY_SUMMARY = "TOOL_ACTIVITY_SUMMARY"
    EVIDENCE_SUMMARY = "EVIDENCE_SUMMARY"
    MEMORY_STATUS = "MEMORY_STATUS"
    ERROR = "ERROR"
    HEALTH = "HEALTH"
    CAPABILITIES = "CAPABILITIES"


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SessionOpenPayload(Payload):
    client_version: str
    supported_protocols: tuple[str, ...] = Field(default=("aethercore-tactility.v2",), max_length=4)
    requested_capabilities: tuple[str, ...] = Field(default=(), max_length=32)


class SessionResumePayload(Payload):
    client_version: str
    last_received_sequence: int = Field(default=0, ge=0)


class UserTextPayload(Payload):
    text: str = Field(min_length=1, max_length=2048)


class UserCancelPayload(Payload):
    reason: str = "user"


class ResetPayload(Payload):
    reason: str = "user"


class AssistantTextDeltaPayload(Payload):
    text: str = Field(min_length=1, max_length=1024)
    final: bool = False


class ClarificationRequestPayload(Payload):
    question: str
    choices: tuple[str, ...] = Field(min_length=2, max_length=8)


class TaskStatusPayload(Payload):
    status: str
    detail: str = ""


class ToolActivitySummaryPayload(Payload):
    tool: str
    success: bool
    summary: str


class EvidenceSummaryPayload(Payload):
    handle_ids: tuple[str, ...] = Field(max_length=32)
    summary: str


class MemoryStatusPayload(Payload):
    operation: str
    success: bool
    memory_ids: tuple[str, ...] = Field(default=(), max_length=32)
    detail: str = Field(default="", max_length=512)


class ErrorPayload(Payload):
    code: str
    message: str
    recoverable: bool


class HealthPayload(Payload):
    status: str
    runtime_version: str
    service_generation: int = Field(default=1, ge=1)


class CapabilitiesPayload(Payload):
    protocol_version: str
    hardware_class: str
    tools: tuple[str, ...] = Field(default=(), max_length=64)
    specialists: tuple[str, ...] = Field(default=(), max_length=64)
    unavailable: tuple[str, ...] = Field(default=(), max_length=64)
    transport: str


MessagePayload = (
    SessionOpenPayload
    | SessionResumePayload
    | UserTextPayload
    | UserCancelPayload
    | ResetPayload
    | AssistantTextDeltaPayload
    | ClarificationRequestPayload
    | TaskStatusPayload
    | ToolActivitySummaryPayload
    | EvidenceSummaryPayload
    | MemoryStatusPayload
    | ErrorPayload
    | HealthPayload
    | CapabilitiesPayload
)

_PAYLOAD_TYPE: dict[MessageType, type[Payload]] = {
    MessageType.SESSION_OPEN: SessionOpenPayload,
    MessageType.SESSION_RESUME: SessionResumePayload,
    MessageType.USER_TEXT: UserTextPayload,
    MessageType.USER_CANCEL: UserCancelPayload,
    MessageType.RESET: ResetPayload,
    MessageType.ASSISTANT_TEXT_DELTA: AssistantTextDeltaPayload,
    MessageType.CLARIFICATION_REQUEST: ClarificationRequestPayload,
    MessageType.TASK_STATUS: TaskStatusPayload,
    MessageType.TOOL_ACTIVITY_SUMMARY: ToolActivitySummaryPayload,
    MessageType.EVIDENCE_SUMMARY: EvidenceSummaryPayload,
    MessageType.MEMORY_STATUS: MemoryStatusPayload,
    MessageType.ERROR: ErrorPayload,
    MessageType.HEALTH: HealthPayload,
    MessageType.CAPABILITIES: CapabilitiesPayload,
}


class ProtocolMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    protocol_version: str = "aethercore-tactility.v2"
    message_id: str = Field(min_length=1, max_length=128)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    sequence: int = Field(ge=0)
    type: MessageType
    payload: MessagePayload

    @model_validator(mode="before")
    @classmethod
    def parse_typed_payload(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        updated = dict(value)
        kind = MessageType(updated["type"])
        updated["payload"] = _PAYLOAD_TYPE[kind].model_validate(updated["payload"])
        return updated

    @model_validator(mode="after")
    def payload_matches_message(self) -> ProtocolMessage:
        expected = _PAYLOAD_TYPE[self.type]
        if not isinstance(self.payload, expected):
            raise ValueError(f"{self.type} requires {expected.__name__}")
        return self


class FramedJsonCodec:
    """Length-prefixed JSON usable over serial, TCP, USB, or in-process tests."""

    MAX_FRAME_BYTES = 16_384

    @classmethod
    def encode(cls, message: ProtocolMessage) -> bytes:
        body = message.model_dump_json().encode()
        if len(body) > cls.MAX_FRAME_BYTES:
            raise ValueError("protocol frame exceeds limit")
        return struct.pack(">I", len(body)) + body

    @classmethod
    def decode(cls, frame: bytes) -> ProtocolMessage:
        if len(frame) < 4:
            raise ValueError("truncated protocol frame")
        (size,) = struct.unpack(">I", frame[:4])
        if size > cls.MAX_FRAME_BYTES or len(frame) != size + 4:
            raise ValueError("invalid protocol frame length")
        return ProtocolMessage.model_validate_json(frame[4:])


MessageHandler = Callable[[ProtocolMessage], Sequence[ProtocolMessage]]


class MockTactilityClient:
    """Deterministic terminal-only client used to freeze the accessory boundary."""

    def __init__(self, session_id: str, handler: MessageHandler) -> None:
        self.session_id = session_id
        self.handler = handler
        self.sequence = 0
        self.received: list[ProtocolMessage] = []

    def send(self, kind: MessageType, payload: MessagePayload) -> tuple[ProtocolMessage, ...]:
        request = ProtocolMessage(
            message_id=f"terminal-{self.sequence}",
            request_id=f"request-{self.sequence}",
            session_id=self.session_id,
            sequence=self.sequence,
            type=kind,
            payload=payload,
        )
        # Force both sides through the wire representation even in the mock.
        decoded_request = FramedJsonCodec.decode(FramedJsonCodec.encode(request))
        responses = tuple(
            FramedJsonCodec.decode(FramedJsonCodec.encode(response))
            for response in self.handler(decoded_request)
        )
        if any(response.session_id != self.session_id for response in responses):
            raise ValueError("accessory returned a response for another session")
        self.received.extend(responses)
        self.sequence += 1
        return responses


def response(
    request: ProtocolMessage,
    kind: MessageType,
    payload: MessagePayload,
    *,
    suffix: str = "response",
) -> ProtocolMessage:
    return ProtocolMessage(
        message_id=f"{request.message_id}-{suffix}",
        request_id=request.request_id or request.message_id,
        session_id=request.session_id,
        sequence=request.sequence,
        type=kind,
        payload=payload,
    )
