#include "trace_runner.h"

#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_random.h>
#include <esp_timer.h>
#include <fcntl.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "aethercore_runtime.h"
#include "pack_io.h"
#include "pack_v2.h"
#include "policy_v14_selected.h"

static const char *TAG = "ac_trace";

#define TRACE_MAGIC "ACP1TRC1"
#define TRACE_PATH "/sdcard/aethercore-traces/phase6-trace.bin"
#define CANDIDATE_RECORD_BYTES 94
#define DECISION_RECORD_BYTES 24
#define CASE_RECORD_BYTES 40
#define QUERY_RECORD_BYTES 36
#define DECISION_CHUNK 128

/* ------------------------------------------------------------------ */
/* measurement helpers                                                 */

static void memory_snapshot(const char *label) {
  printf("MEAS {\"phase\":\"memory\",\"label\":\"%s\",\"internal_free\":%u,"
         "\"internal_largest\":%u,\"psram_free\":%u,\"psram_largest\":%u,"
         "\"stack_high_water\":%u}\n",
         label, (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
         (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
         (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM),
         (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM),
         (unsigned)uxTaskGetStackHighWaterMark(NULL));
}

static int cmp_u32(const void *a, const void *b) {
  uint32_t va = *(const uint32_t *)a, vb = *(const uint32_t *)b;
  return va < vb ? -1 : va > vb ? 1 : 0;
}

static void emit_latency_stats(const char *phase, const char *name, uint32_t *samples,
                               size_t count) {
  if (count == 0) return;
  qsort(samples, count, 4, cmp_u32);
  uint64_t sum = 0;
  for (size_t i = 0; i < count; i++) sum += samples[i];
  printf("MEAS {\"phase\":\"%s\",\"metric\":\"%s\",\"samples\":%u,\"mean_us\":%.1f,"
         "\"p50_us\":%u,\"p95_us\":%u,\"p99_us\":%u,\"min_us\":%u,\"max_us\":%u}\n",
         phase, name, (unsigned)count, (double)sum / (double)count,
         (unsigned)samples[count / 2], (unsigned)samples[(count * 95) / 100],
         (unsigned)samples[(count * 99) / 100], (unsigned)samples[0],
         (unsigned)samples[count - 1]);
}

/* ------------------------------------------------------------------ */
/* trace bundle access (direct reads; not part of the pack workload)   */

typedef struct {
  int fd;
  uint32_t case_count, decision_count, candidate_count, query_count;
  uint64_t pool_off, pool_len, cases_off, decisions_off, candidates_off, queries_off;
  char **strings;
  uint32_t string_count;
} TraceFile;

static TraceFile s_trace;

static bool trace_read(uint64_t offset, void *buffer, size_t length) {
  size_t done = 0;
  while (done < length) {
    ssize_t got = pread(s_trace.fd, (char *)buffer + done, length - done,
                        (off_t)(offset + done));
    if (got <= 0) return false;
    done += (size_t)got;
  }
  return true;
}

static bool trace_open(void) {
  TraceFile *trace = &s_trace;
  memset(trace, 0, sizeof(*trace));
  trace->fd = open(TRACE_PATH, O_RDONLY);
  if (trace->fd < 0) {
    ESP_LOGE(TAG, "trace file missing: %s", TRACE_PATH);
    return false;
  }
  uint8_t header[PACK_PAGE];
  if (!trace_read(0, header, PACK_PAGE)) return false;
  if (memcmp(header, TRACE_MAGIC, 8) != 0) return false;
  uint32_t version, page;
  memcpy(&version, header + 8, 4);
  memcpy(&page, header + 12, 4);
  if (version != 1 || page != PACK_PAGE) return false;
  memcpy(&trace->case_count, header + 16, 4);
  memcpy(&trace->decision_count, header + 20, 4);
  memcpy(&trace->candidate_count, header + 24, 4);
  memcpy(&trace->query_count, header + 28, 4);
  memcpy(&trace->pool_off, header + 32, 8);
  memcpy(&trace->pool_len, header + 40, 8);
  memcpy(&trace->cases_off, header + 48, 8);
  memcpy(&trace->decisions_off, header + 64, 8);
  memcpy(&trace->candidates_off, header + 80, 8);
  memcpy(&trace->queries_off, header + 96, 8);

  uint8_t *pool = (uint8_t *)heap_caps_malloc(trace->pool_len, MALLOC_CAP_SPIRAM);
  if (!pool) return false;
  if (!trace_read(trace->pool_off, pool, trace->pool_len)) {
    free(pool);
    return false;
  }
  uint32_t count = 0;
  memcpy(&count, pool, 4);
  trace->string_count = count;
  trace->strings = (char **)heap_caps_calloc(count, sizeof(char *), MALLOC_CAP_INTERNAL);
  char *copy = (char *)heap_caps_malloc(trace->pool_len + count, MALLOC_CAP_SPIRAM);
  if (!trace->strings || !copy) {
    free(pool);
    return false;
  }
  size_t cursor = 4, out = 0;
  for (uint32_t i = 0; i < count; i++) {
    uint16_t len = 0;
    memcpy(&len, pool + cursor, 2);
    cursor += 2;
    memcpy(copy + out, pool + cursor, len);
    copy[out + len] = 0;
    trace->strings[i] = copy + out;
    cursor += len;
    out += len + 1;
  }
  free(pool);
  ESP_LOGI(TAG, "trace: %u cases, %u decisions, %u queries, %u strings",
           trace->case_count, trace->decision_count, trace->query_count,
           trace->string_count);
  return true;
}

typedef struct {
  uint32_t case_id_str;
  uint8_t tier, partition, success, failure;
  uint32_t decision_start, decision_count, query_start, query_count, operations;
} CaseRecord;

typedef struct {
  uint32_t case_idx;
  uint16_t step;
  uint32_t cand_start, cand_count, chosen_op, chosen_args;
} DecisionRecord;

typedef struct {
  uint32_t surface_str, cand_count;
  uint64_t cand_digest;
  uint32_t entity_count;
  uint64_t entity_digest;
  uint32_t occurrences;
} QueryRecord;

static bool trace_case(uint32_t index, CaseRecord *out) {
  uint8_t raw[CASE_RECORD_BYTES];
  if (!trace_read(s_trace.cases_off + (uint64_t)index * CASE_RECORD_BYTES, raw,
                  sizeof(raw)))
    return false;
  memcpy(&out->case_id_str, raw, 4);
  out->tier = raw[4];
  out->partition = raw[5];
  out->success = raw[6];
  out->failure = raw[7];
  memcpy(&out->decision_start, raw + 8, 4);
  memcpy(&out->decision_count, raw + 12, 4);
  memcpy(&out->query_start, raw + 16, 4);
  memcpy(&out->query_count, raw + 20, 4);
  memcpy(&out->operations, raw + 24, 4);
  return true;
}

static bool trace_decision(uint32_t index, DecisionRecord *out) {
  uint8_t raw[DECISION_RECORD_BYTES];
  if (!trace_read(s_trace.decisions_off + (uint64_t)index * DECISION_RECORD_BYTES,
                  raw, sizeof(raw)))
    return false;
  memcpy(&out->case_idx, raw, 4);
  memcpy(&out->step, raw + 4, 2);
  memcpy(&out->cand_start, raw + 6, 4);
  memcpy(&out->cand_count, raw + 10, 4);
  memcpy(&out->chosen_op, raw + 14, 4);
  memcpy(&out->chosen_args, raw + 18, 4);
  return true;
}

static bool trace_query(uint32_t index, QueryRecord *out) {
  uint8_t raw[QUERY_RECORD_BYTES];
  if (!trace_read(s_trace.queries_off + (uint64_t)index * QUERY_RECORD_BYTES, raw,
                  sizeof(raw)))
    return false;
  memcpy(&out->surface_str, raw, 4);
  memcpy(&out->cand_count, raw + 4, 4);
  memcpy(&out->cand_digest, raw + 8, 8);
  memcpy(&out->entity_count, raw + 16, 4);
  memcpy(&out->entity_digest, raw + 20, 8);
  memcpy(&out->occurrences, raw + 28, 4);
  return true;
}

/* ------------------------------------------------------------------ */
/* phase 5: addressing workload over the cache ladder                  */

static bool phase5_cache_ladder(void) {
  const uint32_t cache_sizes[] = {0u, 256u * 1024u, 1u << 20, 2u << 20};
  const char *cache_names[] = {"zero", "256KiB", "1MiB", "2MiB"};
  uint32_t n_queries = s_trace.query_count;
  uint32_t *lat = (uint32_t *)heap_caps_malloc(n_queries * 4, MALLOC_CAP_INTERNAL);

  for (int s = 0; s < 4; s++) {
    Pager *pager = pager_create(cache_sizes[s]);
    if (!pager || !lat) return false;
    for (int pass = 0; pass < 2; pass++) {
      bool ok = true;
      for (uint32_t q = 0; q < n_queries && ok; q++) {
        QueryRecord query;
        ok = trace_query(q, &query);
        if (!ok) break;
        AddressResult result;
        int64_t start = esp_timer_get_time();
        ok = idx_query_address(pager, s_trace.strings[query.surface_str], &result);
        lat[q] = (uint32_t)(esp_timer_get_time() - start);
        if (!ok) {
          ESP_LOGE(TAG, "ladder %s/%s failed at query %u ('%s')",
                   cache_names[s], pass == 0 ? "cold" : "warm", (unsigned)q,
                   s_trace.strings[query.surface_str]);
        }
        if ((q & 0x1F) == 0x1F) vTaskDelay(1);
      }
      if (!ok) {
        pager_destroy(pager);
        return false;
      }
      PagerStats stats;
      pager_stats(pager, &stats);
      char name[48];
      snprintf(name, sizeof(name), "address_query_%s_%s", cache_names[s],
               pass == 0 ? "cold" : "warm");
      emit_latency_stats("addressing.device", name, lat, n_queries);
      printf("MEAS {\"phase\":\"addressing.device\",\"cache\":\"%s\",\"pass\":\"%s\","
             "\"queries\":%u,\"cache_hits\":%u,\"cache_misses\":%u,"
             "\"hit_rate\":%.4f,\"bytes_read\":%llu,\"pages_read\":%llu,"
             "\"class_postings_misses\":%llu,\"class_surface_misses\":%llu,"
             "\"class_evidence_misses\":%llu,\"random_reads\":%llu,"
             "\"sequential_reads\":%llu,\"reads_avoided\":%llu}\n",
             cache_names[s], pass == 0 ? "cold" : "warm", (unsigned)n_queries,
             (unsigned)stats.cache_hits, (unsigned)stats.cache_misses,
             (double)stats.cache_hits / (double)(stats.cache_hits + stats.cache_misses),
             (unsigned long long)stats.physical_bytes,
             (unsigned long long)stats.physical_reads,
             (unsigned long long)stats.class_misses[CLASS_POSTINGS],
             (unsigned long long)stats.class_misses[CLASS_SURFACE],
             (unsigned long long)stats.class_misses[CLASS_EVIDENCE],
             (unsigned long long)stats.random_reads,
             (unsigned long long)stats.sequential_reads,
             (unsigned long long)stats.cache_hits);
      if (pass == 0) {
        char snap[48];
        snprintf(snap, sizeof(snap), "ladder-%s-cold", cache_names[s]);
        memory_snapshot(snap);
      }
    }
    pager_destroy(pager);
    vTaskDelay(1);
  }
  free(lat);
  return true;
}

/* ------------------------------------------------------------------ */
/* phases 6-8: full trace replay (policy + addressing + residency)     */

static ac_int8_policy_v2 make_policy(void) {
  ac_int8_policy_v2 policy = {sizeof(policy),
                              AC_V14_POLICY_FEATURE_COUNT,
                              AC_V14_POLICY_ACTION_COUNT,
                              AC_V14_POLICY_PARAMETER_COUNT,
                              AC_V14_POLICY_STATE_SCHEMA_ID,
                              AC_V14_POLICY_MODEL_ID,
                              kAcV14PolicyWeights,
                              NULL};
  return policy;
}

/* Candidate record (94 B): row u16, expected score i64, args str u32,
 * op id u32, 38 x i16 features. */
typedef struct {
  uint16_t row;
  int64_t expected_score;
  uint32_t args_str;
  uint32_t op_id;
  const int16_t *features;
} CandidateView;

static CandidateView candidate_view(const uint8_t *record) {
  CandidateView view;
  memcpy(&view.row, record, 2);
  memcpy(&view.expected_score, record + 2, 8);
  memcpy(&view.args_str, record + 10, 4);
  memcpy(&view.op_id, record + 14, 4);
  view.features = (const int16_t *)(record + 18);
  return view;
}

/* Python max() key: (score, -index, -operation_id, canonical args json). */
static bool choice_wins(int64_t score_a, uint32_t index_a, uint32_t op_a,
                        const char *args_a, int64_t score_b, uint32_t index_b,
                        uint32_t op_b, const char *args_b, bool have_b) {
  if (!have_b) return true;
  if (score_a != score_b) return score_a > score_b;
  if (index_a != index_b) return index_a < index_b;
  if (op_a != op_b) return op_a < op_b;
  return strcmp(args_a, args_b) > 0;
}

static bool phase6_trace(int evd_mode, size_t cache_bytes, const char *label) {
  ac_int8_policy_v2 policy = make_policy();
  evd_set_mode(evd_mode);
  packv2_stats_reset();
  evd_dir_sd_reads_reset();
  Pager *pager = pager_create(cache_bytes);
  uint8_t *cand_buf = (uint8_t *)heap_caps_malloc(DECISION_CHUNK * CANDIDATE_RECORD_BYTES,
                                                  MALLOC_CAP_INTERNAL);
  uint32_t *policy_lat = (uint32_t *)heap_caps_malloc(s_trace.decision_count * 4,
                                                      MALLOC_CAP_INTERNAL);
  uint32_t *query_lat = (uint32_t *)heap_caps_malloc(s_trace.query_count * 4,
                                                     MALLOC_CAP_INTERNAL);
  if (!pager || !cand_buf || !policy_lat || !query_lat) return false;

  uint32_t decisions_matched = 0, decisions_total = 0;
  uint32_t queries_matched = 0, queries_total = 0;
  uint32_t cases_ok = 0;
  uint64_t policy_macs = 0;
  uint32_t policy_lat_n = 0, query_lat_n = 0;
  int64_t replay_start = esp_timer_get_time();

  for (uint32_t ci = 0; ci < s_trace.case_count; ci++) {
    CaseRecord record;
    if (!trace_case(ci, &record)) return false;
    uint32_t case_dec_match = 0, case_q_match = 0;
    uint32_t case_cpu_us = 0, case_query_us = 0;

    /* policy decisions: score every recorded candidate exactly as the
     * controller did (features as exported, selected policy, no bias) */
    for (uint32_t d = 0; d < record.decision_count; d++) {
      DecisionRecord decision;
      if (!trace_decision(record.decision_start + d, &decision)) return false;
      int64_t best_score = 0;
      uint32_t best_index = 0, best_op = 0, best_args = 0;
      bool have_best = false;
      bool scores_ok = true;
      uint32_t remaining = decision.cand_count;
      uint64_t offset = s_trace.candidates_off +
                        (uint64_t)decision.cand_start * CANDIDATE_RECORD_BYTES;
      int64_t cpu_start = esp_timer_get_time();
      uint32_t global_idx = 0;
      while (remaining > 0) {
        uint32_t chunk = remaining > DECISION_CHUNK ? DECISION_CHUNK : remaining;
        if (!trace_read(offset, cand_buf, chunk * CANDIDATE_RECORD_BYTES))
          return false;
        offset += (uint64_t)chunk * CANDIDATE_RECORD_BYTES;
        remaining -= chunk;
        for (uint32_t k = 0; k < chunk; k++, global_idx++) {
          CandidateView candidate = candidate_view(cand_buf +
                                                   k * CANDIDATE_RECORD_BYTES);
          int64_t score = 0;
          if (ac_policy_score_candidate_i8_v2(&policy, candidate.row,
                                              candidate.features,
                                              &score) != AC_OK)
            return false;
          policy_macs += AC_V14_POLICY_PARAMETER_COUNT / AC_V14_POLICY_ACTION_COUNT;
          scores_ok &= score == candidate.expected_score;
          if (choice_wins(score, global_idx, candidate.op_id,
                          s_trace.strings[candidate.args_str], best_score,
                          best_index, best_op, s_trace.strings[best_args],
                          have_best)) {
            have_best = true;
            best_score = score;
            best_index = global_idx;
            best_op = candidate.op_id;
            best_args = candidate.args_str;
          }
        }
      }
      uint32_t cpu_us = (uint32_t)(esp_timer_get_time() - cpu_start);
      case_cpu_us += cpu_us;
      policy_lat[policy_lat_n++] = cpu_us;
      decisions_total++;
      if (have_best && scores_ok && best_op == decision.chosen_op &&
          best_args == decision.chosen_args) {
        decisions_matched++;
        case_dec_match++;
      }
    }

    /* address queries through the paged pack */
    for (uint32_t q = 0; q < record.query_count; q++) {
      QueryRecord query;
      if (!trace_query(record.query_start + q, &query)) return false;
      AddressResult result;
      int64_t start = esp_timer_get_time();
      if (!idx_query_address(pager, s_trace.strings[query.surface_str], &result))
        return false;
      uint32_t wall_us = (uint32_t)(esp_timer_get_time() - start);
      query_lat[query_lat_n++] = wall_us;
      case_query_us += wall_us;
      queries_total++;
      bool match = result.cand_digest == query.cand_digest &&
                   result.cand_count == query.cand_count &&
                   result.entity_digest == query.entity_digest &&
                   result.entity_count == query.entity_count &&
                   result.occurrence_total == query.occurrences;
      if (match) {
        queries_matched++;
        case_q_match++;
      }
      /* physical evidence probe: first entity's blob head */
      if (result.has_entity) {
        uint8_t head[256];
        size_t got = 0;
        evd_blob_head(pager, result.first_entity_idx, head, sizeof(head), &got);
      }
    }

    bool case_ok = case_dec_match == record.decision_count &&
                   case_q_match == record.query_count;
    if (case_ok) cases_ok++;
    printf("MEAS {\"phase\":\"case\",\"idx\":%u,\"tier\":%u,\"partition\":%u,"
           "\"policy_matched\":%u,\"policy_total\":%u,\"address_matched\":%u,"
           "\"address_total\":%u,\"ops\":%u,\"policy_cpu_us\":%u,"
           "\"query_wall_us\":%u,\"ok\":%s}\n",
           (unsigned)ci, (unsigned)record.tier, (unsigned)record.partition,
           (unsigned)case_dec_match, (unsigned)record.decision_count,
           (unsigned)case_q_match, (unsigned)record.query_count,
           (unsigned)record.operations, (unsigned)case_cpu_us,
           (unsigned)case_query_us, case_ok ? "true" : "false");
    vTaskDelay(1);
  }

  double replay_seconds = (double)(esp_timer_get_time() - replay_start) / 1e6;
  emit_latency_stats("policy.device", "decision_cpu", policy_lat, policy_lat_n);
  char lat_name[48];
  snprintf(lat_name, sizeof(lat_name), "query_wall_%s", label);
  emit_latency_stats("addressing.device", lat_name, query_lat, query_lat_n);
  PagerStats stats;
  pager_stats(pager, &stats);
  PackV2Stats v2;
  packv2_stats(&v2);
  printf("MEAS {\"phase\":\"replay.device\",\"profile\":\"%s\","
         "\"evd_mode\":%d,\"cache_bytes\":%u,\"seconds\":%.3f,\"cases_ok\":%u,"
         "\"cases_total\":%u,\"decisions_matched\":%u,\"decisions_total\":%u,"
         "\"queries_matched\":%u,\"queries_total\":%u,\"policy_macs\":%llu,"
         "\"cache_hits\":%llu,\"cache_misses\":%llu,\"bytes_read\":%llu,"
         "\"pages_read\":%llu,\"random_reads\":%llu,\"sequential_reads\":%llu,"
         "\"class_postings_misses\":%llu,\"class_surface_misses\":%llu,"
         "\"class_evidence_misses\":%llu,"
         "\"evd_dir_sd_reads\":%llu,\"io_ops\":%llu,"
         "\"readahead_pages\":%llu,\"read_time_s\":%.3f,"
         "\"packv2_lookups\":%llu,"
         "\"packv2_misses\":%llu,\"packv2_cpu_us\":%llu}\n",
         label, evd_mode, (unsigned)pager_capacity(pager),
         replay_seconds, (unsigned)cases_ok, (unsigned)s_trace.case_count,
         (unsigned)decisions_matched, (unsigned)decisions_total,
         (unsigned)queries_matched, (unsigned)queries_total,
         (unsigned long long)policy_macs, (unsigned long long)stats.cache_hits,
         (unsigned long long)stats.cache_misses,
         (unsigned long long)stats.physical_bytes,
         (unsigned long long)stats.physical_reads,
         (unsigned long long)stats.random_reads,
         (unsigned long long)stats.sequential_reads,
         (unsigned long long)stats.class_misses[CLASS_POSTINGS],
         (unsigned long long)stats.class_misses[CLASS_SURFACE],
         (unsigned long long)stats.class_misses[CLASS_EVIDENCE],
         (unsigned long long)evd_dir_sd_reads(),
         (unsigned long long)stats.io_ops,
         (unsigned long long)stats.readahead_pages,
         (double)stats.read_time_us / 1e6,
         (unsigned long long)v2.lookups, (unsigned long long)v2.misses,
         (unsigned long long)v2.cpu_us);
  bool logical_pass = decisions_matched == decisions_total &&
                      queries_matched == queries_total;
  /* The zero-directory-reads proof uses the pager-delta counter: packv2_find
   * structurally cannot touch SD, and evd_dir_sd_reads() measures any actual
   * physical read attributed to a directory lookup in either mode. */
  bool zero_dir_reads = evd_mode != EVD_MODE_V2_DIRECT || evd_dir_sd_reads() == 0;
  printf("MEAS {\"phase\":\"replay.device\",\"profile\":\"%s\","
         "\"verdict\":\"%s\",\"evd_dir_sd_reads_ok\":%s}\n",
         label, logical_pass ? "LOGICAL_PASS" : "LOGICAL_MISMATCH",
         zero_dir_reads ? "true" : "false");
  free(policy_lat);
  free(query_lat);
  free(cand_buf);
  pager_destroy(pager);
  return true;
}

/* ------------------------------------------------------------------ */

/* Shared boot: mount, open, verify, Pack-v2 load. Both the qualification
 * harness and the interactive service boot through exactly this path. */
bool run_pack_boot(void) {
  ESP_LOGI(TAG, "pack boot starting");
  if (!sd_mount()) {
    printf("MEAS {\"phase\":\"storage\",\"status\":\"NO_CARD\"}\n");
    return false;
  }
  sd_card_report();
  if (!pack_open("/sdcard")) {
    printf("MEAS {\"phase\":\"pack\",\"status\":\"OPEN_FAILED\"}\n");
    return false;
  }
  memory_snapshot("post-mount");
#if CONFIG_AC_BOOT_VERIFY_SKIP
  /* Interactive acceptance cycles only: boot re-verify already qualified on
   * this medium. Loud marker; production builds keep this OFF. */
  printf("MEAS {\"phase\":\"pack.verify\",\"result\":\"SKIPPED_INTERACTIVE_ACCEPTANCE\"}\n");
  bool verified = true;
#else
  bool verified = pack_verify_regions();
#endif
  printf("MEAS {\"phase\":\"pack\",\"pack_id\":\"%s\",\"verified\":%s}\n",
         pack_id(), verified ? "true" : "false");
  if (!verified) return false;
  if (!idx_open() || !ent_open() || !evd_open()) return false;
  printf("MEAS {\"phase\":\"pack\",\"surfaces\":%u,\"grams\":%u,\"entities\":%u}\n",
         (unsigned)idx_surface_count(), (unsigned)idx_gram_count(),
         (unsigned)ent_count());
  memory_snapshot("post-gram-dir");

  /* Phase 6 (mission): Pack-v2 direct resident directory. Verification
   * failure is loud and fails closed into the marked V14 degraded path. */
  int active_mode = EVD_MODE_V14_PAGED;
  bool v2_loaded = packv2_load(pack_root_path(), pack_id(),
                               pack_region_evidence()->fd,
                               evd_directory_offset(), evd_directory_length());
  if (v2_loaded) {
    active_mode = EVD_MODE_V2_DIRECT;
  } else {
    active_mode = EVD_MODE_DEGRADED_V14;
    printf("MEAS {\"phase\":\"packv2\",\"status\":\"DEGRADED_V14_LOOKUP\"}\n");
  }
  evd_set_mode(active_mode);
  printf("MEAS {\"phase\":\"packv2\",\"active_layout\":\"%s\",\"mode\":%d,"
         "\"resident_bytes\":%u}\n",
         packv2_layout(), active_mode, (unsigned)packv2_resident_bytes());
  memory_snapshot("post-packv2");
  return true;
}

/* Qualification phases (cache ladder + A/B replay). Requires run_pack_boot(). */
bool run_qual_phases(void) {
  /* The A2 medium was characterized separately (Phase 3, p4_media_bench);
   * the destructive-style storage bench is not part of this target. */

  if (!trace_open()) {
    printf("MEAS {\"phase\":\"trace\",\"status\":\"MISSING\"}\n");
    return false;
  }
  memory_snapshot("post-trace-load");

  if (!phase5_cache_ladder()) return false;
  memory_snapshot("post-phase5");

  /* A/B replay: V14 paged reference (1 MiB) vs selected V15 profile
   * (direct resident directory, 2 MiB cache). Both must be logically exact;
   * the comparison isolates the physical placement change. */
  if (!phase6_trace(EVD_MODE_V14_PAGED, 1u << 20, "v14_paged_1MiB"))
    return false;
  memory_snapshot("post-replay-v14");
  /* The selected profile is whatever run_pack_boot() activated (V2_DIRECT
   * when Pack-v2 verified, else the loud degraded V14 mode). */
  if (!phase6_trace(evd_mode(), 2u << 20, "v15_direct_2MiB"))
    return false;
  memory_snapshot("final");
  return true;
}

/* Legacy composite (parity with earlier harness invocations). */
bool run_sd_phases(void) {
  return run_pack_boot() && run_qual_phases();
}
