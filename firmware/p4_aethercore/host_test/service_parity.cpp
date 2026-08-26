/* Host parity harness for the AetherCore V15 native service core (Phase 9).
 *
 * Usage: service_parity <grounded-records.json> <script.tsv>
 *   script.tsv: one query per line, "session_id<TAB>text".
 *
 * Prints one RESULT {json} line per query (the fields diffed by
 * parity_check.py) and forwards MEAS telemetry lines to stdout.
 * The binary is pure C++17 with -fno-exceptions -fno-rtti and no ESP-IDF
 * dependency, proving the core logic builds for both host and P4 targets.
 */

#include <stdio.h>
#include <string.h>

#include <chrono>
#include <string>
#include <vector>

#include "../main/policy_v14_selected.h"
#include "../main/service/service_core.h"

namespace {

using aethercore::service::GroundedRecord;
using aethercore::service::ServiceCore;
using aethercore::service::ServiceResponse;

bool ReadFile(const char* path, std::string* out) {
  FILE* file = fopen(path, "rb");
  if (!file) return false;
  char buffer[8192];
  size_t got;
  while ((got = fread(buffer, 1, sizeof(buffer), file)) > 0) {
    out->append(buffer, got);
  }
  fclose(file);
  return true;
}

std::string JsonEscape(const std::string& value) {
  std::string out;
  for (char c : value) {
    switch (c) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default:
        if (unsigned(c) < 0x20) {
          char escape[8];
          snprintf(escape, sizeof(escape), "\\u%04x", unsigned(c));
          out += escape;
        } else {
          out.push_back(c);
        }
    }
  }
  return out;
}

void JsonStringArray(std::string* out, const std::vector<std::string>& items) {
  out->push_back('[');
  for (size_t i = 0; i < items.size(); i++) {
    if (i) out->push_back(',');
    *out += "\"" + JsonEscape(items[i]) + "\"";
  }
  out->push_back(']');
}

void EmitMeas(void* /*ctx*/, const char* line) { printf("%s\n", line); }

uint64_t ClockUs(void* /*ctx*/) {
  return uint64_t(std::chrono::duration_cast<std::chrono::microseconds>(
                      std::chrono::steady_clock::now().time_since_epoch())
                      .count());
}

}  // namespace

int main(int argc, char** argv) {
  if (argc != 3) {
    fprintf(stderr, "usage: %s <grounded-records.json> <script.tsv>\n", argv[0]);
    return 2;
  }
  std::string records_json;
  if (!ReadFile(argv[1], &records_json)) {
    fprintf(stderr, "cannot read records file: %s\n", argv[1]);
    return 2;
  }
  std::vector<GroundedRecord> records;
  std::string error;
  if (!aethercore::service::ParseGroundedRecordsJson(
          records_json.data(), records_json.size(), &records, &error)) {
    fprintf(stderr, "record parse failed: %s\n", error.c_str());
    return 2;
  }
  ServiceCore service;
  if (!service.Init(std::move(records), kAcV14PolicyWeights,
                    AC_V14_POLICY_PARAMETER_COUNT, &error)) {
    fprintf(stderr, "service init failed: %s\n", error.c_str());
    return 2;
  }
  service.SetMeasSink(&EmitMeas, nullptr);
  service.SetClock(&ClockUs, nullptr);

  std::string script;
  if (!ReadFile(argv[2], &script)) {
    fprintf(stderr, "cannot read script file: %s\n", argv[2]);
    return 2;
  }
  size_t cursor = 0;
  while (cursor < script.size()) {
    size_t eol = script.find('\n', cursor);
    if (eol == std::string::npos) eol = script.size();
    std::string line = script.substr(cursor, eol - cursor);
    cursor = eol + 1;
    if (!line.empty() && line.back() == '\r') line.pop_back();
    if (line.empty()) continue;
    size_t tab = line.find('\t');
    if (tab == std::string::npos) {
      fprintf(stderr, "bad script line (missing TAB): %s\n", line.c_str());
      return 2;
    }
    std::string session = line.substr(0, tab);
    std::string text = line.substr(tab + 1);
    ServiceResponse resp = service.Query(session, text);

    std::string out = "RESULT {";
    out += "\"session\":\"" + JsonEscape(session) + "\"";
    out += ",\"q\":\"" + JsonEscape(text) + "\"";
    out += ",\"disposition\":\"" + JsonEscape(resp.disposition) + "\"";
    out += ",\"text\":\"" + JsonEscape(resp.text) + "\"";
    out += resp.grounded ? ",\"grounded\":true" : ",\"grounded\":false";
    out += ",\"evidence_handle_ids\":";
    JsonStringArray(&out, resp.evidence_handle_ids);
    out += ",\"candidate_ids\":";
    JsonStringArray(&out, resp.candidate_ids);
    out += ",\"operations\":[";
    for (size_t i = 0; i < resp.operations.size(); i++) {
      if (i) out.push_back(',');
      out += std::to_string(resp.operations[i]);
    }
    out.push_back(']');
    out += resp.verifier_accepted ? ",\"verifier_accepted\":true"
                                  : ",\"verifier_accepted\":false";
    if (resp.has_failure) {
      out += ",\"failure_reason\":\"" + JsonEscape(resp.failure_reason) + "\"";
    } else {
      out += ",\"failure_reason\":null";
    }
    out += ",\"open_obligations\":";
    JsonStringArray(&out, resp.open_mandatory_obligations);
    out += ",\"cog_state\":[";
    for (int i = 0; i < 19; i++) {
      if (i) out.push_back(',');
      out += std::to_string(resp.cog_state[i]);
    }
    out.push_back(']');
    out.push_back('}');
    printf("%s\n", out.c_str());
  }
  return 0;
}
