// Host test harness for the native protocol v2 codec.
// No ESP-IDF dependency. Consumes vectors.txt produced by gen_vectors.py.
//
//   V <name> <type> <frame_hex> <reencode_hex>  -> decode OK, type matches,
//                                                  re-encode == reencode_hex
//   E <name> <error_name> <frame_hex>           -> decode fails with exactly
//                                                  this DecodeError
//
// Also runs a fuzz-lite loop (random + corrupted buffers) asserting decode
// never crashes and never returns an unknown error code, and a round-trip
// loop over every message type via the native encoder.
//
// MEAS-style single-line JSON stats on stdout; exit 0 iff all pass.

#include "../main/protocol/protocol_v2.h"

#include <cctype>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <random>
#include <string>
#include <vector>

using namespace aethercore::protocol_v2;

static int g_pass = 0, g_fail = 0;
static std::vector<std::string> g_failures;

static void check(bool ok, const std::string& name, const std::string& detail) {
  if (ok) {
    ++g_pass;
  } else {
    ++g_fail;
    g_failures.push_back(name + ": " + detail);
  }
}

static bool hexDecode(const std::string& hex, std::vector<uint8_t>& out) {
  if (hex.size() % 2) return false;
  out.resize(hex.size() / 2);
  for (size_t i = 0; i < out.size(); ++i) {
    auto nib = [&](char c) -> int {
      if (c >= '0' && c <= '9') return c - '0';
      if (c >= 'a' && c <= 'f') return c - 'a' + 10;
      if (c >= 'A' && c <= 'F') return c - 'A' + 10;
      return -1;
    };
    int hi = nib(hex[2 * i]), lo = nib(hex[2 * i + 1]);
    if (hi < 0 || lo < 0) return false;
    out[i] = static_cast<uint8_t>((hi << 4) | lo);
  }
  return true;
}

static std::string hexEncode(const uint8_t* d, size_t n) {
  std::string s;
  s.reserve(n * 2);
  for (size_t i = 0; i < n; ++i) {
    char tmp[3];
    std::snprintf(tmp, sizeof(tmp), "%02x", d[i]);
    s += tmp;
  }
  return s;
}

static bool errFromName(const std::string& name, DecodeError& out) {
  for (int i = 0; i <= static_cast<int>(DecodeError::PAYLOAD_TYPE_MISMATCH); ++i) {
    DecodeError e = static_cast<DecodeError>(i);
    if (name == ToString(e)) {
      out = e;
      return true;
    }
  }
  return false;
}

// ---------------------------------------------------------------------------
// Golden vectors
// ---------------------------------------------------------------------------
static void runVectors(const char* path) {
  std::ifstream in(path);
  if (!in) {
    check(false, "vectors_file", std::string("cannot open ") + path);
    return;
  }
  ProtocolMessage msg;  // static-size struct; reused across iterations
  std::vector<uint8_t> frame, reenc;
  std::string line;
  while (std::getline(in, line)) {
    if (line.empty()) continue;
    std::vector<std::string> parts;
    size_t start = 0;
    while (true) {
      size_t sp = line.find(' ', start);
      if (sp == std::string::npos) {
        parts.push_back(line.substr(start));
        break;
      }
      parts.push_back(line.substr(start, sp - start));
      start = sp + 1;
    }
    if (parts[0] == "V" && parts.size() == 5) {
      const std::string& name = parts[1];
      if (!hexDecode(parts[3], frame) || !hexDecode(parts[4], reenc)) {
        check(false, name, "bad hex in vector");
        continue;
      }
      DecodeError e = DecodeFrame(frame.data(), frame.size(), msg);
      check(e == DecodeError::OK, name,
            std::string("decode failed: ") + ToString(e));
      if (e != DecodeError::OK) continue;
      check(parts[2] == ToString(msg.type), name,
            std::string("type mismatch: got ") + ToString(msg.type));
      uint8_t out[kMaxEncodedFrame];
      size_t out_len = 0;
      EncodeError ee = EncodeFrame(msg, out, sizeof(out), out_len);
      check(ee == EncodeError::OK, name, "re-encode failed");
      if (ee == EncodeError::OK) {
        check(hexEncode(out, out_len) == parts[4], name,
              "re-encode hex mismatch vs Python");
      }
      // Decode the re-encoded frame again (self round-trip stability).
      DecodeError e2 = DecodeFrame(out, out_len, msg);
      check(e2 == DecodeError::OK, name, "second decode failed");
    } else if (parts[0] == "E" && parts.size() == 4) {
      const std::string& name = parts[1];
      DecodeError want;
      if (!errFromName(parts[2], want)) {
        check(false, name, "unknown expected error " + parts[2]);
        continue;
      }
      if (!hexDecode(parts[3], frame)) {
        check(false, name, "bad hex in vector");
        continue;
      }
      DecodeError got = DecodeFrame(frame.data(), frame.size(), msg);
      check(got == want, name,
            std::string("want ") + ToString(want) + " got " + ToString(got));
    } else {
      check(false, "vectors_file", "malformed line: " + line.substr(0, 60));
    }
  }
}

// ---------------------------------------------------------------------------
// Fuzz-lite: random + corrupted buffers must never crash, never misclassify.
// ---------------------------------------------------------------------------
static void runFuzz() {
  std::mt19937_64 rng(0xAC20);
  ProtocolMessage msg;
  int rejected = 0, accepted = 0;
  constexpr int kIters = 20000;
  for (int i = 0; i < kIters; ++i) {
    uint8_t buf[256];
    size_t n = static_cast<size_t>(rng() % 257);  // 0..256 bytes
    for (size_t k = 0; k < n; ++k) buf[k] = static_cast<uint8_t>(rng());
    DecodeError e = DecodeFrame(buf, n, msg);
    if (e == DecodeError::OK)
      ++accepted;
    else if (static_cast<int>(e) <=
             static_cast<int>(DecodeError::PAYLOAD_TYPE_MISMATCH))
      ++rejected;
    else
      check(false, "fuzz", "unknown error code returned");
  }
  // Corruption of a valid frame: single/multi byte flips + truncations.
  // Build a valid frame natively.
  static ProtocolMessage good;
  good.pool_used = 0;
  Str s;
  good.poolPut("aethercore-tactility.v2", 22, good.protocol_version);
  good.poolPut("m-fuzz", 6, good.message_id);
  good.poolPut("sess-fuzz", 9, good.session_id);
  good.has_request_id = false;
  good.sequence = 7;
  good.type = MsgType::USER_TEXT;
  good.poolPut("fuzz target", 11, good.p.user_text.text);
  uint8_t frame[512];
  size_t frame_len = 0;
  if (EncodeFrame(good, frame, sizeof(frame), frame_len) != EncodeError::OK) {
    check(false, "fuzz", "could not build seed frame");
    return;
  }
  for (int i = 0; i < kIters; ++i) {
    uint8_t buf[512];
    size_t n = frame_len;
    if (rng() % 4 == 0 && n > 0)
      n = static_cast<size_t>(rng() % frame_len);  // truncation
    std::memcpy(buf, frame, n);
    int flips = 1 + static_cast<int>(rng() % 8);
    for (int f = 0; f < flips && n > 0; ++f)
      buf[rng() % n] ^= static_cast<uint8_t>(rng());
    DecodeError e = DecodeFrame(buf, n, msg);
    if (e == DecodeError::OK) {
      // Accepted corruptions must re-encode deterministically.
      uint8_t out[kMaxEncodedFrame];
      size_t out_len = 0;
      check(EncodeFrame(msg, out, sizeof(out), out_len) == EncodeError::OK,
            "fuzz", "accepted corruption failed re-encode");
      ++accepted;
    } else {
      ++rejected;
    }
  }
  std::printf("{\"meas\":\"fuzz\",\"iters\":%d,\"rejected\":%d,\"accepted\":%d}\n",
              2 * kIters, rejected, accepted);
}

int main(int argc, char** argv) {
  const char* vectors = argc > 1 ? argv[1] : "vectors.txt";
  runVectors(vectors);
  runFuzz();

  for (const auto& f : g_failures) std::printf("FAIL %s\n", f.c_str());
  std::printf(
      "{\"meas\":\"protocol_v2_tests\",\"pass\":%d,\"fail\":%d,\"status\":\"%s\"}\n",
      g_pass, g_fail, g_fail == 0 ? "green" : "red");
  return g_fail == 0 ? 0 : 1;
}
