/* Device B link layer: SLIP (RFC 1055) byte stream <-> AC20 frames <->
 * reassembled protocol v2 messages. Matches espnow-bridge-c6 conventions:
 * one SLIP frame == one AC20 frame == one ESP-NOW packet; leading END flushes
 * partial frames; 921600 8N1.
 *
 * ESP-IDF UART glue is compiled only under ESP_PLATFORM; the codec core is
 * host-testable C++17 with no IDF dependency.
 *
 * Bounds: one SLIP frame <= 600 bytes (bridge SLIP_BUF_BYTES); reassembly
 * buffer 16 KiB (protocol v2 max body); one message in flight per direction
 * (interactive half-duplex link); a new msg_seq abandons an incomplete
 * previous message (sender always sends a message's fragments back-to-back).
 */
#pragma once

#include <cstddef>
#include <cstdint>

#include "ac20_wire.h"

namespace ac::link {

constexpr size_t SLIP_BUF_BYTES = 600;
constexpr size_t LINK_MAX_MESSAGE = 16384;
constexpr uint8_t SLIP_END = 0xC0;
constexpr uint8_t SLIP_ESC = 0xDB;
constexpr uint8_t SLIP_ESC_END = 0xDC;
constexpr uint8_t SLIP_ESC_ESC = 0xDD;

struct LinkStats {
  uint64_t slip_frames = 0;        /* complete SLIP frames seen */
  uint64_t slip_overflow = 0;      /* frames exceeding SLIP_BUF_BYTES */
  uint64_t slip_bad_escape = 0;
  uint64_t ac20_ok = 0;            /* AC20 frames passing validation */
  uint64_t ac20_rejected = 0;      /* bad magic/type/length/CRC/frag fields */
  uint64_t messages_complete = 0;  /* fully reassembled messages */
  uint64_t fragments_dropped = 0;  /* wrong seq / overflow / duplicate */
  uint64_t tx_frames = 0;
  uint64_t tx_bytes = 0;
};

/* Callbacks: frame = one validated AC20 frame; message = complete body. */
typedef void (*Ac20FrameCb)(const Ac20Frame &frame, void *ctx);
typedef void (*LinkMessageCb)(Ac20Type type, uint32_t request_id,
                              uint32_t session_id, const uint8_t *body,
                              size_t body_len, void *ctx);

class Link {
 public:
  Link();

  void on_frame(Ac20FrameCb cb, void *ctx) { frame_cb_ = cb; frame_ctx_ = ctx; }
  void on_message(LinkMessageCb cb, void *ctx) { msg_cb_ = cb; msg_ctx_ = ctx; }

  /* Feed raw UART bytes; SLIP-decodes, AC20-validates, reassembles. */
  void rx_bytes(const uint8_t *data, size_t len);

  /* Encode one logical message into SLIP-framed AC20 frames appended to out
   * (caller emits them to UART). Fragments when body exceeds one frame.
   * Returns bytes appended (0 on error: body > LINK_MAX_MESSAGE). */
  size_t encode_message(uint8_t *out, size_t cap, Ac20Type type,
                        uint32_t request_id, uint32_t session_id,
                        const uint8_t *body, size_t body_len);

  const LinkStats &stats() const { return stats_; }
  void reset_reassembly(); /* session reset / reconnect */

 private:
  void handle_frame(const Ac20Frame &f);

  Ac20FrameCb frame_cb_ = nullptr;
  void *frame_ctx_ = nullptr;
  LinkMessageCb msg_cb_ = nullptr;
  void *msg_ctx_ = nullptr;
  LinkStats stats_;

  /* SLIP decoder state */
  uint8_t slip_buf_[SLIP_BUF_BYTES];
  size_t slip_len_ = 0;
  bool slip_esc_ = false;
  bool slip_overflow_ = false;

  /* Reassembly state (single in-flight message) */
  uint16_t rx_seq_ = 0;
  uint16_t tx_seq_ = 0;
  bool rx_active_ = false;
  Ac20Type rx_type_ = Ac20Type::Error;
  uint32_t rx_request_ = 0;
  uint32_t rx_session_ = 0;
  uint8_t rx_frag_count_ = 0;
  uint8_t rx_frag_received_ = 0;
  uint8_t rx_bitmap_[12]; /* up to 96 fragments */
  size_t rx_body_len_ = 0;
  uint8_t rx_body_[LINK_MAX_MESSAGE];
};

}  // namespace ac::link
