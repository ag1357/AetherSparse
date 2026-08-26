/* AetherCore protocol v2 ESP-NOW wire framing ("AC20") — Device B mirror.
 *
 * Hand-synced copy of the single source of truth:
 *   tactility_p4_work/repos/Tactility/Tactility/Private/Tactility/app/
 *   aetherchat/protocol_v2_wire.h
 * Any change there must be mirrored here (both sides of the RF link).
 *
 * AC20 is the TRANSPORT envelope for the ESP-NOW/UART datagram path; the
 * payload is the protocol v2 JSON message body exactly as the native codec
 * (main/protocol/protocol_v2.h) and Python FramedJsonCodec produce it,
 * WITHOUT the u32be stream length prefix (the AC20 header carries length).
 *
 * Frame layout (little-endian on the wire):
 *   0:  char[4] magic = "AC20"
 *   4:  u16 type (MsgType ordering mirrors protocol_v2.h)
 *   6:  u16 flags (bit0 = FRAGMENTED)
 *   8:  u32 request_id
 *   12: u32 session_id
 *   16: u32 payload_len (includes 4-byte frag sub-header when FRAGMENTED)
 *   20: payload[payload_len]
 *   20+payload_len: u32 crc32 (IEEE 802.3 reflected, poly 0xEDB88320,
 *       init/xorout 0xFFFFFFFF) over bytes [4, 20+payload_len)
 *
 * Fragment sub-header (first 4 payload bytes when FRAGMENTED):
 *   u16 msg_seq, u8 frag_index (0-based), u8 frag_count (>=2)
 *
 * One AC20 frame maps 1:1 onto one ESP-NOW v1 packet (<= 250 B on air).
 */
#pragma once

#include <cstddef>
#include <cstdint>

namespace ac::link {

constexpr uint8_t AC20_MAGIC[4] = {'A', 'C', '2', '0'};
constexpr size_t AC20_HEADER_BYTES = 20;
constexpr size_t AC20_CRC_BYTES = 4;
constexpr size_t AC20_FRAG_HEADER_BYTES = 4;
constexpr size_t ESPNOW_MAX_PACKET = 250;
constexpr size_t AC20_MAX_FRAME_PAYLOAD =
    ESPNOW_MAX_PACKET - AC20_HEADER_BYTES - AC20_CRC_BYTES; /* 226 */
constexpr size_t AC20_MAX_FRAGMENT_CHUNK =
    AC20_MAX_FRAME_PAYLOAD - AC20_FRAG_HEADER_BYTES; /* 222 */
constexpr uint16_t AC20_FLAG_FRAGMENTED = 1U << 0U;

enum class Ac20Type : uint16_t {
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

constexpr bool ac20_known_type(uint16_t type) {
  return type <= static_cast<uint16_t>(Ac20Type::Capabilities);
}

inline uint32_t ac20_crc32(const uint8_t *data, size_t length) {
  uint32_t crc = 0xFFFFFFFFU;
  for (size_t i = 0; i < length; ++i) {
    crc ^= data[i];
    for (int bit = 0; bit < 8; ++bit)
      crc = (crc >> 1U) ^ (0xEDB88320U & (0U - (crc & 1U)));
  }
  return ~crc;
}

inline void ac20_put16(uint8_t *out, uint16_t v) {
  out[0] = static_cast<uint8_t>(v & 0xFFU);
  out[1] = static_cast<uint8_t>((v >> 8U) & 0xFFU);
}
inline void ac20_put32(uint8_t *out, uint32_t v) {
  for (int i = 0; i < 4; ++i)
    out[i] = static_cast<uint8_t>((v >> (8U * i)) & 0xFFU);
}
inline uint16_t ac20_get16(const uint8_t *in) {
  return static_cast<uint16_t>(in[0] |
                               (static_cast<uint16_t>(in[1]) << 8U));
}
inline uint32_t ac20_get32(const uint8_t *in) {
  uint32_t r = 0;
  for (int i = 0; i < 4; ++i) r |= static_cast<uint32_t>(in[i]) << (8U * i);
  return r;
}

struct Ac20Frame {
  Ac20Type type = Ac20Type::Error;
  uint16_t flags = 0;
  uint32_t request_id = 0;
  uint32_t session_id = 0;
  const uint8_t *payload = nullptr;
  size_t payload_len = 0;
  uint16_t msg_seq = 0;
  uint8_t frag_index = 0;
  uint8_t frag_count = 1;
};

inline size_t ac20_encode_frame(uint8_t *out, size_t cap, Ac20Type type,
                                uint16_t flags, uint32_t request_id,
                                uint32_t session_id, const uint8_t *payload,
                                size_t payload_len) {
  if (payload_len > AC20_MAX_FRAME_PAYLOAD ||
      cap < AC20_HEADER_BYTES + payload_len + AC20_CRC_BYTES)
    return 0;
  out[0] = AC20_MAGIC[0];
  out[1] = AC20_MAGIC[1];
  out[2] = AC20_MAGIC[2];
  out[3] = AC20_MAGIC[3];
  ac20_put16(out + 4, static_cast<uint16_t>(type));
  ac20_put16(out + 6, flags);
  ac20_put32(out + 8, request_id);
  ac20_put32(out + 12, session_id);
  ac20_put32(out + 16, static_cast<uint32_t>(payload_len));
  for (size_t i = 0; i < payload_len; ++i) out[AC20_HEADER_BYTES + i] = payload[i];
  const uint32_t crc =
      ac20_crc32(out + 4, AC20_HEADER_BYTES - 4 + payload_len);
  ac20_put32(out + AC20_HEADER_BYTES + payload_len, crc);
  return AC20_HEADER_BYTES + payload_len + AC20_CRC_BYTES;
}

inline void ac20_write_frag_header(uint8_t *out, uint16_t msg_seq,
                                   uint8_t frag_index, uint8_t frag_count) {
  ac20_put16(out, msg_seq);
  out[2] = frag_index;
  out[3] = frag_count;
}

inline bool ac20_decode_frame(const uint8_t *data, size_t size,
                              Ac20Frame &out) {
  if (data == nullptr || size < AC20_HEADER_BYTES + AC20_CRC_BYTES ||
      size > ESPNOW_MAX_PACKET)
    return false;
  if (data[0] != AC20_MAGIC[0] || data[1] != AC20_MAGIC[1] ||
      data[2] != AC20_MAGIC[2] || data[3] != AC20_MAGIC[3])
    return false;
  const uint16_t type = ac20_get16(data + 4);
  if (!ac20_known_type(type)) return false;
  const uint32_t payload_len = ac20_get32(data + 16);
  if (payload_len > AC20_MAX_FRAME_PAYLOAD ||
      size != AC20_HEADER_BYTES + payload_len + AC20_CRC_BYTES)
    return false;
  const uint32_t expected_crc = ac20_get32(data + AC20_HEADER_BYTES + payload_len);
  if (ac20_crc32(data + 4, AC20_HEADER_BYTES - 4 + payload_len) != expected_crc)
    return false;
  out.type = static_cast<Ac20Type>(type);
  out.flags = ac20_get16(data + 6);
  out.request_id = ac20_get32(data + 8);
  out.session_id = ac20_get32(data + 12);
  out.payload = data + AC20_HEADER_BYTES;
  out.payload_len = payload_len;
  if ((out.flags & AC20_FLAG_FRAGMENTED) != 0) {
    if (payload_len < AC20_FRAG_HEADER_BYTES) return false;
    out.msg_seq = ac20_get16(out.payload);
    out.frag_index = out.payload[2];
    out.frag_count = out.payload[3];
    if (out.frag_count < 2 || out.frag_index >= out.frag_count) return false;
    out.payload += AC20_FRAG_HEADER_BYTES;
    out.payload_len -= AC20_FRAG_HEADER_BYTES;
  } else {
    out.msg_seq = 0;
    out.frag_index = 0;
    out.frag_count = 1;
  }
  return true;
}

}  // namespace ac::link
