/* AetherCore V15 ESP32-P4 production firmware target (p4_aethercore).
 *
 * Shares the frozen qualification harness (ABI parity vectors, witnessed-case
 * trace replay) with firmware/p4_qualification, and adds the V15 production
 * storage profile: Pack-v2 direct_compact_resident evidence directory in
 * PSRAM + 2 MiB page cache, with A/B replay against the V14 paged path.
 * The interactive service path (protocol v2, interpreter, memory) lands on
 * top of this base; firmware/p4_qualification stays the frozen diagnostic.
 */

#include <cinttypes>
#include <cstdio>
#include <cstring>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esp_chip_info.h"
#include "esp_cpu.h"
#include "esp_flash.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_psram.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "driver/gpio.h"

#include <string.h>

#include "aethercore_runtime.h"
#include "link_tcp.h"
#include "pack_io.h"
#include "parity_vectors_v14.h"
#include "policy_v14_selected.h"
#include "service_runtime.h"
#include "trace_runner.h"

static const char *TAG = "ac_p4";

/* Boot mode: /sdcard/aethercore-state/bootmode.txt holds "qual" or
 * "service" (default). One firmware image serves both the qualification
 * record and the interactive deployment; the card selects the mode, so the
 * flashed binary hash is identical for Phase 15 evidence and Phase 17. */
static void read_boot_mode(char *out, size_t cap) {
  snprintf(out, cap, "service");
  FILE *f = fopen("/sdcard/aethercore-state/bootmode.txt", "rb");
  if (!f) return;
  size_t n = fread(out, 1, cap - 1, f);
  fclose(f);
  out[n] = 0;
  while (n > 0 && (out[n - 1] == '\n' || out[n - 1] == '\r' || out[n - 1] == ' '))
    out[--n] = 0;
  if (strcmp(out, "qual") != 0 && strcmp(out, "service") != 0)
    snprintf(out, cap, "service");
}

/* Interactive service mode: verified pack + Pack-v2 + knowledge + memory
 * store + protocol v2 over the Device-B STA/client link. radio_ok reflects the
 * pre-boot radio bring-up; fail-closed when the link never came up. */
static void run_service_mode(bool radio_ok) {
  ac::runtime::RuntimeInfo info = {};
  info.pack_verified = true;
  info.packv2_active = evd_mode() == EVD_MODE_V2_DIRECT;
  info.pack_id = pack_id();
  info.storage_identity = "kingston-canvas-go-plus-128gb-a2:SD128";
  info.psram_free = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
  info.internal_free = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);

  char err[160];
  if (!ac::runtime::service_init(
          "/sdcard/aethercore-service/knowledge/v13-grounded-records.json",
          "/sdcard/aethercore-state/state.json", kAcV14PolicyWeights,
          AC_V14_POLICY_PARAMETER_COUNT, info, err, sizeof(err))) {
    printf("MEAS {\"phase\":\"service\",\"status\":\"INIT_FAILED\","
           "\"detail\":\"%s\"}\n", err);
    ESP_LOGE(TAG, "service init failed: %s", err);
    return;
  }

  /* Device A keeps Tactility AP/WebServer ownership. The factory C6 on this
   * Device B is already associated; release the persistent TCP client loop. */
  ac::runtime::service_set_response_sink(ac::linktcp::response_sink, nullptr);
  if (radio_ok) ac::linktcp::serve();
  printf("MEAS {\"phase\":\"service\",\"status\":\"%s\",\"link\":\"tcp "
         "%s:%d\",\"packv2\":%s}\n",
         radio_ok ? "READY" : "LINK_FAILED",
#if CONFIG_AC_LINK_PRODUCTION_STA_CLIENT
         CONFIG_AC_LINK_DEVICE_A_IPV4,
#else
         "legacy-device-b-ap-listener",
#endif
         CONFIG_AC_TCP_PORT, info.packv2_active ? "active" : "degraded");
  ESP_LOGI(TAG, "service mode ready (link %s)", radio_ok ? "serving" : "FAILED");
}

/* The linker collects unused function sections, which would understate the
 * runtime's true footprint in the Phase 1 build report. This `used` table
 * retains every ABI entry point (they are all exercised by later phases). */
__attribute__((used)) static void *const kAbiKeep[] = {
    (void *)&ac_abi_version,
    (void *)&ac_workspace_size_v1,
    (void *)&ac_session_size_v1,
    (void *)&ac_session_serialized_size_v1,
    (void *)&ac_cog_summary_size_v1,
    (void *)&ac_5c_constraint_size_v1,
    (void *)&ac_5c_state_size_v1,
    (void *)&ac_specialist_descriptor_size_v1,
    (void *)&ac_progress_size_v1,
    (void *)&ac_cog_runtime_serialized_size_v1,
    (void *)&ac_workspace_init_v1,
    (void *)&ac_session_init_v1,
    (void *)&ac_union_candidates_v1,
    (void *)&ac_legal_action_mask_v1,
    (void *)&ac_policy_select_v1,
    (void *)&ac_policy_validate_i8_v2,
    (void *)&ac_policy_macs_i8_v2,
    (void *)&ac_policy_score_candidate_i8_v2,
    (void *)&ac_policy_select_i8_v2,
    (void *)&ac_5c_digest_v1,
    (void *)&ac_5c_check_v1,
    (void *)&ac_specialist_summarize_v1,
    (void *)&ac_progress_record_v1,
    (void *)&ac_cog_runtime_serialize_v1,
    (void *)&ac_execute_action_v1,
    (void *)&ac_session_serialize_v1,
    (void *)&ac_session_deserialize_v1,
};

static void report_memory(const char *label) {
  ESP_LOGI(TAG, "[%s] internal free=%u largest=%u", label,
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
           (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL));
  ESP_LOGI(TAG, "[%s] psram free=%u largest=%u total=%u", label,
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM),
           (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM),
           (unsigned)esp_psram_get_size());
  ESP_LOGI(TAG, "[%s] main task stack high-water=%u bytes", label,
           (unsigned)(uxTaskGetStackHighWaterMark(nullptr) * sizeof(StackType_t)));
}

/* ---------------------------------------------------------------------------
 * Phase 2: on-device replay of the frozen Python/native reference vectors.
 * Every case below mirrors a host-side ctypes assertion; all inputs and
 * expected outputs come from parity_vectors_v14.h (generated).
 * ------------------------------------------------------------------------- */

static int g_pass = 0;
static int g_fail = 0;

static void check(const char *name, bool ok) {
  if (ok) {
    g_pass += 1;
  } else {
    g_fail += 1;
    ESP_LOGE(TAG, "PARITY FAIL: %s", name);
  }
}

static int choice_cmp(int64_t score_a, int index_a, uint16_t op_a, const char *args_a,
                      int64_t score_b, int index_b, uint16_t op_b, const char *args_b) {
  /* Python max() key: (score, -index, -operation_id, canonical args json). */
  if (score_a != score_b) return score_a > score_b ? 1 : -1;
  if (index_a != index_b) return index_a < index_b ? 1 : -1;
  if (op_a != op_b) return op_a < op_b ? 1 : -1;
  int cmp = strcmp(args_a, args_b);
  return cmp > 0 ? 1 : (cmp < 0 ? -1 : 0);
}

static ac_workspace_v1 s_workspace;
static ac_session_v1 s_session;
static ac_5c_state_v1 s_5c_before;

static void parity_sizes(void) {
  check("abi_version", ac_abi_version() == 1);
  check("workspace_size", ac_workspace_size_v1() == sizeof(ac_workspace_v1) &&
                          ac_workspace_size_v1() == 648);
  check("session_size",
        ac_session_size_v1() == sizeof(ac_session_v1) && sizeof(ac_session_v1) == 872);
  check("session_serialized_size", ac_session_serialized_size_v1() == 836);
  check("cog_size",
        ac_cog_summary_size_v1() == sizeof(ac_cog_summary_v1) && sizeof(ac_cog_summary_v1) == 48);
  check("5c_constraint_size", ac_5c_constraint_size_v1() == sizeof(ac_5c_constraint_v1) &&
                              sizeof(ac_5c_constraint_v1) == 32);
  check("5c_state_size",
        ac_5c_state_size_v1() == sizeof(ac_5c_state_v1) && sizeof(ac_5c_state_v1) == 64);
  check("specialist_descriptor_size",
        ac_specialist_descriptor_size_v1() == sizeof(ac_specialist_descriptor_v1) &&
            sizeof(ac_specialist_descriptor_v1) == 72);
  check("progress_size",
        ac_progress_size_v1() == sizeof(ac_progress_v1) && sizeof(ac_progress_v1) == 48);
  check("cog_wire_size", ac_cog_runtime_serialized_size_v1() == 180);
}

static void parity_union(void) {
  check("union_init", ac_workspace_init_v1(&s_workspace) == AC_OK);
  ac_candidate_v1 existing[sizeof(kUnionExisting) / sizeof(kUnionExisting[0])];
  ac_candidate_v1 incoming[sizeof(kUnionIncoming) / sizeof(kUnionIncoming[0])];
  for (size_t i = 0; i < sizeof(existing) / sizeof(existing[0]); i++) {
    existing[i].entity_id = kUnionExisting[i][0];
    existing[i].score_q15 = (int32_t)kUnionExisting[i][1];
    existing[i].evidence_mask = kUnionExisting[i][2];
  }
  for (size_t i = 0; i < sizeof(incoming) / sizeof(incoming[0]); i++) {
    incoming[i].entity_id = kUnionIncoming[i][0];
    incoming[i].score_q15 = (int32_t)kUnionIncoming[i][1];
    incoming[i].evidence_mask = kUnionIncoming[i][2];
  }
  check("union_existing", ac_union_candidates_v1(&s_workspace, existing,
                                                 sizeof(existing) / sizeof(existing[0])) == AC_OK);
  check("union_incoming", ac_union_candidates_v1(&s_workspace, incoming,
                                                 sizeof(incoming) / sizeof(incoming[0])) == AC_OK);
  const size_t expected_count = sizeof(kUnionExpected) / sizeof(kUnionExpected[0]);
  bool match = s_workspace.candidate_count == expected_count;
  for (size_t i = 0; match && i < expected_count; i++) {
    match = s_workspace.candidates[i].entity_id == kUnionExpected[i][0] &&
            s_workspace.candidates[i].score_q15 == (int32_t)kUnionExpected[i][1] &&
            s_workspace.candidates[i].evidence_mask == kUnionExpected[i][2];
  }
  check("union_candidates", match);
}

static void parity_policy_v1(void) {
  ac_linear_policy_v1 policy = {sizeof(policy), POLICY_V1_FEATURES, POLICY_V1_ACTIONS,
                                kPolicyV1Weights, kPolicyV1Bias};
  uint32_t action = 0;
  int64_t logit = 0;
  check("policy_v1_select_rc",
        ac_policy_select_v1(&policy, kPolicyV1Features, kPolicyV1LegalMask, &action,
                            &logit) == AC_OK);
  check("policy_v1_values",
        action == kPolicyV1ExpectedAction && logit == kPolicyV1ExpectedLogit);
}

static void parity_session(void) {
  check("session_init", ac_session_init_v1(&s_session, kSessionId) == AC_OK);
  s_session.turn_id = kSessionTurnId;
  s_session.active_entity_count = 1;
  s_session.active_entity_ids[0] = 900;
  for (size_t i = 0;
       i < sizeof(kSessionUtteranceHashes) / sizeof(kSessionUtteranceHashes[0]); i++) {
    s_session.recent_utterance_hashes[i] = kSessionUtteranceHashes[i];
  }
  s_session.workspace = s_workspace;
  bool trajectory_ok = true;
  for (size_t i = 0; i < sizeof(kSessionTrajectory) / sizeof(kSessionTrajectory[0]); i++) {
    trajectory_ok &= ac_execute_action_v1(&s_session.workspace, kSessionTrajectory[i][0],
                                          kSessionTrajectory[i][1]) == AC_OK;
  }
  check("session_trajectory", trajectory_ok);
  uint8_t payload[AC_SESSION_SERIALIZED_BYTES];
  size_t written = 0;
  check("session_serialize_rc",
        ac_session_serialize_v1(&s_session, payload, sizeof(payload), &written) == AC_OK &&
            written == sizeof(payload));
  check("session_serialize_bytes",
        memcmp(payload, kSessionExpectedPayload, sizeof(payload)) == 0);
  ac_session_v1 decoded;
  check("session_deserialize_rc",
        ac_session_deserialize_v1(payload, sizeof(payload), &decoded) == AC_OK);
  check("session_terminal", decoded.workspace.terminal_disposition == 1);
}

static void parity_policy_v14_vector(void) {
  ac_int8_policy_v2 policy = {sizeof(policy),
                              POLICY_V14_FEATURES,
                              POLICY_V14_ACTIONS,
                              POLICY_V14_FEATURES * POLICY_V14_ACTIONS,
                              1,
                              14,
                              kPolicyV14Weights,
                              kPolicyV14Bias};
  check("policy_v14_validate", ac_policy_validate_i8_v2(&policy) == AC_OK);
  check("policy_v14_macs",
        ac_policy_macs_i8_v2(&policy) == POLICY_V14_FEATURES * POLICY_V14_ACTIONS);
  uint32_t action = 0;
  int64_t logit = 0;
  check("policy_v14_select_rc",
        ac_policy_select_i8_v2(&policy, kPolicyV14Features, kPolicyV14LegalMask, &action,
                               &logit) == AC_OK);
  check("policy_v14_values",
        action == kPolicyV14ExpectedAction && logit == kPolicyV14ExpectedLogit);

  ac_int8_policy_v2 zero_bias = {sizeof(policy),
                                 POLICY_V14_FEATURES,
                                 POLICY_V14_ACTIONS,
                                 POLICY_V14_FEATURES * POLICY_V14_ACTIONS,
                                 1,
                                 15,
                                 kPolicyV14Weights,
                                 NULL};
  check("policy_v14_zero_bias_validate", ac_policy_validate_i8_v2(&zero_bias) == AC_OK);
  check("policy_v14_zero_bias_select_rc",
        ac_policy_select_i8_v2(&zero_bias, kPolicyV14Features, kPolicyV14LegalMask,
                               &action, &logit) == AC_OK);
  check("policy_v14_zero_bias_values",
        action == kPolicyV14ZeroBiasExpectedAction &&
            logit == kPolicyV14ZeroBiasExpectedLogit);
}

static void parity_selected_policy(void) {
  ac_int8_policy_v2 policy = {sizeof(policy),
                              SELECTED_FEATURES,
                              SELECTED_ACTIONS,
                              SELECTED_FEATURES * SELECTED_ACTIONS,
                              14,
                              0x987D28FC667044BEull,
                              kSelectedWeights,
                              NULL};
  check("selected_validate", ac_policy_validate_i8_v2(&policy) == AC_OK);
  check("selected_macs", ac_policy_macs_i8_v2(&policy) == 1292);

  struct state_view {
    const uint16_t *rows;
    const uint16_t *op_ids;
    const int16_t *const *features;
    const int64_t *scores;
    const char *const *args;
    size_t count;
    uint32_t expected_op;
    const char *expected_args;
  };
  const state_view states[2] = {
      {kSelState0Rows, kSelState0OpIds, kSelState0Features, kSelState0Scores, kSelState0Args,
       sizeof(kSelState0Rows) / sizeof(kSelState0Rows[0]), kSelState0ExpectedChoiceOpId,
       kSelState0ExpectedChoiceArgs},
      {kSelState1Rows, kSelState1OpIds, kSelState1Features, kSelState1Scores, kSelState1Args,
       sizeof(kSelState1Rows) / sizeof(kSelState1Rows[0]), kSelState1ExpectedChoiceOpId,
       kSelState1ExpectedChoiceArgs},
  };
  for (size_t s = 0; s < 2; s++) {
    const state_view &view = states[s];
    bool scores_ok = true;
    int64_t best_score = 0;
    int best_index = -1;
    int64_t computed[24];
    static_assert(sizeof(kSelState0Rows) / sizeof(kSelState0Rows[0]) <= 24, "state0 cap");
    static_assert(sizeof(kSelState1Rows) / sizeof(kSelState1Rows[0]) <= 24, "state1 cap");
    for (size_t i = 0; i < view.count; i++) {
      int64_t score = 0;
      scores_ok &= ac_policy_score_candidate_i8_v2(&policy, view.rows[i],
                                                   view.features[i], &score) == AC_OK;
      scores_ok &= score == view.scores[i];
      computed[i] = score;
    }
    for (size_t i = 0; i < view.count; i++) {
      if (best_index < 0 ||
          choice_cmp(computed[i], (int)i, view.op_ids[i], view.args[i], best_score,
                     best_index, view.op_ids[best_index], view.args[best_index]) > 0) {
        best_score = computed[i];
        best_index = (int)i;
      }
    }
    char name[40];
    snprintf(name, sizeof(name), "selected_state%d_scores", (int)s);
    check(name, scores_ok);
    snprintf(name, sizeof(name), "selected_state%d_choice", (int)s);
    check(name, best_index >= 0 && view.op_ids[best_index] == view.expected_op &&
                strcmp(view.args[best_index], view.expected_args) == 0);
  }
}

static void parity_five_c(void) {
  ac_5c_constraint_v1 constraint = {0x5C01, 3, 0, 0, 0, 1ull << 9, 0, 0, 0, 0};
  uint64_t digest_low = 0;
  uint64_t digest_high = 0;
  check("5c_digest_rc",
        ac_5c_digest_v1(&constraint, 1, &digest_low, &digest_high) == AC_OK);
  check("5c_digest_values",
        digest_low == kFiveCExpectedDigestLow && digest_high == kFiveCExpectedDigestHigh);
  ac_5c_state_v1 state = {sizeof(state), 1, 1, digest_low, digest_high, 7, 0, 0, {0}};
  memcpy(&s_5c_before, &state, sizeof(state));
  ac_5c_request_v1 request = {9, 0, 0, 0};
  uint32_t allowed = 1;
  uint32_t violation = 0;
  check("5c_deny_rc",
        ac_5c_check_v1(&state, &constraint, 1, &request, &allowed, &violation) == AC_OK);
  check("5c_deny_values", allowed == 0 && violation == 0x5C01);
  check("5c_root_untouched", memcmp(&state, &s_5c_before, sizeof(state)) == 0);
  constraint.action_mask = 0;
  check("5c_tamper_detected",
        ac_5c_check_v1(&state, &constraint, 1, &request, &allowed, &violation) ==
            AC_CHECKSUM_MISMATCH);
}

static void parity_specialists(void) {
  ac_specialist_descriptor_v1 descriptors[3] = {
      {72, 2, 0, 1, 99, 1, 2, 10, 128, 0, 5, 1, 1, 1, 1},
      {72, 2, 1, 2, 99, 1, 2, 10, 256, 0, 5, 1, 1, 2, 1},
      {72, 2, 2, 3, 99, 1, 2, 10, 512, 0, 5, 1, 1, 3, 1},
  };
  ac_specialist_summary_v1 summary = {0, 0, 0, 0};
  check("specialists_rc",
        ac_specialist_summarize_v1(descriptors, 3, 1024, &summary) == AC_OK);
  check("specialists_values", summary.cold_count == 1 && summary.warm_count == 1 &&
                              summary.hot_count == 1 && summary.resident_ram_bytes == 768);
}

static void parity_progress(void) {
  ac_progress_v1 progress = {};
  progress.struct_size = sizeof(progress);
  for (int i = 0; i < 4; i++) {
    check("progress_record_rc",
          ac_progress_record_v1(&progress, 7, 0x10203040, 3, 1, 0, 0, 0, 0, 0) == AC_OK);
  }
  check("progress_stagnated", (progress.flags & AC_PROGRESS_STAGNATED) != 0);
  check("progress_bytes",
        memcmp((const char *)&progress + 4, kProgressExpectedPacked,
               sizeof(kProgressExpectedPacked)) == 0);
}

static void parity_cog_wire(void) {
  ac_cog_summary_v1 cog = {};
  cog.struct_size = sizeof(cog);
  cog.schema_version = 1;
  memcpy(&cog.open_goals, kCogValues, sizeof(kCogValues));
  ac_5c_state_v1 five_c = {sizeof(five_c), 1, kFiveCConstraintCount, kFiveCDigestLow,
                           kFiveCDigestHigh, kFiveCFlags, 0, 0, {0}};
  ac_progress_v1 progress = {};
  progress.struct_size = sizeof(progress);
  progress.open_obligations = 4;
  progress.completed_obligations = 7;
  ac_specialist_summary_v1 specialists = {kSpecialistValues[0], kSpecialistValues[1],
                                          kSpecialistValues[2], kSpecialistValues[3]};
  uint8_t output[180];
  size_t written = 0;
  check("cog_serialize_rc",
        ac_cog_runtime_serialize_v1(&cog, &five_c, &progress, &specialists, output,
                                    sizeof(output), &written) == AC_OK &&
            written == sizeof(output));
  check("cog_serialize_bytes", memcmp(output, kCogWireExpected, sizeof(output)) == 0);
}

static void run_parity(void) {
  ESP_LOGI(TAG, "phase 2: replaying frozen parity vectors");
  parity_sizes();
  parity_union();
  parity_policy_v1();
  parity_session();
  parity_policy_v14_vector();
  parity_selected_policy();
  parity_five_c();
  parity_specialists();
  parity_progress();
  parity_cog_wire();
  ESP_LOGI(TAG, "PARITY RESULT: pass=%d fail=%d", g_pass, g_fail);
  printf("PARITY: %s (%d/%d)\n", g_fail == 0 ? "PASS" : "FAIL", g_pass, g_pass + g_fail);
}

extern "C" void app_main(void) {
  esp_chip_info_t chip_info;
  esp_chip_info(&chip_info);
  uint32_t flash_size = 0;
  esp_flash_get_size(nullptr, &flash_size);

  ESP_LOGI(TAG, "aethercore p4 production target (V15 pack-v2 profile)");
  ESP_LOGI(TAG, "idf=%s chip_model=%d cores=%u rev=%u", esp_get_idf_version(),
           (int)chip_info.model, (unsigned)chip_info.cores,
           (unsigned)chip_info.revision);
  ESP_LOGI(TAG, "cpu_hz=%" PRIu32 " flash_bytes=%" PRIu32 " psram_bytes=%u",
           (uint32_t)esp_cpu_get_cycle_count() > 0 ? (uint32_t)CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ * 1000000u : 0u,
           flash_size, (unsigned)esp_psram_get_size());
  ESP_LOGI(TAG, "abi_version=%u workspace=%zu session=%zu session_wire=%zu cog_wire=%zu",
           (unsigned)ac_abi_version(), ac_workspace_size_v1(), ac_session_size_v1(),
           ac_session_serialized_size_v1(), ac_cog_runtime_serialized_size_v1());

  /* Force-load the retention table so section GC keeps the full ABI. */
  uintptr_t abi_sentinel = 0;
  for (size_t i = 0; i < sizeof(kAbiKeep) / sizeof(kAbiKeep[0]); ++i) {
    abi_sentinel ^= (uintptr_t)kAbiKeep[i];
  }
  ESP_LOGI(TAG, "abi entry points retained=%zu sentinel=%08" PRIx32,
           sizeof(kAbiKeep) / sizeof(kAbiKeep[0]), (uint32_t)abi_sentinel);

  report_memory("boot");

  static ac_int8_policy_v2 policy;
  std::memset(&policy, 0, sizeof(policy));
  policy.struct_size = sizeof(policy);
  policy.feature_count = AC_V14_POLICY_FEATURE_COUNT;
  policy.action_count = AC_V14_POLICY_ACTION_COUNT;
  policy.parameter_count = AC_V14_POLICY_PARAMETER_COUNT;
  policy.state_schema_id = AC_V14_POLICY_STATE_SCHEMA_ID;
  policy.model_id = AC_V14_POLICY_MODEL_ID;
  policy.weights = kAcV14PolicyWeights;
  policy.bias = nullptr;

  ac_status_v1 status = ac_policy_validate_i8_v2(&policy);
  ESP_LOGI(TAG, "policy validate=%d params=%" PRIu32 " macs/decision=%" PRIu32,
           (int)status, policy.parameter_count, ac_policy_macs_i8_v2(&policy));

  /* Smoke selection: bias-feature-only vector (feature0 = 256), all actions legal. */
  int16_t features[AC_V14_POLICY_FEATURE_COUNT];
  std::memset(features, 0, sizeof(features));
  features[0] = 256;
  uint32_t selected = 0;
  int64_t logit = 0;
  uint64_t legal_mask = (AC_V14_POLICY_ACTION_COUNT >= 64u)
                            ? UINT64_MAX
                            : ((1ull << AC_V14_POLICY_ACTION_COUNT) - 1ull);
  status = ac_policy_select_i8_v2(&policy, features, legal_mask, &selected, &logit);
  ESP_LOGI(TAG, "smoke select status=%d action=%" PRIu32 " logit=%lld", (int)status,
           selected, (long long)logit);

  report_memory("post-policy");

  run_parity();

  /* Radio bring-up MUST precede the SD mount (pack boot): the C6 link is
   * SDIO slot 1 on the shared SDMMC host and slot-1 card init fails once
   * slot 0 is mounted (hardware-verified; Tactility uses the same order).
   * Blocks until the C6 link is associated to Device A and has a DHCP address.
   * The TCP client starts later, in serve(), after pack boot (or immediately
   * in the explicit transport-only diagnostic build). */
#if CONFIG_AC_LINK_PRODUCTION_STA_CLIENT
  ac::linktcp::Config lcfg = {
      CONFIG_AC_LINK_DEVICE_A_SSID,
      CONFIG_AC_LINK_DEVICE_A_PASS,
      CONFIG_AC_LINK_DEVICE_A_IPV4,
      0,
      CONFIG_AC_TCP_PORT,
      CONFIG_AC_LINK_RECONNECT_DELAY_MS,
      CONFIG_AC_LINK_STA_CONNECT_TIMEOUT_MS,
      false,
#if CONFIG_AC_LINK_DIAGNOSTIC_ONLY
      CONFIG_AC_LINK_DIAGNOSTIC_ONLY,
#else
      false,
#endif
  };
#else
  ac::linktcp::Config lcfg = {
      CONFIG_AC_TCP_AP_SSID,
      CONFIG_AC_TCP_AP_PASS,
      nullptr,
      CONFIG_AC_TCP_AP_CHANNEL,
      CONFIG_AC_TCP_PORT,
      2000,
      30000,
#if CONFIG_AC_TCP_LOOPBACK_SELFTEST
      CONFIG_AC_TCP_LOOPBACK_SELFTEST,
#else
      false,
#endif
      false,
  };
#endif
#if CONFIG_AC_LINK_DIAGNOSTIC_ONLY
  bool radio_ok = ac::linktcp::radio_up(lcfg);
  printf("LINK_DIAGNOSTIC_ONLY -- NOT AETHERCORE QUALIFICATION\n");
  printf("MEAS {\"phase\":\"link_diagnostic\",\"qualification\":false,"
         "\"pack_mounted\":false,\"cognition_started\":false}\n");
  if (radio_ok) ac::linktcp::serve();
#else
  /* Shared verified boot (mount + pack verify + Pack-v2), then the
   * card-selected mode: "qual" (cache ladder + A/B replay evidence) or
   * "service" (interactive protocol v2 runtime).
   *
   * Boot order is pack FIRST, radio SECOND: slot-0 SD mount/verify traffic
   * wedges the hosted C6 SDIO link (slot 1) when the radio is already
   * active (measured: every radio-first boot lost the C6 data path in the
   * mount window; the 802.11 association survived but TCP SYNs died into
   * errno 113 and the sdio error storm). Bringing hosted up after the pack
   * is resident keeps the shared host quiet during hosted init. */
  bool boot_ok = run_pack_boot();
  ESP_LOGI(TAG, "pack boot %s", boot_ok ? "complete" : "FAILED (see MEAS lines)");
  /* The SD slot-0 mount browns out the C6 co-processor on the shared rail;
   * the crashed slave never reboots itself and every later hosted RPC dies
   * (errno 113 + sdio storm). The hosted driver's boot-time slave reset ran
   * BEFORE the mount, so pulse the slave-reset line (GPIO54, per
   * ESP_HOSTED_SDIO_RESET_SLAVE_GPIO range) again now that the rail is
   * quiet, then let hosted init see a fresh ROM-booted C6. */
  gpio_config_t rst_cfg = {};
  rst_cfg.pin_bit_mask = 1ULL << 54;
  rst_cfg.mode = GPIO_MODE_OUTPUT;
  gpio_config(&rst_cfg);
  gpio_set_level((gpio_num_t)54, 0);
  vTaskDelay(pdMS_TO_TICKS(300));
  gpio_set_level((gpio_num_t)54, 1);
  vTaskDelay(pdMS_TO_TICKS(3000));
  printf("MEAS {\"phase\":\"link\",\"event\":\"slave_reset_pulsed\",\"gpio\":54}\n");
  bool radio_ok = ac::linktcp::radio_up(lcfg);
  if (boot_ok) {
    char mode[16];
    read_boot_mode(mode, sizeof(mode));
    printf("MEAS {\"phase\":\"boot\",\"mode\":\"%s\"}\n", mode);
    if (strcmp(mode, "qual") == 0) {
      bool qual_ok = run_qual_phases();
      ESP_LOGI(TAG, "qual phases %s", qual_ok ? "complete" : "incomplete");
    } else {
      run_service_mode(radio_ok);
    }
  }
#endif

  for (;;) {
    vTaskDelay(pdMS_TO_TICKS(60000));
  }
}
