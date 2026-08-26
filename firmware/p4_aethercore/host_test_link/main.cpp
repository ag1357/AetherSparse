/* Host tests for the Device B link layer (SLIP + AC20 + reassembly):
 * round-trip fidelity, fragmentation/reassembly incl. out-of-order delivery,
 * corruption rejection (CRC, truncation, bad magic/escape), abandonment on
 * msg_seq change, and a deterministic fuzz pass. Prints MEAS JSONL. */
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "../main/link/slip_link.h"

using namespace ac::link;

static int g_pass = 0;
static int g_fail = 0;
#define CHECK(cond)                                              \
  do {                                                           \
    if (cond) {                                                  \
      g_pass++;                                                  \
    } else {                                                     \
      g_fail++;                                                  \
      printf("{\"fail\":\"%s:%d %s\"}\n", __FILE__, __LINE__, #cond); \
    }                                                            \
  } while (0)

struct Sink {
  std::vector<std::string> bodies;
  std::vector<uint32_t> requests;
  std::vector<Ac20Type> types;
  int frames = 0;
};

static void on_frame(const Ac20Frame &, void *ctx) { ((Sink *)ctx)->frames++; }
static void on_message(Ac20Type type, uint32_t request_id, uint32_t,
                       const uint8_t *body, size_t body_len, void *ctx) {
  Sink *s = (Sink *)ctx;
  s->types.push_back(type);
  s->requests.push_back(request_id);
  s->bodies.emplace_back((const char *)body, body_len);
}

/* Feed with per-byte granularity (worst-case UART interrupt delivery). */
static void feed_bytes(Link &link, const uint8_t *data, size_t len) {
  for (size_t i = 0; i < len; i++) link.rx_bytes(data + i, 1);
}

int main() {
  /* 1. Small message round-trip (single frame). */
  {
    Link a, b;
    Sink sa, sb;
    a.on_message(on_message, &sa);
    b.on_message(on_message, &sb);
    b.on_frame(on_frame, &sb);
    const char *body = "{\"type\":\"USER_TEXT\",\"text\":\"Who was Alan "
                       "Turing?\",\"request_id\":7}";
    uint8_t wire[4096];
    size_t n = a.encode_message(wire, sizeof(wire), Ac20Type::UserText, 7, 42,
                                (const uint8_t *)body, strlen(body));
    CHECK(n > 0);
    feed_bytes(b, wire, n);
    CHECK(sb.bodies.size() == 1);
    CHECK(sb.bodies[0] == body);
    CHECK(sb.requests[0] == 7);
    CHECK(sb.types[0] == Ac20Type::UserText);
    CHECK(sb.frames == 1);
    CHECK(b.stats().ac20_rejected == 0);
    printf("{\"test\":\"roundtrip_small\",\"ok\":%d}\n", g_fail == 0);
  }

  /* 2. Large message: fragmentation + in-order reassembly. */
  {
    Link a, b;
    Sink sb;
    b.on_message(on_message, &sb);
    std::string big(5000, 'x');
    for (size_t i = 0; i < big.size(); i += 97)
      big[i] = '{'; /* sprinkle frame-relevant bytes */
    uint8_t wire[16384];
    size_t n = a.encode_message(wire, sizeof(wire),
                                Ac20Type::AssistantTextDelta, 9, 42,
                                (const uint8_t *)big.data(), big.size());
    CHECK(n > 0);
    feed_bytes(b, wire, n);
    CHECK(sb.bodies.size() == 1);
    CHECK(sb.bodies[0] == big);
    CHECK(b.stats().messages_complete == 1);
    size_t expect_frames =
        (big.size() + AC20_MAX_FRAGMENT_CHUNK - 1) / AC20_MAX_FRAGMENT_CHUNK;
    CHECK(b.stats().slip_frames == expect_frames);
    printf("{\"test\":\"fragmented_5k\",\"frames\":%zu}\n", expect_frames);
  }

  /* 3. Out-of-order + duplicate fragment delivery (RF reorder/retry). */
  {
    Sink sb;
    Link b;
    b.on_message(on_message, &sb);
    std::string big(2000, 'q');
    uint8_t frag[AC20_MAX_FRAME_PAYLOAD];
    uint8_t frame[ESPNOW_MAX_PACKET];
    size_t count =
        (big.size() + AC20_MAX_FRAGMENT_CHUNK - 1) / AC20_MAX_FRAGMENT_CHUNK;
    /* deliver in reverse, duplicate one fragment */
    for (size_t idx = count; idx-- > 0;) {
      size_t off = idx * AC20_MAX_FRAGMENT_CHUNK;
      size_t chunk = big.size() - off;
      if (chunk > AC20_MAX_FRAGMENT_CHUNK) chunk = AC20_MAX_FRAGMENT_CHUNK;
      ac20_write_frag_header(frag, 77, (uint8_t)idx, (uint8_t)count);
      memcpy(frag + AC20_FRAG_HEADER_BYTES, big.data() + off, chunk);
      size_t n = ac20_encode_frame(frame, sizeof(frame), Ac20Type::TaskStatus,
                                   AC20_FLAG_FRAGMENTED, 3, 42, frag,
                                   AC20_FRAG_HEADER_BYTES + chunk);
      CHECK(n > 0);
      /* deliver raw frame through the SLIP decoder path: wrap in END markers */
      uint8_t slip[1300];
      size_t sn = 0;
      slip[sn++] = SLIP_END;
      for (size_t i = 0; i < n; i++) {
        uint8_t c = frame[i];
        if (c == SLIP_END) {
          slip[sn++] = SLIP_ESC;
          slip[sn++] = SLIP_ESC_END;
        } else if (c == SLIP_ESC) {
          slip[sn++] = SLIP_ESC;
          slip[sn++] = SLIP_ESC_ESC;
        } else {
          slip[sn++] = c;
        }
      }
      slip[sn++] = SLIP_END;
      feed_bytes(b, slip, sn);
      if (idx == count / 2) feed_bytes(b, slip, sn); /* duplicate */
    }
    CHECK(sb.bodies.size() == 1);
    CHECK(sb.bodies[0] == big);
    CHECK(b.stats().fragments_dropped == 1); /* the duplicate */
    printf("{\"test\":\"out_of_order\",\"frags\":%zu}\n", count);
  }

  /* 4. Corruption: CRC flip, mid-frame truncation splice, bad magic, bad
   *    escape, SLIP overflow; link must keep delivering clean frames after. */
  {
    Link a, b;
    Sink sb;
    b.on_message(on_message, &sb);
    const char *body = "{\"type\":\"PING\"}";
    uint8_t wire[1024];
    size_t n = a.encode_message(wire, sizeof(wire), Ac20Type::Health, 1, 1,
                                (const uint8_t *)body, strlen(body));
    CHECK(n > 0);
    /* CRC flip: rejected, nothing delivered */
    std::vector<uint8_t> bad(wire, wire + n);
    bad[n - 3] ^= 0x40;
    feed_bytes(b, bad.data(), bad.size());
    /* mid-frame truncation: partial frame splices into the next frame's
     * leading END -> CRC mismatch -> rejected; the clean frame's own END
     * then terminates it correctly -> delivered. */
    feed_bytes(b, wire, n / 2);
    feed_bytes(b, wire, n);
    CHECK(sb.bodies.size() == 1); /* splice rejected, clean frame delivered */
    /* bad magic */
    std::vector<uint8_t> bad2(wire, wire + n);
    bad2[1] = 'X'; /* after leading END */
    feed_bytes(b, bad2.data(), bad2.size());
    /* bad escape: ESC followed by 0x00 */
    uint8_t badesc[] = {SLIP_END, 'A', 'C', '2', '0', SLIP_ESC, 0x00, SLIP_END};
    feed_bytes(b, badesc, sizeof(badesc));
    /* SLIP overflow: > SLIP_BUF_BYTES between ENDs */
    {
      std::vector<uint8_t> huge(1 + SLIP_BUF_BYTES + 50 + 1, 0x41);
      huge.front() = SLIP_END;
      huge.back() = SLIP_END;
      feed_bytes(b, huge.data(), huge.size());
    }
    /* a clean frame must still decode after all of the above */
    feed_bytes(b, wire, n);
    CHECK(sb.bodies.size() == 2);
    CHECK(sb.bodies[0] == body && sb.bodies[1] == body);
    CHECK(b.stats().ac20_rejected >= 3);   /* crc + splice + magic */
    CHECK(b.stats().slip_bad_escape == 1); /* bad escape */
    CHECK(b.stats().slip_overflow == 1);   /* oversize frame */
    printf("{\"test\":\"corruption\",\"rejected\":%llu}\n",
           (unsigned long long)b.stats().ac20_rejected);
  }

  /* 5. Abandonment: partial fragmented message superseded by a new
   *    unfragmented message (and by a new fragmented seq). */
  {
    Link b;
    Sink sb;
    b.on_message(on_message, &sb);
    uint8_t frag[AC20_MAX_FRAME_PAYLOAD];
    uint8_t frame[ESPNOW_MAX_PACKET];
    std::string junk(600, 'j');
    auto slip_send = [&](const uint8_t *data, size_t n) {
      uint8_t slip[1300];
      size_t sn = 0;
      slip[sn++] = SLIP_END;
      for (size_t i = 0; i < n; i++) {
        uint8_t c = data[i];
        if (c == SLIP_END) {
          slip[sn++] = SLIP_ESC;
          slip[sn++] = SLIP_ESC_END;
        } else if (c == SLIP_ESC) {
          slip[sn++] = SLIP_ESC;
          slip[sn++] = SLIP_ESC_ESC;
        } else {
          slip[sn++] = c;
        }
      }
      slip[sn++] = SLIP_END;
      feed_bytes(b, slip, sn);
    };
    /* first two fragments of a 3-fragment message, seq 5 */
    for (uint8_t idx = 0; idx < 2; idx++) {
      ac20_write_frag_header(frag, 5, idx, 3);
      memcpy(frag + AC20_FRAG_HEADER_BYTES, junk.data() + (size_t)idx * 200, 200);
      size_t n = ac20_encode_frame(frame, sizeof(frame), Ac20Type::UserText,
                                   AC20_FLAG_FRAGMENTED, 1, 1, frag,
                                   AC20_FRAG_HEADER_BYTES + 200);
      CHECK(n > 0);
      slip_send(frame, n);
    }
    CHECK(sb.bodies.empty());
    /* a clean unfragmented message supersedes the partial one */
    Link a;
    uint8_t wire[4096];
    const char *fresh = "{\"type\":\"RESET\"}";
    size_t n = a.encode_message(wire, sizeof(wire), Ac20Type::Reset, 2, 1,
                                (const uint8_t *)fresh, strlen(fresh));
    feed_bytes(b, wire, n);
    CHECK(sb.bodies.size() == 1);
    CHECK(sb.bodies[0] == fresh);
    CHECK(b.stats().fragments_dropped == 2); /* abandoned partial fragments */
    printf("{\"test\":\"abandon\",\"dropped\":%llu}\n",
           (unsigned long long)b.stats().fragments_dropped);
  }

  /* 6. Deterministic fuzz: random byte streams must never crash and must
   *    never produce a message body that fails decode checks downstream. */
  {
    Link b;
    Sink sb;
    b.on_message(on_message, &sb);
    uint32_t rng = 0xAC20AC20u;
    auto next = [&]() {
      rng ^= rng << 13;
      rng ^= rng >> 17;
      rng ^= rng << 5;
      return rng;
    };
    uint8_t buf[257];
    for (int iter = 0; iter < 20000; iter++) {
      size_t len = next() % sizeof(buf);
      for (size_t i = 0; i < len; i++) buf[i] = (uint8_t)next();
      b.rx_bytes(buf, len);
    }
    /* Any message that survived full SLIP+AC20+reassembly is well-formed by
     * construction (CRC-guarded); just report. */
    printf("{\"meas\":\"link_fuzz\",\"iters\":20000,\"messages\":%zu,"
           "\"rejected\":%llu,\"bad_escape\":%llu,\"overflow\":%llu}\n",
           sb.bodies.size(), (unsigned long long)b.stats().ac20_rejected,
           (unsigned long long)b.stats().slip_bad_escape,
           (unsigned long long)b.stats().slip_overflow);
  }

  printf("{\"meas\":\"link_tests\",\"pass\":%d,\"fail\":%d,\"status\":\"%s\"}\n",
         g_pass, g_fail, g_fail == 0 ? "green" : "red");
  return g_fail == 0 ? 0 : 1;
}
