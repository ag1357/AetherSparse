/* AetherCore V15 native service core (Phase 9) — C++17 port of the Python
 * vertical slice (src/aethersparse/agent/vertical.py) for ESP32-P4.
 *
 * Pipeline per query, mirroring vertical.py exactly:
 *   interpret (COG-lite) -> relation scoring -> fuzzy address (EXACT +
 *   CHAR_NGRAM channels, union) -> conversation action selection
 *   (reset/cancel/clarify/continue with referent & follow-up context)
 *   -> workspace (claims + exact spans) -> learned int8 controller loop
 *   (legal-action mask + fixed-point scoring, <= 12 steps) -> exact
 *   verifier -> evidence-copy realizer -> completion gate -> response.
 *
 * Determinism and bounds: no exceptions, no RTTI, all lists capped, the
 * only floating point is in the address/claim feature scoring that feeds
 * integer quantization (round-half-even x256), matching
 * QuantizedAdaptivePolicy.  Telemetry is emitted as single-line
 * "MEAS {...}" JSONL through an injected sink for later on-device latency
 * breakdowns (Phase 18).
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <string>
#include <vector>

#include "service_records.h"

namespace aethercore {
namespace service {

// Service-level bounds (Python uses 8/16/32 for the corresponding lists).
constexpr size_t kMaxSessions = 16;
constexpr size_t kMaxCandidates = 8;    // conversation candidate cap
constexpr size_t kMaxChoices = 8;       // PendingClarification choice cap
constexpr size_t kMaxResolved = 16;     // previously_resolved_entities cap
constexpr size_t kMaxHandles = 32;      // session evidence handle cap
constexpr size_t kMaxClaims = 128;      // workspace claims (records x values)
constexpr size_t kMaxSpans = 16;        // workspace source spans
constexpr size_t kMaxStepsDefault = 12; // controller steps (vertical default)
constexpr size_t kMaxOpsLog = 64;       // vertical allows up to 64 steps
constexpr size_t kMaxSpanCandidates = 128;  // fuzzy span generation cap
constexpr size_t kMaxAddressResults = 32;   // address_cap used by vertical
constexpr size_t kMaxQueryBytes = 2048;     // AetherCoreRequest text bound
constexpr size_t kMaxSessionIdBytes = 128;  // AetherCoreRequest session bound

struct ServiceResponse {
  std::string disposition;  // ANSWER|CLARIFY|ABSTAIN|CANCELLED|RESET
  std::string session_id;
  std::string text;
  bool grounded = false;
  std::vector<std::string> evidence_handle_ids;
  std::vector<std::string> candidate_ids;
  std::vector<int> operations;
  bool verifier_accepted = false;
  bool has_failure = false;
  std::string failure_reason;
  // CLARIFY only: structured choices for the wire (Python operational.py
  // sends question + ("choice_id: label") tuple); `text` carries the same
  // content as one formatted sentence, exactly as vertical.py realises it.
  std::string clarify_question;
  std::vector<std::string> clarify_choices;
  std::vector<std::string> open_mandatory_obligations;
  // compact_view().packed_u16(): schema_version + 18 integer fields.
  uint16_t cog_state[19] = {};
};

// Telemetry sink: receives one NUL-terminated single-line "MEAS {...}" per
// call.  Return false from the query path unchanged; sink must not block.
typedef void (*MeasSink)(void* ctx, const char* line);
// Monotonic clock in microseconds (esp_timer_get_time on device).
typedef uint64_t (*ClockFn)(void* ctx);

class ServiceCore {
 public:
  ServiceCore();
  ~ServiceCore();
  ServiceCore(const ServiceCore&) = delete;
  ServiceCore& operator=(const ServiceCore&) = delete;

  // `policy_weights` is row-major [34][38] int8 for operation ids 32..65
  // (the frozen V14 artifact; see main/policy_v14_selected.h).
  bool Init(std::vector<GroundedRecord> records, const int8_t* policy_weights,
            size_t policy_weight_count, std::string* error);
  // Both setters are valid after a successful Init().
  void SetMeasSink(MeasSink sink, void* ctx);
  void SetClock(ClockFn clock, void* ctx);

  ServiceResponse Query(const std::string& session_id, const std::string& text);

 private:
  struct Impl;
  Impl* impl_;
};

}  // namespace service
}  // namespace aethercore
