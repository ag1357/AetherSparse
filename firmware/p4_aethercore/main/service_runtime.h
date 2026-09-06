/* Device B service runtime (V15): wires the native service core (Phase 9),
 * memory subsystem (Phase 10), protocol v2 codec (Phase 11) and the
 * transport-independent AetherLink stream into the operational handler,
 * mirroring
 * src/aethersparse/agent/operational.py semantics exactly:
 *
 *   SESSION_OPEN/RESUME -> HEALTH + CAPABILITIES
 *   USER_TEXT           -> user-memory interception (remember/list/recall/
 *                          edit/delete, authorized + persisted) else the
 *                          vertical query (interpret -> ground -> control ->
 *                          verify -> realize)
 *   USER_CANCEL/RESET   -> vertical "cancel"/"reset" utterance (as Python)
 *   HEALTH/CAPABILITIES -> live self-model from runtime state
 *
 * Transport note: USB/UART/TCP stream links carry the frozen four-byte u32be
 * prefix. The historical AC20 datagram envelope remains available only for
 * archived diagnostics. JSON bodies are byte-identical in either case.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "link/slip_link.h"

namespace aethercore {
namespace service {
class KnowledgeProvider;
}  // namespace service
}  // namespace aethercore

namespace ac::runtime {

struct RuntimeInfo {
  bool pack_verified = false;
  bool packv2_active = false;
  bool memory_persistent = false; /* false = session-only (store load failed) */
  uint64_t service_generation = 1;
  const char *pack_id = "";
  const char *storage_identity = "";
  uint32_t psram_free = 0;
  uint32_t internal_free = 0;
};

/* Outbound message sink: receives one complete protocol v2 JSON body (no
 * length prefix) plus the AC20 type and the request's AC20 routing ids
 * (echoed so the Tactility-side reassembler can correlate). The caller
 * (link glue) encodes and transmits. */
typedef void (*ResponseSink)(void *ctx, ac::link::Ac20Type type,
                             uint32_t request_id, uint32_t session_id,
                             const uint8_t *json_body, size_t body_len);

/* V15 production init: pack-backed knowledge provider (per-query retrieval
 * over the Semantic Address v2 + Pack-v2 corpus). Same memory-store and
 * fail-closed semantics as the retired fixture init. The V13 JSON fixture
 * path is not part of the production knowledge path; host-side tests that
 * need fixture records construct ServiceCore directly. */
bool service_init_pack(aethercore::service::KnowledgeProvider *provider,
                       const char *state_path, const int8_t *policy_weights,
                       size_t policy_weight_count, const RuntimeInfo &info,
                       char *err, size_t err_cap);

void service_set_response_sink(ResponseSink sink, void *ctx);

/* Dev/qualification console path: run one query through the full service
 * core and print disposition + text + evidence handles to the console.
 * Used by the optional stdin REPL (CONFIG_AC_SERVICE_CONSOLE_REPL). */
void service_console_query(const char *text);

/* Entry point for one complete protocol-v2 JSON body (no stream prefix).
 * Malformed bodies produce an ERROR response; they never crash the loop. */
void service_handle_message(ac::link::Ac20Type type, uint32_t request_id,
                            uint32_t session_id, const uint8_t *body,
                            size_t body_len);

/* MEAS-visible counters. */
uint64_t service_requests(void);
uint64_t service_errors(void);

}  // namespace ac::runtime
