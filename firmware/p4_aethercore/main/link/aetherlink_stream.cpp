#include "aetherlink_stream.h"

#include <algorithm>
#include <cstring>

namespace ac::aetherlink {

void FrameDecoder::reset() {
  header_size_ = 0;
  payload_size_ = 0;
  payload_received_ = 0;
  frame_ready_ = false;
}

void FrameDecoder::consume_frame() { reset(); }

DecodeStatus FrameDecoder::feed(const uint8_t *bytes, size_t length,
                                size_t *consumed) {
  if (consumed == nullptr) return DecodeStatus::kMalformedLength;
  *consumed = 0;
  if (frame_ready_) return DecodeStatus::kFrameReady;
  if (bytes == nullptr && length != 0) return DecodeStatus::kMalformedLength;

  if (header_size_ < kLengthBytes) {
    const size_t take = std::min(length, kLengthBytes - header_size_);
    if (take != 0) std::memcpy(header_ + header_size_, bytes, take);
    header_size_ += take;
    *consumed += take;
    bytes += take;
    length -= take;
    if (header_size_ != kLengthBytes) return DecodeStatus::kNeedMore;
    payload_size_ = (static_cast<size_t>(header_[0]) << 24) |
                    (static_cast<size_t>(header_[1]) << 16) |
                    (static_cast<size_t>(header_[2]) << 8) |
                    static_cast<size_t>(header_[3]);
    if (payload_size_ == 0 || payload_size_ > kMaxPayloadBytes) {
      reset();
      return DecodeStatus::kMalformedLength;
    }
  }

  const size_t take = std::min(length, payload_size_ - payload_received_);
  if (take != 0) std::memcpy(payload_ + payload_received_, bytes, take);
  payload_received_ += take;
  *consumed += take;
  if (payload_received_ != payload_size_) return DecodeStatus::kNeedMore;
  frame_ready_ = true;
  return DecodeStatus::kFrameReady;
}

bool valid(const Transport &transport) {
  return transport.open != nullptr && transport.close != nullptr &&
         transport.read != nullptr && transport.write != nullptr &&
         transport.connected != nullptr && transport.capabilities != nullptr &&
         transport.cancel != nullptr;
}

bool write_frame(Transport &transport, const uint8_t *payload, size_t length,
                 uint32_t timeout_ms) {
  if (!valid(transport) || payload == nullptr || length == 0 ||
      length > kMaxPayloadBytes || !transport.connected(transport.context)) {
    return false;
  }
  const uint8_t header[kLengthBytes] = {
      static_cast<uint8_t>(length >> 24), static_cast<uint8_t>(length >> 16),
      static_cast<uint8_t>(length >> 8), static_cast<uint8_t>(length)};
  const int first = transport.write(transport.context, header, sizeof(header),
                                    timeout_ms);
  if (first != static_cast<int>(sizeof(header))) return false;
  return transport.write(transport.context, payload, length, timeout_ms) ==
         static_cast<int>(length);
}

}  // namespace ac::aetherlink
