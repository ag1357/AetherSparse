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

class KnowledgeProvider {
 public:
  virtual ~KnowledgeProvider() = default;

  // Bounded semantic-address candidates for a free-text query.
  virtual bool Address(const std::string& text,
                       std::vector<AddressHyp>* out) = 0;

  // Per-query grounded records for the selected entities, bounded by the
  // record/span/claim caps of the service workspace.
  virtual bool FetchRecords(const std::vector<std::string>& entity_ids,
                            const std::string& query_text,
                            std::vector<GroundedRecord>* out) = 0;
};

}  // namespace service
}  // namespace aethercore
