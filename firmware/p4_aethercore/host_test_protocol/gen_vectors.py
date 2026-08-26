#!/usr/bin/env python3
"""Golden-vector generator for the native protocol v2 codec.

Imports the authoritative Python implementation
(src/aethersparse/agent/protocol.py) and emits a line-based vector file that
the native C++ test harness consumes:

  V <name> <type> <frame_hex> <reencode_hex>
      -> decode must succeed with matching type; re-encode must equal
         reencode_hex (covers default-field normalization).
  E <name> <error_name> <frame_hex>
      -> decode must fail with exactly this DecodeError name.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]  # firmware/p4_aethercore/host_test_protocol -> repo root
sys.path.insert(0, str(REPO_ROOT / "src"))

from aethersparse.agent.protocol import (  # noqa: E402
    AssistantTextDeltaPayload,
    CapabilitiesPayload,
    ClarificationRequestPayload,
    ErrorPayload,
    EvidenceSummaryPayload,
    FramedJsonCodec,
    HealthPayload,
    MemoryStatusPayload,
    MessageType,
    ProtocolMessage,
    ResetPayload,
    SessionOpenPayload,
    SessionResumePayload,
    TaskStatusPayload,
    ToolActivitySummaryPayload,
    UserCancelPayload,
    UserTextPayload,
)


def frame_hex(message: ProtocolMessage) -> str:
    return FramedJsonCodec.encode(message).hex()


def make(seq: int, kind: MessageType, payload, session: str = "sess-01",
         request: str | None = None) -> ProtocolMessage:
    return ProtocolMessage(
        message_id=f"m-{seq}",
        request_id=request if request is not None else f"req-{seq}",
        session_id=session,
        sequence=seq,
        type=kind,
        payload=payload,
    )


def raw_frame(body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + body


def main() -> None:
    out = Path(__file__).resolve().parent / "vectors.txt"
    lines: list[str] = []

    def v(name: str, message: ProtocolMessage, frame: bytes | None = None) -> None:
        raw = frame if frame is not None else FramedJsonCodec.encode(message)
        decoded = FramedJsonCodec.decode(raw)  # must validate in Python
        reencode = FramedJsonCodec.encode(decoded)
        lines.append(f"V {name} {decoded.type.value} {raw.hex()} {reencode.hex()}")

    def e(name: str, error: str, frame: bytes) -> None:
        lines.append(f"E {name} {error} {frame.hex()}")

    # ---- Valid vectors: one per message type --------------------------
    v("session_open_full", make(0, MessageType.SESSION_OPEN, SessionOpenPayload(
        client_version="tactility-15.0",
        supported_protocols=("aethercore-tactility.v2",),
        requested_capabilities=("evidence", "memory"),
    )))
    v("session_open_defaults", make(1, MessageType.SESSION_OPEN, SessionOpenPayload(
        client_version="tactility-15.0")))
    v("session_resume", make(2, MessageType.SESSION_RESUME, SessionResumePayload(
        client_version="tactility-15.0", last_received_sequence=41)))
    v("session_resume_default_seq", make(3, MessageType.SESSION_RESUME,
                                         SessionResumePayload(client_version="t-15")))
    v("user_text", make(4, MessageType.USER_TEXT,
                        UserTextPayload(text="Who was Alan Turing?")))
    v("user_text_max", make(5, MessageType.USER_TEXT,
                            UserTextPayload(text="x" * 2048)))
    v("user_cancel", make(6, MessageType.USER_CANCEL,
                          UserCancelPayload(reason="timeout")))
    v("user_cancel_default", make(7, MessageType.USER_CANCEL, UserCancelPayload()))
    v("reset", make(8, MessageType.RESET, ResetPayload(reason="watchdog")))
    v("assistant_delta", make(9, MessageType.ASSISTANT_TEXT_DELTA,
                              AssistantTextDeltaPayload(text="Grounded answer.", final=True)))
    v("assistant_delta_not_final", make(10, MessageType.ASSISTANT_TEXT_DELTA,
                                        AssistantTextDeltaPayload(text="partial...")))
    v("clarification", make(11, MessageType.CLARIFICATION_REQUEST,
                            ClarificationRequestPayload(
                                question="Which repo?", choices=("v14", "v15", "v16"))))
    v("task_status", make(12, MessageType.TASK_STATUS,
                          TaskStatusPayload(status="running", detail="step 2/5")))
    v("tool_activity", make(13, MessageType.TOOL_ACTIVITY_SUMMARY,
                            ToolActivitySummaryPayload(tool="search", success=True,
                                                       summary="3 hits")))
    v("evidence_summary", make(14, MessageType.EVIDENCE_SUMMARY,
                               EvidenceSummaryPayload(handle_ids=("e1", "e2"),
                                                      summary="two sources")))
    v("memory_status", make(15, MessageType.MEMORY_STATUS,
                            MemoryStatusPayload(operation="write", success=False,
                                                memory_ids=("mem-9",),
                                                detail="quota exceeded")))
    v("error", make(16, MessageType.ERROR,
                    ErrorPayload(code="E_OVERLOAD", message="busy", recoverable=True)))
    v("health", make(17, MessageType.HEALTH,
                     HealthPayload(status="ready", runtime_version="v15.0.0",
                                   service_generation=3)))
    v("health_default_gen", make(18, MessageType.HEALTH,
                                 HealthPayload(status="ready", runtime_version="v15")))
    v("capabilities", make(19, MessageType.CAPABILITIES, CapabilitiesPayload(
        protocol_version="aethercore-tactility.v2",
        hardware_class="esp32-p4",
        tools=("search", "gpio"),
        specialists=("vision",),
        unavailable=("audio-out",),
        transport="esp-now",
    )))
    v("request_id_null", ProtocolMessage(
        message_id="m-20", session_id="sess-01", sequence=20,
        type=MessageType.USER_TEXT, payload=UserTextPayload(text="no request id")))
    v("escapes_round_trip", make(21, MessageType.USER_TEXT,
                                 UserTextPayload(text='quote " backslash \\ newline\n tab\t')))
    v("unicode_round_trip", make(22, MessageType.TASK_STATUS,
                                 TaskStatusPayload(status="café ✓", detail="")))

    # Defaults-on-decode: hand-built JSON omitting defaulted fields.
    omit = {
        "message_id": "m-23",
        "session_id": "sess-01",
        "sequence": 23,
        "type": "USER_CANCEL",
        "payload": {},
    }
    body = json.dumps(omit, separators=(",", ":")).encode()
    msg = FramedJsonCodec.decode(raw_frame(body))
    lines.append(f"V user_cancel_omitted_defaults {msg.type.value} "
                 f"{raw_frame(body).hex()} {FramedJsonCodec.encode(msg).hex()}")

    # ---- Malformed vectors --------------------------------------------
    good = FramedJsonCodec.encode(make(30, MessageType.USER_TEXT,
                                       UserTextPayload(text="hello")))

    e("empty_frame", "TRUNCATED_FRAME", b"")
    e("two_bytes", "TRUNCATED_FRAME", b"\x00\x00")
    e("truncated_body", "TRUNCATED_FRAME", good[:-3])
    e("length_too_small", "INVALID_LENGTH", struct.pack(">I", 5) + good[4:])
    e("length_oversize", "INVALID_LENGTH", struct.pack(">I", 16385) + good[4:])
    e("length_huge", "INVALID_LENGTH", struct.pack(">I", 0xFFFFFFFF) + good[4:])
    e("garbage_body", "INVALID_JSON", raw_frame(b"\xff\xfe\x00 not json"))
    e("json_array_root", "INVALID_FIELD_TYPE", raw_frame(b"[1,2,3]"))
    e("unknown_type", "UNKNOWN_TYPE", raw_frame(json.dumps(
        {"message_id": "m", "session_id": "s", "sequence": 0,
         "type": "BOGUS", "payload": {}}, separators=(",", ":")).encode()))
    e("missing_message_id", "MISSING_FIELD", raw_frame(json.dumps(
        {"session_id": "s", "sequence": 0, "type": "RESET",
         "payload": {}}, separators=(",", ":")).encode()))
    e("missing_payload", "MISSING_FIELD", raw_frame(json.dumps(
        {"message_id": "m", "session_id": "s", "sequence": 0,
         "type": "RESET"}, separators=(",", ":")).encode()))
    e("session_id_bad_chars", "VALIDATION_FAILED", raw_frame(json.dumps(
        {"message_id": "m", "session_id": "bad session!", "sequence": 0,
         "type": "RESET", "payload": {}}, separators=(",", ":")).encode()))
    e("negative_sequence", "VALIDATION_FAILED", raw_frame(json.dumps(
        {"message_id": "m", "session_id": "s", "sequence": -1,
         "type": "RESET", "payload": {}}, separators=(",", ":")).encode()))
    e("extra_envelope_field", "VALIDATION_FAILED", raw_frame(json.dumps(
        {"message_id": "m", "session_id": "s", "sequence": 0, "type": "RESET",
         "payload": {}, "surprise": 1}, separators=(",", ":")).encode()))
    e("extra_payload_field", "VALIDATION_FAILED", raw_frame(json.dumps(
        {"message_id": "m", "session_id": "s", "sequence": 0, "type": "RESET",
         "payload": {"reason": "user", "surprise": 1}}, separators=(",", ":")).encode()))
    e("sequence_wrong_type", "INVALID_FIELD_TYPE", raw_frame(json.dumps(
        {"message_id": "m", "session_id": "s", "sequence": "zero",
         "type": "RESET", "payload": {}}, separators=(",", ":")).encode()))
    e("empty_user_text", "VALIDATION_FAILED", raw_frame(json.dumps(
        {"message_id": "m", "session_id": "s", "sequence": 0,
         "type": "USER_TEXT", "payload": {"text": ""}}, separators=(",", ":")).encode()))
    e("oversize_user_text", "VALIDATION_FAILED", raw_frame(json.dumps(
        {"message_id": "m", "session_id": "s", "sequence": 0,
         "type": "USER_TEXT", "payload": {"text": "y" * 2049}},
        separators=(",", ":")).encode()))
    e("choices_too_few", "VALIDATION_FAILED", raw_frame(json.dumps(
        {"message_id": "m", "session_id": "s", "sequence": 0,
         "type": "CLARIFICATION_REQUEST",
         "payload": {"question": "q", "choices": ["only"]}},
        separators=(",", ":")).encode()))
    e("choices_wrong_item_type", "INVALID_FIELD_TYPE", raw_frame(json.dumps(
        {"message_id": "m", "session_id": "s", "sequence": 0,
         "type": "CLARIFICATION_REQUEST",
         "payload": {"question": "q", "choices": ["a", 2]}},
        separators=(",", ":")).encode()))
    # Payload valid for HEALTH but not USER_TEXT: extra fields under
    # extra="forbid" plus missing "text" -> validation failure.
    e("payload_type_mismatch", "VALIDATION_FAILED", raw_frame(json.dumps(
        {"message_id": "m", "session_id": "s", "sequence": 0,
         "type": "USER_TEXT",
         "payload": {"status": "ready", "runtime_version": "v13"}},
        separators=(",", ":")).encode()))
    e("request_id_wrong_type", "INVALID_FIELD_TYPE", raw_frame(json.dumps(
        {"message_id": "m", "request_id": 7, "session_id": "s", "sequence": 0,
         "type": "RESET", "payload": {}}, separators=(",", ":")).encode()))

    out.write_text("\n".join(lines) + "\n")
    n_v = sum(1 for line in lines if line.startswith("V "))
    n_e = sum(1 for line in lines if line.startswith("E "))
    print(f'{{"meas":"gen_vectors","valid":{n_v},"errors":{n_e},'
          f'"out":"{out.name}"}}')


if __name__ == "__main__":
    main()
