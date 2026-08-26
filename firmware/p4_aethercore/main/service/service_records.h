/* Grounded knowledge record model + bounded JSON loader for the AetherCore
 * V15 native service core (ESP32-P4).
 *
 * Mirrors src/aethersparse/agent/vertical.py:GroundedKnowledgeRecord.  The
 * parser is deliberately minimal: it understands exactly the deployed
 * fixture shape (a JSON array of flat record objects with one nested
 * "evidence" object; string arrays; one numeric "confidence"), silently
 * skipping unknown keys with a generic bounded value skipper.  It is NOT a
 * general JSON library.  All collections are capped; oversized or malformed
 * input fails closed (returns false) rather than throwing.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <string>
#include <vector>

namespace aethercore {
namespace service {

// Deployment bounds (fixture: 4 records; generous headroom, still bounded).
constexpr size_t kMaxRecords = 16;
constexpr size_t kMaxSurfacesPerRecord = 8;
constexpr size_t kMaxRelationTerms = 8;
constexpr size_t kMaxValuesPerRecord = 8;
constexpr size_t kMaxSupportedValues = 8;
constexpr size_t kMaxStringBytes = 2048;
constexpr size_t kMaxJsonDepth = 16;

struct EvidenceHandleRec {
  std::string handle_id;
  std::string source_namespace;
  std::string canonical_object_id;
  std::string source_version;
  std::string source_locator;
  std::string exact_text;
  std::vector<std::string> supported_values;
};

struct GroundedRecord {
  std::string entity_id;
  std::string canonical_title;
  std::vector<std::string> address_surfaces;
  std::string relation;
  std::vector<std::string> relation_terms;
  std::string relation_text;
  std::string answer_kind;  // AnswerKind value, e.g. "FACTUAL_VALUE"
  std::vector<std::string> values;
  EvidenceHandleRec evidence;
  double confidence = 1.0;
};

// Parse the fixture document (array of grounded records).  Returns false and
// fills `error` on malformed input or bound violation.
bool ParseGroundedRecordsJson(const char* json, size_t length,
                              std::vector<GroundedRecord>* out,
                              std::string* error);

}  // namespace service
}  // namespace aethercore
