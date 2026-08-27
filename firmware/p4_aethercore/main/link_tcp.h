/* Option A link for the physical deployment: Device B opens a deterministic
 * private softAP (via esp-hosted onto the factory C6; no C6 firmware change)
 * and serves the qualified protocol-v2 framed codec on a single-client TCP
 * listener bound to the AP interface. Cognition stays entirely on Device B.
 *
 * Two phases, split around the SD-dependent pack boot:
 *  - radio_up(): NVS + hosted + Wi-Fi softAP bring-up. MUST run before the
 *    SD card (SDMMC slot 0) is mounted: the C6 link is SDIO slot 1 on the
 *    shared SDMMC host peripheral, and slot-1 card init fails once slot 0
 *    is mounted (probe + Tactility both bring the radio up first). Blocks
 *    the caller until the AP is up or bring-up has failed.
 *  - serve(): after run_pack_boot() has succeeded; starts the TCP listener
 *    + accept/dispatch loop (and the one-time loopback self-test).
 *
 * All flash-writing / Wi-Fi / socket work runs on static-stack tasks so no
 * stack ever lands in TCM (P4 flash write cache-disable sanity assert).
 */

#pragma once

#include <cstddef>
#include <cstdint>

#include "link/ac20_wire.h" /* ac::link::Ac20Type (transport artifact type) */

namespace ac::linktcp {

struct Config {
  const char *ap_ssid;
  const char *ap_pass;
  int ap_channel;
  int tcp_port;
  bool loopback_selftest; /* connect-to-self frame proof, then serve normally */
};

bool radio_up(const Config &cfg); /* blocking; false = link failed */
void serve(void);                 /* starts listener; call after pack boot */

/* Registered with ac::runtime::service_set_response_sink. Called on the
 * link task; writes one framed protocol-v2 JSON envelope to the client. */
void response_sink(void *ctx, ac::link::Ac20Type type, uint32_t request_id,
                   uint32_t session_id, const uint8_t *json_body,
                   size_t body_len);

}  // namespace ac::linktcp
