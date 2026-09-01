#include "aetherlink_stream.h"

#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <vector>

using ac::aetherlink::DecodeStatus;
using ac::aetherlink::FrameDecoder;

static std::vector<uint8_t> frame(const char *body) {
  const size_t n = std::strlen(body);
  std::vector<uint8_t> out = {static_cast<uint8_t>(n >> 24),
                              static_cast<uint8_t>(n >> 16),
                              static_cast<uint8_t>(n >> 8),
                              static_cast<uint8_t>(n)};
  out.insert(out.end(), body, body + n);
  return out;
}

static void expect_body(FrameDecoder &decoder, const char *body) {
  assert(decoder.payload_size() == std::strlen(body));
  assert(std::memcmp(decoder.payload(), body, decoder.payload_size()) == 0);
}

int main() {
  const auto a = frame("{\"type\":\"HEALTH\"}");
  const auto b = frame("{\"type\":\"CAPABILITIES\"}");
  FrameDecoder d;
  size_t used = 0;

  // Split length header, then split body.
  assert(d.feed(a.data(), 2, &used) == DecodeStatus::kNeedMore && used == 2);
  assert(d.feed(a.data() + 2, 5, &used) == DecodeStatus::kNeedMore && used == 5);
  assert(d.feed(a.data() + 7, a.size() - 7, &used) == DecodeStatus::kFrameReady);
  expect_body(d, "{\"type\":\"HEALTH\"}");

  // Two frames in one transport read: the decoder stops exactly at frame 1.
  d.consume_frame();
  std::vector<uint8_t> both = a;
  both.insert(both.end(), b.begin(), b.end());
  assert(d.feed(both.data(), both.size(), &used) == DecodeStatus::kFrameReady);
  assert(used == a.size());
  expect_body(d, "{\"type\":\"HEALTH\"}");
  d.consume_frame();
  size_t used2 = 0;
  assert(d.feed(both.data() + used, both.size() - used, &used2) ==
         DecodeStatus::kFrameReady);
  assert(used2 == b.size());
  expect_body(d, "{\"type\":\"CAPABILITIES\"}");

  // Disconnect halfway through a frame discards it; fresh framing works.
  d.consume_frame();
  assert(d.feed(a.data(), 9, &used) == DecodeStatus::kNeedMore);
  d.reset();
  assert(d.feed(b.data(), b.size(), &used) == DecodeStatus::kFrameReady);
  expect_body(d, "{\"type\":\"CAPABILITIES\"}");

  // Zero/oversize lengths fail closed.
  d.consume_frame();
  const std::array<uint8_t, 4> zero = {0, 0, 0, 0};
  assert(d.feed(zero.data(), zero.size(), &used) == DecodeStatus::kMalformedLength);
  const std::array<uint8_t, 4> huge = {0, 0, 0x40, 1};
  assert(d.feed(huge.data(), huge.size(), &used) == DecodeStatus::kMalformedLength);
  return 0;
}
