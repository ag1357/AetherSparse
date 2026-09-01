/* Transport-independent AetherLink byte-stream contract.
 *
 * Protocol-v2 framing lives here, above USB CDC, UART and deprecated TCP.
 * Backends only move bytes.  The decoder is allocation-free and deliberately
 * keeps exactly one bounded frame in flight.
 */
#pragma once

#include <cstddef>
#include <cstdint>

namespace ac::aetherlink {

constexpr size_t kMaxPayloadBytes = 16u * 1024u;
constexpr size_t kLengthBytes = 4u;

enum Capability : uint32_t {
  kCapabilityByteStream = 1u << 0,
  kCapabilityHotplug = 1u << 1,
  kCapabilityCancelIo = 1u << 2,
  kCapabilityUsbCdc = 1u << 3,
  kCapabilityUart = 1u << 4,
  kCapabilityDeprecatedTcp = 1u << 5,
};

struct Transport {
  void *context;
  bool (*open)(void *context);
  void (*close)(void *context);
  int (*read)(void *context, uint8_t *bytes, size_t capacity, uint32_t timeout_ms);
  int (*write)(void *context, const uint8_t *bytes, size_t length,
               uint32_t timeout_ms);
  bool (*connected)(void *context);
  uint32_t (*capabilities)(void *context);
  void (*cancel)(void *context);
};

enum class DecodeStatus : uint8_t {
  kNeedMore,
  kFrameReady,
  kMalformedLength,
};

class FrameDecoder {
 public:
  FrameDecoder() { reset(); }

  /* Feed one arbitrary transport fragment.  `consumed` always advances up to
   * the first complete frame or malformed prefix, so callers can process
   * multiple frames delivered by one USB/UART/TCP read. */
  DecodeStatus feed(const uint8_t *bytes, size_t length, size_t *consumed);
  const uint8_t *payload() const { return payload_; }
  size_t payload_size() const { return payload_size_; }
  void consume_frame();
  void reset(); /* required on disconnect: partial frame is never resumed */

 private:
  uint8_t header_[kLengthBytes];
  uint8_t payload_[kMaxPayloadBytes];
  size_t header_size_;
  size_t payload_size_;
  size_t payload_received_;
  bool frame_ready_;
};

bool valid(const Transport &transport);
bool write_frame(Transport &transport, const uint8_t *payload, size_t length,
                 uint32_t timeout_ms);

}  // namespace ac::aetherlink
