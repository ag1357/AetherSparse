/* Deterministic private AetherLink over local IP/TCP (Option A, mission
 * transport gate 2026-08-26): factory-C6 hosted softAP + a single-client
 * TCP server speaking the qualified protocol-v2 FramedJsonCodec stream
 * format (u32 BE length + JSON envelope, max 16 KiB). See
 * phase-notes/phase-option-a-tcp-spec.md.
 *
 * All work (NVS, hosted bring-up, Wi-Fi, sockets, dispatch, persistence)
 * runs on STATIC-STACK tasks: the P4 heap may place task stacks in TCM,
 * and flash-write paths assert on TCM-resident stacks (probe-verified).
 */
#pragma once

#include <cstddef>
#include <cstdint>

#include "link/ac20_wire.h"

namespace ac::linktcp {

struct Config {
  const char *ap_ssid;
  const char *ap_pass;
  int ap_channel;
  int tcp_port;
  bool loopback_selftest;
};

/* Starts the link (hosted AP + TCP listener + framed dispatch). Returns
 * once the listener is up (or bring-up failed); the accept/dispatch loop
 * keeps running on its own task either way when true. */
bool start(const Config &cfg);

/* Response sink compatible with ac::runtime::ResponseSink: frames the
 * protocol-v2 JSON body (u32 BE length prefix) and sends it to the current
 * client. AC20 type/ids are transport artifacts of the ESP-NOW link and
 * are ignored here — the JSON envelope is authoritative. */
void response_sink(void *ctx, ac::link::Ac20Type type, uint32_t request_id,
                   uint32_t session_id, const uint8_t *json_body,
                   size_t body_len);

}  // namespace ac::linktcp
