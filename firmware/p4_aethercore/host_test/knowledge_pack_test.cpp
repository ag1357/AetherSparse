/* Direct native PackProvider tests over synthetic ACP1EVD1 occurrence blobs. */

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <algorithm>
#include <set>
#include <string>
#include <vector>

#include "../main/knowledge_pack.h"

struct Pager {};

namespace {

std::vector<uint8_t> g_blob;
std::string g_title = "Ada Lovelace";
std::vector<AddressCandidate> g_address_candidates;
std::string g_surface_text;
bool g_cancel = false;

void AppendU16(uint16_t value) {
  g_blob.push_back(uint8_t(value & 0xff));
  g_blob.push_back(uint8_t(value >> 8));
}

void AppendU32(uint32_t value) {
  for (int shift = 0; shift < 32; shift += 8) {
    g_blob.push_back(uint8_t(value >> shift));
  }
}

void AppendOccurrence(const std::string& mention, const std::string& context,
                      uint16_t flags = 0) {
  AppendU32(0);
  g_blob.push_back(0);
  g_blob.push_back(0);
  AppendU16(uint16_t(mention.size()));
  AppendU16(uint16_t(context.size()));
  AppendU16(flags);
  g_blob.insert(g_blob.end(), mention.begin(), mention.end());
  g_blob.insert(g_blob.end(), context.begin(), context.end());
}

aethercore::service::RequestFrame Request(const std::string& query,
                                          const std::string& relation,
                                          const std::string& shape,
                                          bool general = false) {
  aethercore::service::RequestFrame request;
  request.query_text = query;
  request.relation_family = relation;
  request.answer_shape = shape;
  request.general_description = general;
  return request;
}

const aethercore::service::GroundedRecord* FindOccurrence(
    const aethercore::service::FetchResult& result, uint32_t occurrence) {
  for (const auto& record : result.records) {
    if (record.occurrence_index == occurrence) return &record;
  }
  return nullptr;
}

bool CancelProbe(void*) { return g_cancel; }

int failures = 0;

void Check(bool condition, const char* name) {
  printf("[%s] %s\n", condition ? "PASS" : "FAIL", name);
  if (!condition) failures++;
}

ac::knowledge::PackProvider StartedProvider() {
  ac::knowledge::PackProvider provider;
  Check(provider.start(64 * 1024), "provider:start");
  return provider;
}

void TestWhereAndWhen() {
  g_blob.clear();
  AppendOccurrence(
      "Ada Lovelace",
      "Ada Lovelace was born in London. She was an English mathematician.");
  AppendOccurrence(
      "Ada Lovelace",
      "Ada Lovelace (1815-1852) was an English mathematician and writer.", 1);
  auto provider = StartedProvider();
  aethercore::service::FetchOptions options;

  aethercore::service::FetchResult where;
  provider.FetchRecords(
      {"packv2:e0"},
      Request("Where was Ada Lovelace born?", "birth_place", "location"),
      options, &where);
  Check(where.status == aethercore::service::RetrievalStatus::kComplete,
        "where:fetch");
  const auto* place = FindOccurrence(where, 0);
  Check(place && place->relation == "birth_place" &&
            place->answer_kind == "LOCATION" &&
            place->values.front() == "London",
        "where:extract-location-not-date");

  aethercore::service::FetchResult when;
  provider.FetchRecords(
      {"packv2:e0"},
      Request("When was Ada Lovelace born?", "birth_date", "date"), options,
      &when);
  Check(when.status == aethercore::service::RetrievalStatus::kComplete,
        "when:fetch");
  const auto* date = FindOccurrence(when, 1);
  Check(date && date->relation == "birth_date" &&
            date->answer_kind == "DATE" && date->values.front() == "1815",
        "when:extract-date");
}

void TestSymmetricAddressCoverage() {
  auto provider = StartedProvider();
  g_address_candidates = {{0, 1, 6, 14}};
  g_surface_text = "florida senate";
  std::vector<aethercore::service::AddressHyp> weak;
  Check(provider.Address("florbnicate zxqv", &weak) && weak.empty(),
        "address:reject-weak-query-coverage");

  g_address_candidates = {{0, 1, 14, 12}};
  g_surface_text = "ada lovelace";
  std::vector<aethercore::service::AddressHyp> exact;
  Check(provider.Address("Ada Lovelace", &exact) && exact.size() == 1 &&
            exact.front().entity_id == "packv2:e0",
        "address:retain-full-query-coverage");

  g_address_candidates = {
      {UINT32_MAX, 1, 13, 11},
      {0, 2, 8, 9},
  };
  std::vector<aethercore::service::AddressHyp> shadowed;
  Check(provider.Address("Alan Turing", &shadowed) && shadowed.empty(),
        "address:unresolved-exact-blocks-weaker-fuzzy-entity");
  g_address_candidates.clear();
  g_surface_text.clear();
}

void TestSubjectBoundBiographicalRelations() {
  g_blob.clear();
  g_title = "Marie Curie";
  AppendOccurrence(
      "Marie Curie",
      "1910 - Otto Wallach for his work. 1911 - Marie Curie for her "
      "discovery of radium. 1919 - Another event.");
  AppendOccurrence(
      "Marie",
      "Personal life. Curie was born in Paris. Marie and Pierre Curie "
      "were her parents.");
  AppendOccurrence(
      "Marie Curie",
      "Maria Salomea Sklodowska-Curie (7 November 1867 - 4 July 1934) "
      "was a Polish physicist and chemist.",
      1);
  AppendOccurrence(
      "Marie Curie",
      "Marie Curie, born in partitioned Poland (Russian Empire), won "
      "major scientific prizes.");
  AppendOccurrence(
      "Marie Curie",
      "Marie Curie collaborated with Pierre Curie, who was born in Paris.");
  AppendOccurrence(
      "Marie Curie",
      "Marie Curie was born in Paris and won a major prize in 1903.");
  AppendOccurrence(
      "Marie Curie",
      "Marie Curie was born and later lived in Paris for many years.");
  auto provider = StartedProvider();
  aethercore::service::FetchOptions options;

  aethercore::service::FetchResult birth_date;
  provider.FetchRecords(
      {"packv2:e0"},
      Request("When was Marie Curie born?", "birth_date", "date"), options,
      &birth_date);
  const auto* unrelated_year = FindOccurrence(birth_date, 0);
  const auto* self_lead = FindOccurrence(birth_date, 2);
  const auto* intervening_date = FindOccurrence(birth_date, 5);
  Check(unrelated_year &&
            unrelated_year->support ==
                aethercore::service::EvidenceSupport::kRelatedBackground &&
            intervening_date &&
            intervening_date->support ==
                aethercore::service::EvidenceSupport::kRelatedBackground &&
            self_lead &&
            self_lead->support ==
                aethercore::service::EvidenceSupport::kDirectSupport &&
            self_lead->values.front() == "1867",
        "relations:ordered-self-lead-birth-slot");

  aethercore::service::FetchResult death_date;
  provider.FetchRecords(
      {"packv2:e0"},
      Request("When did Marie Curie die?", "death_date", "date"), options,
      &death_date);
  const auto* death_lead = FindOccurrence(death_date, 2);
  Check(death_lead &&
            death_lead->support ==
                aethercore::service::EvidenceSupport::kDirectSupport &&
            death_lead->values.front() == "1934",
        "relations:ordered-self-lead-death-slot");

  aethercore::service::FetchResult birth_place;
  provider.FetchRecords(
      {"packv2:e0"},
      Request("Where was Marie Curie born?", "birth_place", "location"),
      options, &birth_place);
  const auto* other_subject = FindOccurrence(birth_place, 1);
  const auto* bound_subject = FindOccurrence(birth_place, 3);
  const auto* embedded_subject = FindOccurrence(birth_place, 4);
  const auto* intervening_place = FindOccurrence(birth_place, 6);
  Check(other_subject &&
            other_subject->support ==
                aethercore::service::EvidenceSupport::kRelatedBackground &&
            embedded_subject &&
            embedded_subject->support ==
                aethercore::service::EvidenceSupport::kRelatedBackground &&
            intervening_place &&
            intervening_place->support ==
                aethercore::service::EvidenceSupport::kRelatedBackground &&
            bound_subject &&
            bound_subject->support ==
                aethercore::service::EvidenceSupport::kDirectSupport &&
            bound_subject->values.front() == "partitioned Poland",
        "relations:event-and-subject-share-clause");

  g_title = "Ada Lovelace";
}

void TestGenericTypedPredicatesAndInitials() {
  g_blob.clear();
  g_title = "Eiffel Tower";
  AppendOccurrence(
      "Eiffel Tower",
      "The Eiffel Tower is located in Paris, France. It is a wrought-iron "
      "landmark.");
  AppendOccurrence(
      "Eiffel Tower",
      "The Eiffel Tower was opened in 1889 for an international exposition.");
  auto provider = StartedProvider();
  aethercore::service::FetchOptions options;

  aethercore::service::FetchResult location;
  provider.FetchRecords(
      {"packv2:e0"},
      Request("Where is the Eiffel Tower?", "location", "location"), options,
      &location);
  const auto* place = FindOccurrence(location, 0);
  Check(place &&
            place->support ==
                aethercore::service::EvidenceSupport::kDirectSupport &&
            place->values.front() == "Paris, France",
        "relations:generic-location-predicate");

  aethercore::service::FetchResult date;
  provider.FetchRecords(
      {"packv2:e0"},
      Request("When did the Eiffel Tower open?", "date", "date"), options,
      &date);
  const auto* opened = FindOccurrence(date, 1);
  Check(opened &&
            opened->support ==
                aethercore::service::EvidenceSupport::kDirectSupport &&
            opened->values.front() == "1889",
        "relations:generic-date-predicate");

  g_blob.clear();
  g_title = "J. Robert Oppenheimer";
  AppendOccurrence(
      "J. Robert Oppenheimer",
      "J. Robert Oppenheimer was born in New York City and became a "
      "theoretical physicist.");
  aethercore::service::FetchResult initial;
  provider.FetchRecords(
      {"packv2:e0"},
      Request("Where was J. Robert Oppenheimer born?", "birth_place",
              "location"),
      options, &initial);
  const auto* initial_place = FindOccurrence(initial, 0);
  Check(initial_place &&
            initial_place->support ==
                aethercore::service::EvidenceSupport::kDirectSupport &&
            initial_place->values.front() == "New York City",
        "relations:initials-remain-subject-bound");

  g_title = "Ada Lovelace";
}

void TestConditionalSelfArticlePrior() {
  g_blob.clear();
  const std::string context =
      "Ada Lovelace was an English mathematician and early computing writer.";
  AppendOccurrence("Ada Lovelace", context, 0);
  AppendOccurrence("Ada Lovelace", context + " She wrote extensive notes.", 1);
  auto provider = StartedProvider();
  aethercore::service::FetchOptions options;

  aethercore::service::FetchResult general;
  provider.FetchRecords(
      {"packv2:e0"},
      Request("Tell me about Ada Lovelace", "describe", "definition", true),
      options, &general);
  aethercore::service::FetchResult specific;
  provider.FetchRecords(
      {"packv2:e0"},
      Request("Why Ada Lovelace", "cause", "definition", false), options,
      &specific);
  const auto* general_self = FindOccurrence(general, 1);
  const auto* specific_self = FindOccurrence(specific, 1);
  Check(general_self && specific_self &&
            general_self->relevance_score ==
                specific_self->relevance_score + 6,
        "self-prior:general-only");
}

void TestNegativeUsableScore() {
  g_blob.clear();
  AppendOccurrence(
      "Ada Lovelace",
      "######## Ada Lovelace was an English mathematician and computing writer.");
  auto provider = StartedProvider();
  aethercore::service::FetchOptions options;
  aethercore::service::FetchResult result;
  provider.FetchRecords(
      {"packv2:e0"},
      Request("Tell me about Ada Lovelace", "describe", "definition", true),
      options, &result);
  Check(!result.records.empty(), "score:negative-usable-retained");
}

void TestResumeMatchesFull() {
  g_blob.clear();
  for (int i = 0; i < 5; i++) {
    AppendOccurrence("Ada Lovelace",
                     "Ada Lovelace was a mathematician in record " +
                         std::to_string(i) + " with sufficient context.");
  }
  auto provider = StartedProvider();
  const auto request =
      Request("Tell me about Ada Lovelace", "describe", "definition", true);
  aethercore::service::FetchOptions full_options;
  aethercore::service::FetchResult full;
  provider.FetchRecords({"packv2:e0"}, request, full_options, &full);

  std::set<std::string> resumed_handles;
  aethercore::service::FetchOptions part_options;
  part_options.occurrence_budget = 2;
  for (int round = 0; round < 4; round++) {
    aethercore::service::FetchResult part;
    provider.FetchRecords({"packv2:e0"}, request, part_options, &part);
    for (const auto& record : part.records) {
      resumed_handles.insert(record.evidence.handle_id);
    }
    if (part.status == aethercore::service::RetrievalStatus::kComplete) break;
    Check(part.status ==
              aethercore::service::RetrievalStatus::kBudgetExhausted &&
              part.next_cursor.valid,
          "resume:bounded-status");
    part_options.cursor = part.next_cursor;
  }
  std::set<std::string> full_handles;
  for (const auto& record : full.records) {
    full_handles.insert(record.evidence.handle_id);
  }
  Check(resumed_handles == full_handles, "resume:equivalent-union");
}

void TestCancelAndCorrupt() {
  g_blob.clear();
  AppendOccurrence(
      "Ada Lovelace",
      "Ada Lovelace was an English mathematician with sufficient context.");
  auto provider = StartedProvider();
  aethercore::service::FetchOptions options;
  options.cancel_probe = &CancelProbe;
  g_cancel = true;
  aethercore::service::FetchResult cancelled;
  provider.FetchRecords(
      {"packv2:e0"},
      Request("Tell me about Ada Lovelace", "describe", "definition", true),
      options, &cancelled);
  Check(cancelled.status ==
            aethercore::service::RetrievalStatus::kCancelled &&
            cancelled.next_cursor.valid,
        "cancel:resumable");
  g_cancel = false;

  g_blob.resize(g_blob.size() - 3);
  options.cancel_probe = nullptr;
  aethercore::service::FetchResult corrupt;
  provider.FetchRecords(
      {"packv2:e0"},
      Request("Tell me about Ada Lovelace", "describe", "definition", true),
      options, &corrupt);
  Check(corrupt.status == aethercore::service::RetrievalStatus::kCorrupt,
        "corrupt:distinct-status");
}

void TestPassageExpansion() {
  g_blob.clear();
  std::string context =
      "Ada Lovelace was an English mathematician and computing writer.";
  context.append(1700, 'x');
  AppendOccurrence("Ada Lovelace", context);
  auto provider = StartedProvider();
  aethercore::service::FetchOptions options;
  options.max_passage_bytes = 2048;
  aethercore::service::FetchResult result;
  provider.FetchRecords(
      {"packv2:e0"},
      Request("Tell me about Ada Lovelace", "describe", "definition", true),
      options, &result);
  Check(!result.records.empty() &&
            result.records.front().evidence.exact_text.size() > 1536,
        "expansion:selected-passage-reread");
}

}  // namespace

Pager* pager_create(size_t) { return new Pager(); }
void pager_destroy(Pager* pager) { delete pager; }
void pager_stats(Pager*, PagerStats* out) { memset(out, 0, sizeof(*out)); }
uint32_t idx_address_candidates(Pager*, const char*, AddressCandidate* out,
                                uint32_t cap) {
  uint32_t count =
      std::min<uint32_t>(cap, uint32_t(g_address_candidates.size()));
  for (uint32_t i = 0; i < count; i++) out[i] = g_address_candidates[i];
  return count;
}
bool idx_surface_text(Pager*, uint32_t surface_id, char* out, size_t cap) {
  if (surface_id != 1 || g_surface_text.empty() || cap == 0) return false;
  snprintf(out, cap, "%s", g_surface_text.c_str());
  return true;
}
bool ent_title_at(uint32_t entity_idx, char* out, size_t cap) {
  if (entity_idx != 0 || cap == 0) return false;
  snprintf(out, cap, "%s", g_title.c_str());
  return true;
}
bool evd_lookup(Pager*, uint32_t entity_idx, uint32_t* blob_off,
                uint32_t* blob_len, uint32_t* count) {
  if (entity_idx != 0) return false;
  *blob_off = 100;
  *blob_len = uint32_t(g_blob.size());
  *count = 0;
  size_t pos = 0;
  while (pos + 12 <= g_blob.size()) {
    uint16_t mention_len = uint16_t(g_blob[pos + 6]) |
                           (uint16_t(g_blob[pos + 7]) << 8);
    uint16_t context_len = uint16_t(g_blob[pos + 8]) |
                           (uint16_t(g_blob[pos + 9]) << 8);
    uint32_t length = 12u + mention_len + context_len;
    if (pos + length > g_blob.size()) break;
    (*count)++;
    pos += length;
  }
  return true;
}
bool evd_blob_read(Pager*, uint32_t blob_off, uint32_t blob_len,
                   uint32_t rel_off, uint8_t* buffer, size_t length,
                   size_t* read_out) {
  if (blob_off != 100 || blob_len != g_blob.size() ||
      rel_off > g_blob.size()) {
    *read_out = 0;
    return false;
  }
  size_t got = std::min(length, g_blob.size() - rel_off);
  memcpy(buffer, g_blob.data() + rel_off, got);
  *read_out = got;
  return true;
}
const char* pack_id(void) { return "acpack:test"; }

int main() {
  TestWhereAndWhen();
  TestSymmetricAddressCoverage();
  TestSubjectBoundBiographicalRelations();
  TestGenericTypedPredicatesAndInitials();
  TestConditionalSelfArticlePrior();
  TestNegativeUsableScore();
  TestResumeMatchesFull();
  TestCancelAndCorrupt();
  TestPassageExpansion();
  printf("KNOWLEDGE PACK: %s (%d failures)\n",
         failures == 0 ? "PASS" : "FAIL", failures);
  return failures == 0 ? 0 : 1;
}
