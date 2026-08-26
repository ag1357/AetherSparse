/* See slip_link.h. */
#include "slip_link.h"

#include <cstring>

namespace ac::link {

Link::Link() { reset_reassembly(); }

void Link::reset_reassembly() {
  rx_active_ = false;
  rx_frag_count_ = 0;
  rx_frag_received_ = 0;
  rx_body_len_ = 0;
  memset(rx_bitmap_, 0, sizeof(rx_bitmap_));
}

void Link::rx_bytes(const uint8_t *data, size_t len) {
  for (size_t i = 0; i < len; i++) {
    uint8_t b = data[i];
    if (slip_esc_) {
      slip_esc_ = false;
      if (b == SLIP_ESC_END)
        b = SLIP_END;
      else if (b == SLIP_ESC_ESC)
        b = SLIP_ESC;
      else {
        stats_.slip_bad_escape += 1;
        slip_len_ = 0; /* protocol violation: drop partial frame */
        continue;
      }
      if (slip_len_ < SLIP_BUF_BYTES)
        slip_buf_[slip_len_++] = b;
      else
        slip_overflow_ = true;
      continue;
    }
    if (b == SLIP_ESC) {
      slip_esc_ = true;
      continue;
    }
    if (b == SLIP_END) {
      if (slip_len_ == 0) continue; /* leading/back-to-back END: flush marker */
      stats_.slip_frames += 1;
      if (slip_overflow_) {
        stats_.slip_overflow += 1;
      } else {
        Ac20Frame f;
        if (ac20_decode_frame(slip_buf_, slip_len_, f)) {
          stats_.ac20_ok += 1;
          if (frame_cb_) frame_cb_(f, frame_ctx_);
          handle_frame(f);
        } else {
          stats_.ac20_rejected += 1;
        }
      }
      slip_len_ = 0;
      slip_overflow_ = false;
      continue;
    }
    if (slip_len_ < SLIP_BUF_BYTES)
      slip_buf_[slip_len_++] = b;
    else
      slip_overflow_ = true;
  }
}

void Link::handle_frame(const Ac20Frame &f) {
  if ((f.flags & AC20_FLAG_FRAGMENTED) == 0) {
    /* Unfragmented: complete message, abandons any partial reassembly. */
    if (rx_active_) {
      stats_.fragments_dropped += rx_frag_received_;
      reset_reassembly();
    }
    stats_.messages_complete += 1;
    if (msg_cb_)
      msg_cb_(f.type, f.request_id, f.session_id, f.payload, f.payload_len,
              msg_ctx_);
    return;
  }
  if (rx_active_ && f.msg_seq != rx_seq_) {
    /* New message started before the previous completed: abandon. */
    stats_.fragments_dropped += rx_frag_received_;
    reset_reassembly();
  }
  if (!rx_active_) {
    if (f.frag_count > 96) {
      stats_.fragments_dropped += 1;
      return;
    }
    /* Reject a message that can never fit the reassembly buffer. */
    if ((size_t)f.frag_count * AC20_MAX_FRAGMENT_CHUNK > LINK_MAX_MESSAGE) {
      stats_.fragments_dropped += 1;
      return;
    }
    rx_active_ = true;
    rx_seq_ = f.msg_seq;
    rx_type_ = f.type;
    rx_request_ = f.request_id;
    rx_session_ = f.session_id;
    rx_frag_count_ = f.frag_count;
    rx_frag_received_ = 0;
    rx_body_len_ = 0;
    memset(rx_bitmap_, 0, sizeof(rx_bitmap_));
  }
  if (f.frag_count != rx_frag_count_ || f.frag_index >= rx_frag_count_ ||
      f.type != rx_type_ || f.request_id != rx_request_ ||
      f.session_id != rx_session_) {
    stats_.fragments_dropped += 1;
    return;
  }
  uint8_t mask = (uint8_t)(1U << (f.frag_index & 7U));
  uint8_t &slot = rx_bitmap_[f.frag_index >> 3U];
  if (slot & mask) {
    stats_.fragments_dropped += 1; /* duplicate fragment */
    return;
  }
  size_t off = (size_t)f.frag_index * AC20_MAX_FRAGMENT_CHUNK;
  if (off + f.payload_len > LINK_MAX_MESSAGE) {
    stats_.fragments_dropped += 1;
    return;
  }
  memcpy(rx_body_ + off, f.payload, f.payload_len);
  slot |= mask;
  rx_frag_received_ += 1;
  if (off + f.payload_len > rx_body_len_) rx_body_len_ = off + f.payload_len;
  if (rx_frag_received_ == rx_frag_count_) {
    stats_.messages_complete += 1;
    if (msg_cb_)
      msg_cb_(rx_type_, rx_request_, rx_session_, rx_body_, rx_body_len_,
              msg_ctx_);
    reset_reassembly();
  }
}

size_t Link::encode_message(uint8_t *out, size_t cap, Ac20Type type,
                            uint32_t request_id, uint32_t session_id,
                            const uint8_t *body, size_t body_len) {
  if (body_len > LINK_MAX_MESSAGE) return 0;
  size_t produced = 0;
  auto emit_frame = [&](uint16_t flags, const uint8_t *payload,
                        size_t payload_len) -> bool {
    uint8_t frame[ESPNOW_MAX_PACKET];
    size_t n = ac20_encode_frame(frame, sizeof(frame), type, flags, request_id,
                                 session_id, payload, payload_len);
    if (n == 0) return false;
    /* SLIP encode: leading END, escaping, trailing END. */
    if (produced + 2 * n + 2 > cap) return false;
    out[produced++] = SLIP_END;
    for (size_t i = 0; i < n; i++) {
      uint8_t b = frame[i];
      if (b == SLIP_END) {
        out[produced++] = SLIP_ESC;
        out[produced++] = SLIP_ESC_END;
      } else if (b == SLIP_ESC) {
        out[produced++] = SLIP_ESC;
        out[produced++] = SLIP_ESC_ESC;
      } else {
        out[produced++] = b;
      }
    }
    out[produced++] = SLIP_END;
    stats_.tx_frames += 1;
    return true;
  };

  if (body_len <= AC20_MAX_FRAME_PAYLOAD) {
    if (!emit_frame(0, body, body_len)) return 0;
  } else {
    size_t count =
        (body_len + AC20_MAX_FRAGMENT_CHUNK - 1) / AC20_MAX_FRAGMENT_CHUNK;
    if (count > 96) return 0;
    uint16_t seq = tx_seq_++;
    for (size_t idx = 0; idx < count; idx++) {
      size_t off = idx * AC20_MAX_FRAGMENT_CHUNK;
      size_t chunk = body_len - off;
      if (chunk > AC20_MAX_FRAGMENT_CHUNK) chunk = AC20_MAX_FRAGMENT_CHUNK;
      uint8_t payload[AC20_MAX_FRAME_PAYLOAD];
      ac20_write_frag_header(payload, seq, (uint8_t)idx, (uint8_t)count);
      memcpy(payload + AC20_FRAG_HEADER_BYTES, body + off, chunk);
      if (!emit_frame(AC20_FLAG_FRAGMENTED, payload,
                      AC20_FRAG_HEADER_BYTES + chunk))
        return 0;
    }
  }
  stats_.tx_bytes += produced;
  return produced;
}

}  // namespace ac::link
