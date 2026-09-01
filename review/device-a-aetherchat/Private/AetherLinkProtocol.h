// AetherChat protocol-v2 message-level shim.
//
// Transport history: this shim was built for the AC20 wire framing
// (protocol_v2_wire.h, fragmentation over 250 B ESP-NOW payloads - see
// phase-notes/phase12-13-transport-tactility.md). The deployment pivoted to
// Option A (local IP/TCP toward Device B's softAP, see
// phase-notes/phase-option-a-tcp-spec.md): the JSON envelope is now carried
// as u32be length-prefixed frames by the selected AetherLink transport, and the AC20
// encode/reassembly helpers below are retained only as the ESP-NOW artifact.
//
// Envelopes are UTF-8 compact JSON per the protocol v2 message model,
// byte-compatible with the Python FramedJsonCodec (protocol.py /
// firmware/p4_aethercore protocol_v2 codec): type names are the UPPERCASE
// MessageType enum values ("SESSION_OPEN", "USER_TEXT", ...).
#pragma once

#include <Tactility/app/aetherchat/protocol_v2_wire.h>

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace tt::app::aetherchat {

using MessageType = wire::MsgType;

// Message body bounds (mirror protocol v2 model bounds).
constexpr size_t MAX_USER_TEXT_BYTES = 2048;
constexpr size_t MAX_MESSAGE_BODY_BYTES = 4096; // decode-side guard for reassembly
constexpr size_t MAX_FRAGMENTS = 32;            // ceil(4096 / 222) + margin

struct Message {
    MessageType type = MessageType::Error;
    uint32_t sessionId = 0;
    uint32_t requestId = 0;   // 0 for session-scoped messages
    uint16_t sequence = 0;    // msg_seq on the wire
    std::string payload;      // JSON body (complete, after reassembly)
};

// Build the protocol v2 JSON envelope for an outgoing message.
// session/request ids are rendered as decimal strings for the JSON schema
// (pattern ^[A-Za-z0-9_-]+$).
std::string buildEnvelopeJson(
    MessageType type,
    uint32_t sessionId,
    uint32_t requestId,
    int64_t sequence,
    const std::string& payloadObjectJson // e.g. "\"text\":\"...\"" fields, no braces
);

// Map an inbound envelope's "type" string (UPPERCASE MessageType value) back
// to the enum. Returns false for unknown names.
bool messageTypeFromJsonName(const std::string& name, MessageType& out);

// Escape a UTF-8 string for embedding inside a JSON string literal.
std::string jsonEscape(const std::string& text);

// Extract the string value of a top-level-or-one-deep field ("key":"value")
// from a JSON body. Returns false if absent. Minimal, non-validating: the
// authoritative validation happens on Device B; Device A only renders text.
bool jsonExtractString(const std::string& json, const char* key, std::string& out);

// Extract a boolean field ("key":true/false). Returns false if absent.
bool jsonExtractBool(const std::string& json, const char* key, bool& out);

// Fragment and encode a message into one or more AC20 frames (each <= 250 B).
// Returns false if the message is out of bounds.
bool encodeMessage(const Message& message, std::vector<std::vector<uint8_t>>& frames);

// Reassembles fragmented incoming frames into complete Messages.
// Bounded: at most MAX_PARTIALS messages in flight; oldest incomplete entry is
// dropped when full. Not thread-safe: feed from a single task (the ESP-NOW
// receive dispatch context is serialized by EspNowService's mutex).
class Reassembler {
    struct Partial {
        uint32_t sessionId = 0;
        uint32_t requestId = 0;
        uint16_t msgSeq = 0;
        MessageType type = MessageType::Error;
        uint8_t fragCount = 0;
        uint8_t received = 0;
        uint32_t receivedMask = 0; // supports up to 32 fragments
        std::string body;
        bool inUse = false;
    };
    static constexpr size_t MAX_PARTIALS = 4;
    Partial partials[MAX_PARTIALS];

public:
    // Feed one raw ESP-NOW payload. Returns true when `out` holds a complete
    // message (immediately for unfragmented frames).
    bool feed(const uint8_t* data, size_t size, Message& out);
};

} // namespace tt::app::aetherchat
