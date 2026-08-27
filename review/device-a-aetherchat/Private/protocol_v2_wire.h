// AetherCore protocol v2 ESP-NOW wire framing ("AC20").
//
// Single source of truth for the Device A <-> Device B transport framing.
// Device B side: espnow-bridge-c6 firmware (C6) + p4_aethercore (P4) share these
// exact constants. Keep in sync by hand.
//
// Frame layout (all multi-byte fields little-endian on the wire):
//
//   offset  0: char[4]  magic       = {'A','C','2','0'}
//   offset  4: u16      type        (MsgType, mirrors protocol v2 message ordering)
//   offset  6: u16      flags       (bit0 = FRAGMENTED, rest reserved/zero)
//   offset  8: u32      request_id
//   offset 12: u32      session_id
//   offset 16: u32      payload_len (bytes of payload that follow, INCLUDING the
//                                    4-byte fragment sub-header when FRAGMENTED)
//   offset 20: u8[]     payload     (payload_len bytes)
//   offset 20+payload_len: u32      crc32 (IEEE 802.3, poly 0xEDB88320, init/xorout
//                                    0xFFFFFFFF), stored little-endian, computed
//                                    over bytes [4, 20+payload_len) - i.e. everything
//                                    after the magic, excluding the CRC itself.
//
// Fragment sub-header (first 4 bytes of payload when FLAG_FRAGMENTED is set):
//   u16 msg_seq      - identifies the logical message being reassembled
//   u8  frag_index   - 0-based fragment index
//   u8  frag_count   - total fragment count (>= 2)
//
// An unfragmented message is a single frame with flags == 0 and payload == the
// message body (UTF-8 JSON per the protocol v2 message model, WITHOUT the u32be
// length prefix used by the stream codec - the AC20 header already carries the
// length, and the JSON never crosses a stream boundary here).
//
// Sizing: one AC20 frame maps 1:1 onto one ESP-NOW packet. ESP-NOW v1.0 caps the
// payload at 250 bytes, so a full frame (header + payload + CRC) must be <= 250.
#pragma once

#include <cstddef>
#include <cstdint>

namespace tt::app::aetherchat::wire {

constexpr uint8_t MAGIC[4] = {'A', 'C', '2', '0'};

constexpr size_t HEADER_BYTES = 20;
constexpr size_t CRC_BYTES = 4;
constexpr size_t FRAG_HEADER_BYTES = 4;

// ESP-NOW v1.0 maximum on-air payload. An AC20 frame maps 1:1 to one packet.
constexpr size_t ESPNOW_MAX_PACKET = 250;

// Largest payload_len a single frame can carry.
constexpr size_t MAX_FRAME_PAYLOAD = ESPNOW_MAX_PACKET - HEADER_BYTES - CRC_BYTES; // 226

// Largest message-body chunk per fragmented frame (payload minus frag sub-header).
constexpr size_t MAX_FRAGMENT_CHUNK = MAX_FRAME_PAYLOAD - FRAG_HEADER_BYTES; // 222

constexpr uint16_t FLAG_FRAGMENTED = 1U << 0U;

// Message types: identical ordering to the protocol v2 MsgType enum
// (firmware/p4_aethercore/main/protocol/protocol_v2.h).
enum class MsgType : uint16_t {
    SessionOpen = 0,
    SessionResume = 1,
    UserText = 2,
    UserCancel = 3,
    Reset = 4,
    AssistantTextDelta = 5,
    ClarificationRequest = 6,
    TaskStatus = 7,
    ToolActivitySummary = 8,
    EvidenceSummary = 9,
    MemoryStatus = 10,
    Error = 11,
    Health = 12,
    Capabilities = 13,
};

constexpr bool isKnownType(uint16_t type) {
    return type <= static_cast<uint16_t>(MsgType::Capabilities);
}

// IEEE 802.3 CRC-32 (reflected, poly 0xEDB88320, init 0xFFFFFFFF, xorout 0xFFFFFFFF).
// Table-free bitwise implementation: frames here are <= 250 B and infrequent.
inline uint32_t crc32(const uint8_t* data, size_t length) {
    uint32_t crc = 0xFFFFFFFFU;
    for (size_t i = 0; i < length; ++i) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; ++bit) {
            crc = (crc >> 1U) ^ (0xEDB88320U & (0U - (crc & 1U)));
        }
    }
    return ~crc;
}

inline void put16(uint8_t* out, uint16_t value) {
    out[0] = static_cast<uint8_t>(value & 0xFFU);
    out[1] = static_cast<uint8_t>((value >> 8U) & 0xFFU);
}

inline void put32(uint8_t* out, uint32_t value) {
    for (int i = 0; i < 4; ++i) {
        out[i] = static_cast<uint8_t>((value >> (8U * i)) & 0xFFU);
    }
}

inline uint16_t get16(const uint8_t* in) {
    return static_cast<uint16_t>(in[0]) |
        static_cast<uint16_t>(static_cast<uint16_t>(in[1]) << 8U);
}

inline uint32_t get32(const uint8_t* in) {
    uint32_t result = 0;
    for (int i = 0; i < 4; ++i) {
        result |= static_cast<uint32_t>(in[i]) << (8U * i);
    }
    return result;
}

// Parsed frame view; payload points into the caller's buffer.
struct Frame {
    MsgType type = MsgType::Error;
    uint16_t flags = 0;
    uint32_t requestId = 0;
    uint32_t sessionId = 0;
    const uint8_t* payload = nullptr;
    size_t payloadLen = 0;
    // Fragment sub-header (valid only when (flags & FLAG_FRAGMENTED) != 0).
    uint16_t msgSeq = 0;
    uint8_t fragIndex = 0;
    uint8_t fragCount = 1;
};

// Serialize one frame into out[0..cap). Returns bytes written, or 0 on error
// (payload too large / buffer too small). If fragmented, payload must already
// include the 4-byte fragment sub-header; use writeFragHeader() to build it.
inline size_t encodeFrame(
    uint8_t* out, size_t cap,
    MsgType type, uint16_t flags,
    uint32_t requestId, uint32_t sessionId,
    const uint8_t* payload, size_t payloadLen
) {
    if (payloadLen > MAX_FRAME_PAYLOAD || cap < HEADER_BYTES + payloadLen + CRC_BYTES) {
        return 0;
    }
    out[0] = MAGIC[0];
    out[1] = MAGIC[1];
    out[2] = MAGIC[2];
    out[3] = MAGIC[3];
    put16(out + 4, static_cast<uint16_t>(type));
    put16(out + 6, flags);
    put32(out + 8, requestId);
    put32(out + 12, sessionId);
    put32(out + 16, static_cast<uint32_t>(payloadLen));
    for (size_t i = 0; i < payloadLen; ++i) {
        out[HEADER_BYTES + i] = payload[i];
    }
    const uint32_t crc = crc32(out + 4, HEADER_BYTES - 4 + payloadLen);
    put32(out + HEADER_BYTES + payloadLen, crc);
    return HEADER_BYTES + payloadLen + CRC_BYTES;
}

inline void writeFragHeader(uint8_t* out, uint16_t msgSeq, uint8_t fragIndex, uint8_t fragCount) {
    put16(out, msgSeq);
    out[2] = fragIndex;
    out[3] = fragCount;
}

// Strictly parse and validate one frame (magic, type, lengths, CRC, frag fields).
// Returns false on any malformation.
inline bool decodeFrame(const uint8_t* data, size_t size, Frame& out) {
    if (data == nullptr || size < HEADER_BYTES + CRC_BYTES || size > ESPNOW_MAX_PACKET) {
        return false;
    }
    if (data[0] != MAGIC[0] || data[1] != MAGIC[1] || data[2] != MAGIC[2] || data[3] != MAGIC[3]) {
        return false;
    }
    const uint16_t type = get16(data + 4);
    if (!isKnownType(type)) {
        return false;
    }
    const uint32_t payloadLen = get32(data + 16);
    if (payloadLen > MAX_FRAME_PAYLOAD || size != HEADER_BYTES + payloadLen + CRC_BYTES) {
        return false;
    }
    const uint32_t expectedCrc = get32(data + HEADER_BYTES + payloadLen);
    if (crc32(data + 4, HEADER_BYTES - 4 + payloadLen) != expectedCrc) {
        return false;
    }
    out.type = static_cast<MsgType>(type);
    out.flags = get16(data + 6);
    out.requestId = get32(data + 8);
    out.sessionId = get32(data + 12);
    out.payload = data + HEADER_BYTES;
    out.payloadLen = payloadLen;
    if ((out.flags & FLAG_FRAGMENTED) != 0) {
        if (payloadLen < FRAG_HEADER_BYTES) {
            return false;
        }
        out.msgSeq = get16(out.payload);
        out.fragIndex = out.payload[2];
        out.fragCount = out.payload[3];
        if (out.fragCount < 2 || out.fragIndex >= out.fragCount) {
            return false;
        }
        out.payload += FRAG_HEADER_BYTES;
        out.payloadLen -= FRAG_HEADER_BYTES;
    } else {
        out.msgSeq = 0;
        out.fragIndex = 0;
        out.fragCount = 1;
    }
    return true;
}

} // namespace tt::app::aetherchat::wire
