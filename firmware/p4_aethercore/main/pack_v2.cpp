#include "pack_v2.h"

#include <cJSON.h>
#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <fcntl.h>
#include <inttypes.h>
#include <mbedtls/sha256.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static const char *TAG = "ac_packv2";

#define PACKV2_SCHEMA "aethersparse.deployment-pack-v2.v1"
#define PACKV2_LAYOUT "direct_compact_resident"
#define PACKV2_RECORD_BYTES 12
#define PACKV2_MISSING 0xFFFFFFFFu
#define PACKV2_IMAGE_NAME "derived/evidence-directory-v2.bin"

static uint8_t *s_table; /* PSRAM-resident direct table */
static uint32_t s_capacity;
static size_t s_bytes;
static bool s_active;
static PackV2Stats s_stats;

static void hex_digest(const uint8_t digest[32], char out[65]) {
  for (int i = 0; i < 32; i++) snprintf(out + i * 2, 3, "%02x", digest[i]);
}

static bool build_path(char *out, size_t out_size, const char *root,
                       const char *leaf) {
  if (strlen(root) + 1 + strlen(leaf) + 1 > out_size) return false;
  strlcpy(out, root, out_size);
  strlcat(out, "/", out_size);
  strlcat(out, leaf, out_size);
  return true;
}

static char *read_text_file(const char *path) {
  FILE *file = fopen(path, "rb");
  if (!file) return nullptr;
  fseek(file, 0, SEEK_END);
  long length = ftell(file);
  fseek(file, 0, SEEK_SET);
  char *text = (char *)heap_caps_malloc((size_t)length + 1, MALLOC_CAP_INTERNAL);
  if (!text) {
    fclose(file);
    return nullptr;
  }
  if (fread(text, 1, (size_t)length, file) != (size_t)length) {
    fclose(file);
    free(text);
    return nullptr;
  }
  fclose(file);
  text[length] = 0;
  return text;
}

/* sha256 over an arbitrary span of an already-open region file. */
static bool sha256_span(int fd, uint64_t offset, uint64_t length, char out[65]) {
  mbedtls_sha256_context ctx;
  mbedtls_sha256_init(&ctx);
  mbedtls_sha256_starts(&ctx, 0);
  uint8_t block[4096];
  uint64_t done = 0;
  while (done < length) {
    size_t want = (length - done) < sizeof(block) ? (size_t)(length - done)
                                                  : sizeof(block);
    ssize_t got = pread(fd, block, want, (off_t)(offset + done));
    if (got <= 0) {
      mbedtls_sha256_free(&ctx);
      return false;
    }
    mbedtls_sha256_update(&ctx, block, (size_t)got);
    done += (uint64_t)got;
  }
  uint8_t digest[32];
  mbedtls_sha256_finish(&ctx, digest);
  mbedtls_sha256_free(&ctx);
  hex_digest(digest, out);
  return true;
}

static const char *json_string(cJSON *root, const char *key) {
  cJSON *item = cJSON_GetObjectItem(root, key);
  return cJSON_IsString(item) ? item->valuestring : nullptr;
}

static bool json_u64(cJSON *root, const char *key, uint64_t *out) {
  cJSON *item = cJSON_GetObjectItem(root, key);
  if (!cJSON_IsNumber(item)) return false;
  *out = (uint64_t)item->valuedouble;
  return true;
}

bool packv2_load(const char *pack_root, const char *active_pack_id, int evd_fd,
                 uint64_t evd_directory_off, uint64_t evd_directory_len) {
  s_active = false;
  int64_t load_start = esp_timer_get_time();
  char path[192];
  if (!build_path(path, sizeof(path), pack_root, PACKV2_IMAGE_NAME ".json")) {
    ESP_LOGE(TAG, "descriptor path too long");
    return false;
  }
  char *text = read_text_file(path);
  if (!text) {
    ESP_LOGE(TAG, "pack-v2 descriptor missing: %s", path);
    return false;
  }
  cJSON *descriptor = cJSON_Parse(text);
  free(text);
  if (!descriptor) {
    ESP_LOGE(TAG, "pack-v2 descriptor parse failed");
    return false;
  }

  const char *schema = json_string(descriptor, "schema_version");
  const char *layout = json_string(descriptor, "layout");
  const char *pack_id = json_string(descriptor, "source_pack_id");
  const char *image_sha = json_string(descriptor, "image_sha256");
  const char *dir_sha = json_string(descriptor, "source_directory_sha256");
  const char *compiler = json_string(descriptor, "compiler_identity");
  uint64_t capacity = 0, image_bytes = 0, record_bytes = 0;
  bool fields_ok = json_u64(descriptor, "entity_capacity", &capacity) &&
                   json_u64(descriptor, "image_bytes", &image_bytes) &&
                   json_u64(descriptor, "record_bytes", &record_bytes);
  bool shape_ok = fields_ok && schema && layout && image_sha && dir_sha &&
                  pack_id && strcmp(schema, PACKV2_SCHEMA) == 0 &&
                  strcmp(layout, PACKV2_LAYOUT) == 0 && capacity > 0 &&
                  capacity <= 0xFFFFFFFFu &&
                  image_bytes == capacity * PACKV2_RECORD_BYTES &&
                  record_bytes == PACKV2_RECORD_BYTES;
  if (!shape_ok) {
    ESP_LOGE(TAG, "pack-v2 descriptor shape/binding invalid");
    cJSON_Delete(descriptor);
    return false;
  }
  if (strcmp(pack_id, active_pack_id) != 0) {
    ESP_LOGE(TAG, "pack-v2 pack_id mismatch: %s != %s", pack_id, active_pack_id);
    cJSON_Delete(descriptor);
    return false;
  }

  /* Bind the derived image to the authoritative evidence directory bytes. */
  char actual_dir_sha[65];
  if (!sha256_span(evd_fd, evd_directory_off, evd_directory_len,
                   actual_dir_sha) ||
      strcmp(actual_dir_sha, dir_sha) != 0) {
    ESP_LOGE(TAG, "pack-v2 source directory sha256 mismatch");
    cJSON_Delete(descriptor);
    return false;
  }
  ESP_LOGI(TAG, "pack-v2 source directory hash OK (%llu bytes)",
           (unsigned long long)evd_directory_len);

  if (!build_path(path, sizeof(path), pack_root, PACKV2_IMAGE_NAME)) {
    cJSON_Delete(descriptor);
    return false;
  }
  int fd = open(path, O_RDONLY);
  if (fd < 0) {
    ESP_LOGE(TAG, "pack-v2 image missing: %s", path);
    cJSON_Delete(descriptor);
    return false;
  }
  uint8_t *table = (uint8_t *)heap_caps_malloc((size_t)image_bytes,
                                               MALLOC_CAP_SPIRAM);
  if (!table) {
    ESP_LOGE(TAG, "pack-v2 PSRAM allocation failed (%llu bytes)",
             (unsigned long long)image_bytes);
    close(fd);
    cJSON_Delete(descriptor);
    return false;
  }
  mbedtls_sha256_context ctx;
  mbedtls_sha256_init(&ctx);
  mbedtls_sha256_starts(&ctx, 0);
  uint64_t done = 0;
  bool io_ok = true;
  while (done < image_bytes) {
    ssize_t got = read(fd, table + done, (size_t)(image_bytes - done));
    if (got <= 0) {
      io_ok = false;
      break;
    }
    mbedtls_sha256_update(&ctx, table + done, (size_t)got);
    done += (uint64_t)got;
  }
  close(fd);
  uint8_t digest[32];
  mbedtls_sha256_finish(&ctx, digest);
  mbedtls_sha256_free(&ctx);
  char actual_image_sha[65];
  hex_digest(digest, actual_image_sha);
  if (!io_ok || strcmp(actual_image_sha, image_sha) != 0) {
    ESP_LOGE(TAG, "pack-v2 image hash mismatch (io_ok=%d)", (int)io_ok);
    heap_caps_free(table);
    cJSON_Delete(descriptor);
    return false;
  }

  s_table = table;
  s_capacity = (uint32_t)capacity;
  s_bytes = (size_t)image_bytes;
  s_active = true;
  packv2_stats_reset();
  double seconds = (double)(esp_timer_get_time() - load_start) / 1e6;
  printf("MEAS {\"phase\":\"packv2.load\",\"layout\":\"%s\","
         "\"entity_capacity\":%u,\"resident_bytes\":%u,"
         "\"image_sha256\":\"%s\",\"source_directory_sha256\":\"%s\","
         "\"compiler\":\"%s\",\"seconds\":%.2f}\n",
         PACKV2_LAYOUT, (unsigned)s_capacity, (unsigned)s_bytes,
         actual_image_sha, actual_dir_sha, compiler ? compiler : "unknown",
         seconds);
  cJSON_Delete(descriptor);
  return true;
}

bool packv2_active(void) { return s_active; }
const char *packv2_layout(void) { return s_active ? PACKV2_LAYOUT : "none"; }
uint32_t packv2_entity_capacity(void) { return s_capacity; }
size_t packv2_resident_bytes(void) { return s_bytes; }

bool packv2_find(uint32_t entity_idx, uint32_t *blob_off, uint32_t *blob_len,
                 uint32_t *count) {
  int64_t start = esp_timer_get_time();
  s_stats.lookups++;
  if (!s_active || entity_idx >= s_capacity) {
    s_stats.misses++;
    s_stats.cpu_us += (uint64_t)(esp_timer_get_time() - start);
    return false;
  }
  const uint8_t *entry = s_table + (size_t)entity_idx * PACKV2_RECORD_BYTES;
  uint32_t off, len, cnt;
  memcpy(&off, entry, 4);
  memcpy(&len, entry + 4, 4);
  memcpy(&cnt, entry + 8, 4);
  s_stats.cpu_us += (uint64_t)(esp_timer_get_time() - start);
  if (off == PACKV2_MISSING) {
    s_stats.misses++;
    return false;
  }
  *blob_off = off;
  *blob_len = len;
  *count = cnt;
  return true;
}

void packv2_stats(PackV2Stats *out) { *out = s_stats; }

void packv2_stats_reset(void) { memset(&s_stats, 0, sizeof(s_stats)); }
