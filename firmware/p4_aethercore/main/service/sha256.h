/* Minimal SHA-256 (FIPS 180-4) for the AetherCore native service core.
 *
 * Used for claim IDs ("v13:claim:<hex[:24]>") and exact source-span text
 * hashes, mirroring hashlib.sha256 in the Python vertical slice.  No dynamic
 * allocation, no exceptions, no RTTI; safe for ESP32-P4 (-fno-exceptions).
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace aethercore {
namespace service {

class Sha256 {
 public:
  Sha256() { Reset(); }
  void Reset();
  void Update(const uint8_t* data, size_t length);
  void Update(const char* text);  // NUL-terminated convenience
  void Final(uint8_t out[32]);

 private:
  void Block(const uint8_t* block);
  uint32_t state_[8];
  uint8_t buffer_[64];
  uint64_t total_bits_;
  size_t buffer_len_;
};

// Hex digest (lowercase, 64 chars + NUL).  `out` must hold 65 bytes.
void Sha256Hex(const uint8_t* data, size_t length, char out[65]);
void Sha256Hex(const char* text, char out[65]);

}  // namespace service
}  // namespace aethercore
