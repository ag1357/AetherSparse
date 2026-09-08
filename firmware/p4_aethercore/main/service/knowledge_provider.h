/* Knowledge provider interface for the AetherCore V15 service core.
 *
 * The V13/V14 path kept one static in-RAM fixture vector for the whole
 * process lifetime.  V15 replaces that in production with a provider that
 * resolves semantic addresses and fetches bounded, per-query grounded
 * records from the pack on removable storage (no full-corpus RAM load).
 *
 * The interface is host-safe on purpose: no pack_io or ESP types appear
 * here, so the host test suite keeps building ServiceCore against the
 * fixture path while the firmware links the pack-backed implementation.
 *
 * Contract:
 *  - Address() maps free text to at most a handful of ranked candidates
 *    (the service applies its own conversation caps).  Empty result means
 *    "no grounded address", which the service turns into an abstention.
 *  - FetchRecords() fills per-query GroundedRecords for the selected
 *    entity ids.  The records must satisfy the same verifier contract as
 *    fixture records: every value is an exact substring of the record's
 *    evidence.exact_text (or listed in supported_values).
 */
#pragma once

#include <stdint.h>

#include <string>
#include <vector>

#include "service_records.h"

namespace aethercore {
namespace service {

struct AddressHyp {
  std::string entity_id;
  std::string label;
  double confidence = 0.0;  // [0,1], conversation ambiguity thresholds apply
  std::string matched_surface;
};

// Question semantics are established before retrieval and remain independent
// of whatever records happen to exist in the corpus. `relation_family` is a
// generic relation id (for example describe, birth_date, or location);
// `answer_shape` uses the service controller vocabulary
// (definition/date/location/quantity/quotation).
enum SupportObligation : uint32_t {
  kSupportSubject = 1u << 0,
  kSupportRelation = 1u << 1,
  kSupportAnswerType = 1u << 2,
  kSupportConstraints = 1u << 3,
  kSupportEvidence = 1u << 4,
};

struct RequestFrame {
  std::string query_text;
  std::string relation_family;
  std::string answer_shape;
  uint32_t required_obligations =
      kSupportSubject | kSupportRelation | kSupportAnswerType |
      kSupportEvidence;
  std::vector<std::string> constraint_terms;
  bool general_description = false;
  bool explicit_intent = false;
};

enum class RetrievalStatus {
  kComplete,
  kBudgetExhausted,
  kCancelled,
  kIoError,
  kCorrupt,
};

struct RetrievalCursor {
  bool valid = false;
  size_t entity_slot = 0;
  uint32_t relative_blob_offset = 0;
  uint32_t occurrence_index = 0;
};

typedef bool (*CancelProbe)(void* context);

struct FetchOptions {
  size_t max_candidates = 8;
  uint32_t occurrence_budget = 1024;
  uint32_t blob_byte_budget = 4u * 1024u * 1024u;
  size_t max_passage_bytes = 2048;
  RetrievalCursor cursor;
  CancelProbe cancel_probe = nullptr;
  void* cancel_context = nullptr;
};

struct FetchResult {
  std::vector<GroundedRecord> records;
  RetrievalStatus status = RetrievalStatus::kComplete;
  RetrievalCursor next_cursor;
  uint32_t occurrences_scanned = 0;
  uint32_t blob_bytes_scanned = 0;
};

class KnowledgeProvider {
 public:
  virtual ~KnowledgeProvider() = default;

  // Bounded semantic-address candidates for a free-text query.
  virtual bool Address(const std::string& text,
                       std::vector<AddressHyp>* out) = 0;

  // Per-query grounded records for the selected entities. The provider must
  // preserve `request` semantics and return bounded competing evidence, not
  // a preselected answer. Unsupported knowledge is represented by an empty
  // COMPLETE result; transport/storage failure uses an explicit status.
  virtual void FetchRecords(const std::vector<std::string>& entity_ids,
                            const RequestFrame& request,
                            const FetchOptions& options,
                            FetchResult* result) = 0;
};

}  // namespace service
}  // namespace aethercore
