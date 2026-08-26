// AetherCore protocol v2 native codec (C++17, no ESP-IDF dependency).
//
// Wire profile ("v2 native framing"): this codec matches the Python reference
// implementation in src/aethersparse/agent/protocol.py (FramedJsonCodec)
// byte-for-byte:
//
//   frame := u32be length || json_body
//   - length: 4-byte big-endian unsigned length of the JSON body in bytes.
//   - json_body: UTF-8 JSON serialization of ProtocolMessage using pydantic
//     v2 model_dump_json semantics: compact separators (no spaces), fields in
//     model declaration order, `null` for absent request_id, raw (unescaped)
//     non-ASCII UTF-8, minimal JSON string escaping (\" \\ \b \f \n \r \t,
//     other control chars as \u00XX).
//   - MAX_FRAME_BYTES = 16384 (max JSON body size; max frame = 16388).
//
// The decoder is a strict superset-tolerant reader: it accepts any field
// order, insignificant whitespace, and all JSON string escapes, then applies
// the exact pydantic validation rules (required fields, extra=forbid, bounds,
// session_id pattern, typed-payload matching, defaults for omitted fields).
//
// Bounded-memory guarantee: all storage lives inside ProtocolMessage (fixed
// pools). No heap allocation, no recursion deeper than 2 (object->array), and
// every malformed input is rejected with a typed DecodeError -- never a crash
// or unbounded allocation.
#pragma once

#include <cstddef>
#include <cstdint>

namespace aethercore {
namespace protocol_v2 {

// ---------------------------------------------------------------------------
// Limits (mirroring the Python model; pool sizes bound the whole frame).
// ---------------------------------------------------------------------------
constexpr uint32_t kMaxFrameBytes = 16384;      // max JSON body bytes
constexpr uint32_t kMaxEncodedFrame = 4 + kMaxFrameBytes;
constexpr size_t kMaxIdLen = 128;               // message_id / request_id / session_id
constexpr size_t kMaxUserText = 2048;
constexpr size_t kMaxAssistantDelta = 1024;
constexpr size_t kMaxMemoryDetail = 512;
constexpr size_t kMaxTupleItems = 64;           // largest tuple bound (capabilities)
constexpr size_t kMaxChoices = 8;

// Fixed string pool inside each decoded message. The JSON body is at most
// 16 KiB, so 20 KiB covers all unescaped string content with margin.
constexpr size_t kStringPoolBytes = 20480;

// ---------------------------------------------------------------------------
// Message types (identical set to the Python MessageType StrEnum).
// ---------------------------------------------------------------------------
enum class MsgType : uint8_t {
  SESSION_OPEN = 0,
  SESSION_RESUME,
  USER_TEXT,
  USER_CANCEL,
  RESET,
  ASSISTANT_TEXT_DELTA,
  CLARIFICATION_REQUEST,
  TASK_STATUS,
  TOOL_ACTIVITY_SUMMARY,
  EVIDENCE_SUMMARY,
  MEMORY_STATUS,
  ERROR,
  HEALTH,
  CAPABILITIES,
  kCount
};

const char* ToString(MsgType t);
// Returns false for unknown strings.
bool MsgTypeFromString(const char* s, size_t len, MsgType& out);

// ---------------------------------------------------------------------------
// Typed errors. Decode never throws and never crashes on bad input.
// ---------------------------------------------------------------------------
enum class DecodeError : uint8_t {
  OK = 0,
  TRUNCATED_FRAME,       // fewer than 4 bytes, or body shorter than length prefix
  INVALID_LENGTH,        // length prefix > kMaxFrameBytes or != actual body size
  INVALID_JSON,          // lexical/structural JSON error or pool overflow
  MISSING_FIELD,         // required envelope/payload field absent
  UNKNOWN_TYPE,          // "type" string not a known MessageType
  INVALID_FIELD_TYPE,    // field present but wrong JSON type
  VALIDATION_FAILED,     // bounds/pattern/tuple-size/extra-field violation
  PAYLOAD_TYPE_MISMATCH  // payload object does not match "type" schema
};

const char* ToString(DecodeError e);

enum class EncodeError : uint8_t {
  OK = 0,
  INVALID_MESSAGE,    // message violates a bound (would not validate)
  BUFFER_TOO_SMALL,   // caller buffer cannot hold the frame
};

// ---------------------------------------------------------------------------
// String view into the message's fixed pool.
// ---------------------------------------------------------------------------
struct Str {
  const char* data = nullptr;  // points into ProtocolMessage::pool
  uint32_t len = 0;
  bool equals(const char* s) const;
};

struct StrList {
  Str items[kMaxTupleItems];
  uint32_t count = 0;
};

// ---------------------------------------------------------------------------
// Payloads (tagged union over fixed storage; one member active per `type`).
// ---------------------------------------------------------------------------
struct SessionOpenPayload {
  Str client_version;             // required
  StrList supported_protocols;    // default ("aethercore-tactility.v2",), max 4
  StrList requested_capabilities; // default (), max 32
};
struct SessionResumePayload {
  Str client_version;             // required
  int64_t last_received_sequence = 0;  // default 0, >= 0
};
struct UserTextPayload { Str text; };  // required, 1..2048
struct UserCancelPayload { Str reason; };  // default "user"
struct ResetPayload { Str reason; };       // default "user"
struct AssistantTextDeltaPayload {
  Str text;      // required, 1..1024
  bool final = false;
};
struct ClarificationRequestPayload {
  Str question;                    // required
  Str choices[kMaxChoices];        // 2..8 items
  uint32_t choice_count = 0;
};
struct TaskStatusPayload { Str status; Str detail; };  // detail default ""
struct ToolActivitySummaryPayload { Str tool; bool success = false; Str summary; };
struct EvidenceSummaryPayload { StrList handle_ids; Str summary; };  // max 32
struct MemoryStatusPayload {
  Str operation;
  bool success = false;
  StrList memory_ids;  // default (), max 32
  Str detail;          // default "", max 512
};
struct ErrorPayload { Str code; Str message; bool recoverable = false; };
struct HealthPayload {
  Str status;
  Str runtime_version;
  int64_t service_generation = 1;  // default 1, >= 1
};
struct CapabilitiesPayload {
  Str protocol_version;
  Str hardware_class;
  StrList tools;        // default (), max 64
  StrList specialists;  // default (), max 64
  StrList unavailable;  // default (), max 64
  Str transport;        // required
};

// ---------------------------------------------------------------------------
// ProtocolMessage: envelope + tagged payload, all storage inline (bounded).
// ---------------------------------------------------------------------------
struct ProtocolMessage {
  // Envelope
  Str protocol_version;          // default "aethercore-tactility.v2"
  Str message_id;                // required, 1..128
  Str request_id;                // optional (null); 1..128 when present
  bool has_request_id = false;
  Str session_id;                // required, 1..128, ^[A-Za-z0-9_-]+$
  int64_t sequence = 0;          // required, >= 0
  MsgType type = MsgType::SESSION_OPEN;

  union Payloads {
    SessionOpenPayload session_open;
    SessionResumePayload session_resume;
    UserTextPayload user_text;
    UserCancelPayload user_cancel;
    ResetPayload reset;
    AssistantTextDeltaPayload assistant_text_delta;
    ClarificationRequestPayload clarification_request;
    TaskStatusPayload task_status;
    ToolActivitySummaryPayload tool_activity_summary;
    EvidenceSummaryPayload evidence_summary;
    MemoryStatusPayload memory_status;
    ErrorPayload error;
    HealthPayload health;
    CapabilitiesPayload capabilities;
    Payloads() {}
  } p;

  // Fixed string arena for all decoded strings / encoder input staging.
  char pool[kStringPoolBytes];
  uint32_t pool_used = 0;

  // Copy `s` into the pool; returns false on overflow.
  bool poolPut(const char* s, size_t len, Str& out);
};

// ---------------------------------------------------------------------------
// Codec. Length-prefixed framing identical to Python FramedJsonCodec.
// ---------------------------------------------------------------------------

// Decode one complete frame (4-byte BE length + JSON body) held fully in
// `frame[0..len)`. On OK, `out` is fully validated. On error, returns the
// typed error; `out` contents are unspecified but never partially pool-owned.
DecodeError DecodeFrame(const uint8_t* frame, size_t len, ProtocolMessage& out);

// Encode `msg` into `out[0..cap)` as length-prefixed JSON identical to
// Python's FramedJsonCodec.encode (compact, field-ordered). Returns bytes
// written via `out_len`.
EncodeError EncodeFrame(const ProtocolMessage& msg, uint8_t* out, size_t cap,
                        size_t& out_len);

}  // namespace protocol_v2
}  // namespace aethercore
