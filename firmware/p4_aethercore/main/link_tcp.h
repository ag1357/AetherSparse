/* Selected physical link: Device B joins Device A's Tactility-owned AP as a
 * hosted-C6 station (no C6 firmware change) and initiates a persistent,
 * full-duplex protocol-v2 TCP connection to Device A. Cognition stays entirely
 * on Device B. The old Device-B AP/server topology is retained only behind
 * CONFIG_AC_LINK_LEGACY_B_AP_DIAGNOSTIC.
 *
 * Two phases, split around the SD-dependent pack boot:
 *  - radio_up(): NVS + hosted + Wi-Fi association/DHCP. MUST run before the
 *    SD card (SDMMC slot 0) is mounted: the C6 link is SDIO slot 1 on the
 *    shared SDMMC host peripheral, and slot-1 card init fails once slot 0
 *    is mounted (probe + Tactility both bring the radio up first). Blocks
 *    the caller until station DHCP succeeds or bring-up has failed.
 *  - serve(): after run_pack_boot() has succeeded; starts the persistent TCP
 *    client/reconnect + dispatch loop. In CONFIG_AC_LINK_DIAGNOSTIC_ONLY it is
 *    called immediately and never starts pack/cognition.
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
  const char *network_ssid;
  const char *network_pass;
  const char *device_a_ipv4; /* ignored by legacy AP diagnostic */
  int ap_channel;
  int tcp_port;
  int reconnect_delay_ms;
  int connect_timeout_ms;
  bool loopback_selftest; /* legacy AP/server diagnostic only */
  bool diagnostic_only;   /* transport-only, never AetherCore qualification */
};

bool radio_up(const Config &cfg); /* blocking until association/DHCP or failure */
void serve(void);                 /* starts client loop; call after pack boot */

/* Registered with ac::runtime::service_set_response_sink. Called on the
 * link task; writes one framed protocol-v2 JSON envelope to the client. */
void response_sink(void *ctx, ac::link::Ac20Type type, uint32_t request_id,
                   uint32_t session_id, const uint8_t *json_body,
                   size_t body_len);

}  // namespace ac::linktcp
