/* Native provider-mode contract tests for V15.
 *
 * These tests intentionally use a fake KnowledgeProvider. They verify the
 * service/provider seam without a static fixture or ESP/pack dependency.
 */

#include <stdio.h>

#include <string>
#include <utility>
#include <vector>

#include "../main/policy_v14_selected.h"
#include "../main/service/knowledge_provider.h"
#include "../main/service/service_core.h"

namespace {

using aethercore::service::AddressHyp;
using aethercore::service::EvidenceSupport;
using aethercore::service::FetchOptions;
using aethercore::service::FetchResult;
using aethercore::service::GroundedRecord;
using aethercore::service::KnowledgeProvider;
using aethercore::service::RequestFrame;
using aethercore::service::RetrievalStatus;
using aethercore::service::ServiceCore;
using aethercore::service::ServiceResponse;
using aethercore::service::kSupportAnswerType;
using aethercore::service::kSupportEvidence;
using aethercore::service::kSupportRelation;
using aethercore::service::kSupportSubject;

constexpr uint32_t kFullSupport =
    kSupportSubject | kSupportRelation | kSupportAnswerType | kSupportEvidence;

GroundedRecord Record(const std::string& entity, const std::string& title,
                      const std::string& relation,
                      const std::string& answer_kind,
                      const std::string& value, const std::string& evidence,
                      const std::string& handle, EvidenceSupport support,
                      uint32_t obligations, double confidence,
                      int relevance = 0) {
  GroundedRecord record;
  record.entity_id = entity;
  record.canonical_title = title;
  record.address_surfaces.push_back(title);
  record.relation = relation;
  record.relation_text =
      relation == "birth_date" ? "was born in"
      : relation == "birth_place" ? "was born in"
      : relation == "describe" ? "describes"
                               : "is";
  record.answer_kind = answer_kind;
  record.values.push_back(value);
  record.evidence.handle_id = handle;
  record.evidence.source_namespace = "fake";
  record.evidence.canonical_object_id = entity;
  record.evidence.source_version = "test";
  record.evidence.source_locator = "fake://" + handle;
  record.evidence.exact_text = evidence;
  record.confidence = confidence;
  record.support = support;
  record.supported_obligations = obligations;
  record.relevance_score = relevance;
  return record;
}

class FakeProvider final : public KnowledgeProvider {
 public:
  enum Scenario {
    kGeneralDescription,
    kOnlyDate,
    kOnlyBackground,
    kCompeting,
    kEmpty,
    kFailure,
    kResume,
    kAmbiguous,
    kWrongShape,
  };

  explicit FakeProvider(Scenario scenario) : scenario_(scenario) {}

  bool Address(const std::string& text, std::vector<AddressHyp>* out) override {
    out->clear();
    if (scenario_ == kAmbiguous &&
        text.find("Mercury") != std::string::npos) {
      out->push_back(
          AddressHyp{"entity:mercury:planet", "Mercury (planet)", 0.90,
                     "Mercury"});
      out->push_back(
          AddressHyp{"entity:mercury:element", "Mercury (element)", 0.86,
                     "Mercury"});
      return true;
    }
    const bool charles = text.find("Charles") != std::string::npos;
    const bool ada = text.find("Ada") != std::string::npos ||
                     text.find("she") != std::string::npos;
    if (!charles && !ada) return true;
    AddressHyp hit;
    hit.entity_id = charles ? "entity:charles" : "entity:ada";
    hit.label = charles ? "Charles Babbage" : "Ada Lovelace";
    hit.confidence = 0.99;
    hit.matched_surface = hit.label;
    out->push_back(std::move(hit));
    return true;
  }

  void FetchRecords(const std::vector<std::string>& entity_ids,
                    const RequestFrame& request, const FetchOptions& options,
                    FetchResult* result) override {
    requests.push_back(request);
    cursors.push_back(options.cursor);
    result->records.clear();
    result->status = RetrievalStatus::kComplete;
    result->next_cursor = {};
    if (scenario_ == kFailure) {
      result->status = RetrievalStatus::kIoError;
      return;
    }
    if (entity_ids.empty() || scenario_ == kEmpty) return;

    const std::string& entity = entity_ids.front();
    const std::string title =
        entity == "entity:charles"
            ? "Charles Babbage"
            : entity == "entity:mercury:planet"
                  ? "Mercury (planet)"
                  : entity == "entity:mercury:element" ? "Mercury (element)"
                                                       : "Ada Lovelace";
    const std::string date = entity == "entity:charles" ? "1791" : "1815";
    const std::string date_evidence =
        title + " was born in " + date + " in London.";
    const GroundedRecord date_record =
        Record(entity, title, "birth_date", "DATE", date, date_evidence,
               "evidence:date:" + entity, EvidenceSupport::kDirectSupport,
               kFullSupport, 0.80, 8);
    const GroundedRecord background =
        Record(entity, title, "describe", "QUOTATION",
               title + " was a mathematician.",
               title + " was a mathematician.", "evidence:bg:" + entity,
               EvidenceSupport::kRelatedBackground,
               kSupportSubject | kSupportEvidence, 1.0, 20);

    if (scenario_ == kResume && !options.cursor.valid) {
      result->records.push_back(background);
      result->status = RetrievalStatus::kBudgetExhausted;
      result->next_cursor = {true, 0, 64, 1};
      return;
    }
    if (scenario_ == kGeneralDescription || scenario_ == kAmbiguous) {
      result->records.push_back(
          Record(entity, title, "describe", "QUOTATION",
                 title + " was a mathematician.",
                 title + " was a mathematician.", "evidence:describe:" + entity,
                 EvidenceSupport::kDirectSupport, kFullSupport, 0.9, 9));
    } else if (scenario_ == kOnlyDate || scenario_ == kResume) {
      result->records.push_back(date_record);
    } else if (scenario_ == kOnlyBackground) {
      result->records.push_back(background);
    } else if (scenario_ == kWrongShape) {
      result->records.push_back(
          Record(entity, title, "birth_place", "QUOTATION",
                 title + " was born in London.", title + " was born in London.",
                 "evidence:wrong-shape:" + entity,
                 EvidenceSupport::kDirectSupport, kFullSupport, 0.95, 12));
    } else if (scenario_ == kCompeting) {
      result->records.push_back(background);
      result->records.push_back(date_record);
    }
  }

  std::vector<RequestFrame> requests;
  std::vector<aethercore::service::RetrievalCursor> cursors;

 private:
  Scenario scenario_;
};

bool Init(FakeProvider* provider, ServiceCore* service) {
  std::string error;
  if (service->InitWithProvider(provider, kAcV14PolicyWeights,
                                AC_V14_POLICY_PARAMETER_COUNT, &error)) {
    return true;
  }
  fprintf(stderr, "init failed: %s\n", error.c_str());
  return false;
}

int failures = 0;

void Check(bool condition, const char* name) {
  printf("[%s] %s\n", condition ? "PASS" : "FAIL", name);
  if (!condition) failures++;
}

void TestGeneralDescription() {
  FakeProvider provider(FakeProvider::kGeneralDescription);
  ServiceCore service;
  Check(Init(&provider, &service), "general:init");
  ServiceResponse response = service.Query("general", "Tell me about Ada Lovelace");
  Check(response.disposition == "ANSWER", "general:answer");
  Check(response.support_level ==
            aethercore::service::SupportLevel::kFull,
        "general:full-support");
  Check(!provider.requests.empty() &&
            provider.requests.back().general_description &&
            provider.requests.back().relation_family == "describe",
        "general:typed-describe");
}

void TestWhereIsNotWhen() {
  FakeProvider provider(FakeProvider::kOnlyDate);
  ServiceCore service;
  Check(Init(&provider, &service), "where:init");
  ServiceResponse response =
      service.Query("where", "Where was Ada Lovelace born?");
  Check(!provider.requests.empty() &&
            provider.requests.back().relation_family == "birth_place" &&
            provider.requests.back().answer_shape == "location",
        "where:location-frame");
  Check(response.disposition == "ABSTAIN" &&
            response.support_level ==
                aethercore::service::SupportLevel::kPartial &&
            response.failure_reason == "PARTIAL_SUPPORT",
        "where:date-cannot-answer");
}

void TestBackgroundPartial() {
  FakeProvider provider(FakeProvider::kOnlyBackground);
  ServiceCore service;
  Check(Init(&provider, &service), "partial:init");
  ServiceResponse response =
      service.Query("partial", "When was Ada Lovelace born?");
  Check(response.disposition == "ABSTAIN" &&
            response.support_level ==
                aethercore::service::SupportLevel::kPartial &&
            response.grounded,
        "partial:grounded-background");
  Check(!response.evidence_handle_ids.empty(), "partial:evidence-retained");
}

void TestFullBeatsBackground() {
  FakeProvider provider(FakeProvider::kCompeting);
  ServiceCore service;
  Check(Init(&provider, &service), "competing:init");
  ServiceResponse response =
      service.Query("competing", "When was Ada Lovelace born?");
  Check(response.disposition == "ANSWER" &&
            response.text.find("1815") != std::string::npos,
        "competing:typed-evidence-wins");
  Check(response.evidence_handle_ids.size() == 1 &&
            response.evidence_handle_ids.front().find("date") !=
                std::string::npos,
        "competing:date-lineage");
}

void TestFrameInheritance() {
  FakeProvider provider(FakeProvider::kOnlyDate);
  ServiceCore service;
  Check(Init(&provider, &service), "inherit:init");
  ServiceResponse first =
      service.Query("inherit", "When was Ada Lovelace born?");
  ServiceResponse second =
      service.Query("inherit", "What about Charles Babbage?");
  Check(first.disposition == "ANSWER" && second.disposition == "ANSWER",
        "inherit:both-answer");
  Check(provider.requests.size() >= 2 &&
            provider.requests.back().relation_family == "birth_date" &&
            provider.requests.back().answer_shape == "date",
        "inherit:complete-frame");
  Check(second.text.find("1791") != std::string::npos,
        "inherit:new-subject-old-obligation");

  ServiceResponse explicit_description =
      service.Query("inherit", "Tell me about Ada Lovelace");
  Check(!provider.requests.empty() &&
            provider.requests.back().relation_family == "describe" &&
            provider.requests.back().general_description,
        "inherit:explicit-description-opens-new-frame");
  Check(explicit_description.disposition == "ABSTAIN",
        "inherit:explicit-description-does-not-use-prior-date");
}

void TestEmptyVersusFailure() {
  FakeProvider empty(FakeProvider::kEmpty);
  ServiceCore empty_service;
  Check(Init(&empty, &empty_service), "empty:init");
  ServiceResponse unsupported =
      empty_service.Query("empty", "When was Ada Lovelace born?");
  Check(unsupported.failure_reason == "VALUE_UNAVAILABLE" &&
            unsupported.support_level ==
                aethercore::service::SupportLevel::kNone,
        "empty:unsupported-none");

  FakeProvider failed(FakeProvider::kFailure);
  ServiceCore failed_service;
  Check(Init(&failed, &failed_service), "failure:init");
  ServiceResponse io =
      failed_service.Query("failure", "When was Ada Lovelace born?");
  Check(io.failure_reason == "RETRIEVAL_ERROR" &&
            io.support_level == aethercore::service::SupportLevel::kNone,
        "failure:distinct-io-error");
}

void TestResume() {
  FakeProvider provider(FakeProvider::kResume);
  ServiceCore service;
  Check(Init(&provider, &service), "resume:init");
  ServiceResponse response =
      service.Query("resume", "When was Ada Lovelace born?");
  Check(response.disposition == "ANSWER", "resume:answer");
  Check(provider.cursors.size() == 2 && !provider.cursors[0].valid &&
            provider.cursors[1].valid &&
            provider.cursors[1].relative_blob_offset == 64,
        "resume:cursor-forwarded");
}

void TestConstraintCoverage() {
  FakeProvider provider(FakeProvider::kOnlyDate);
  ServiceCore service;
  Check(Init(&provider, &service), "constraint:init");
  ServiceResponse response =
      service.Query("constraint", "When was Ada Lovelace born in 1843?");
  Check(!provider.requests.empty() &&
            !provider.requests.back().constraint_terms.empty(),
        "constraint:captured");
  Check(response.disposition == "ABSTAIN" &&
            response.support_level ==
                aethercore::service::SupportLevel::kPartial,
        "constraint:coverage-required");
}

void TestShapeMismatch() {
  FakeProvider provider(FakeProvider::kWrongShape);
  ServiceCore service;
  Check(Init(&provider, &service), "shape:init");
  ServiceResponse response =
      service.Query("shape", "Where was Ada Lovelace born?");
  Check(response.disposition == "ABSTAIN" &&
            response.support_level ==
                aethercore::service::SupportLevel::kPartial,
        "shape:mismatch-cannot-answer");
}

void TestNumericClarification() {
  FakeProvider provider(FakeProvider::kAmbiguous);
  ServiceCore service;
  Check(Init(&provider, &service), "clarify:init");
  ServiceResponse clarify =
      service.Query("clarify", "Tell me about Mercury");
  ServiceResponse answer = service.Query("clarify", "1");
  Check(clarify.disposition == "CLARIFY", "clarify:ambiguous");
  Check(answer.disposition == "ANSWER" &&
            answer.text.find("Mercury (planet)") != std::string::npos,
        "clarify:numeric-selection");
}

}  // namespace

int main() {
  TestGeneralDescription();
  TestWhereIsNotWhen();
  TestBackgroundPartial();
  TestFullBeatsBackground();
  TestFrameInheritance();
  TestEmptyVersusFailure();
  TestResume();
  TestConstraintCoverage();
  TestShapeMismatch();
  TestNumericClarification();
  printf("PROVIDER CONTRACT: %s (%d failures)\n",
         failures == 0 ? "PASS" : "FAIL", failures);
  return failures == 0 ? 0 : 1;
}
