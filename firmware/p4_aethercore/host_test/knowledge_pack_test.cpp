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
uint32_t idx_address_candidates(Pager*, const char*, AddressCandidate*, uint32_t) {
  return 0;
}
bool idx_surface_text(Pager*, uint32_t, char*, size_t) { return false; }
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
  TestConditionalSelfArticlePrior();
  TestNegativeUsableScore();
  TestResumeMatchesFull();
  TestCancelAndCorrupt();
  TestPassageExpansion();
  printf("KNOWLEDGE PACK: %s (%d failures)\n",
         failures == 0 ? "PASS" : "FAIL", failures);
  return failures == 0 ? 0 : 1;
}
