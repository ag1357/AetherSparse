/* AetherCore V14 ESP32-P4 physical qualification firmware.
 *
 * Phase 1 scope: real ESP-IDF build + on-device identity/boot report.
 * Binds the frozen selected int8 policy (1,292 parameters) through the stable
 * C ABI and reports physical memory/clock figures. Frozen-vector parity
 * replay and paged-storage queries arrive in later phases on the SD medium.
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

#include "aethercore_runtime.h"
#include "policy_v14_selected.h"

static const char *TAG = "ac_p4_qual";

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

extern "C" void app_main(void) {
  esp_chip_info_t chip_info;
  esp_chip_info(&chip_info);
  uint32_t flash_size = 0;
  esp_flash_get_size(nullptr, &flash_size);

  ESP_LOGI(TAG, "aethercore-v14 p4 physical qualification (phase 1 build)");
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

  for (;;) {
    vTaskDelay(pdMS_TO_TICKS(60000));
  }
}
