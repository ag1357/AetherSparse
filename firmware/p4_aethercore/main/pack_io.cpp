#include "pack_io.h"

#include "pack_v2.h"

#include <cJSON.h>
#include <dirent.h>
#include <driver/sdmmc_host.h>
#include <errno.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <esp_vfs_fat.h>
#include <fcntl.h>
#include <mbedtls/sha256.h>
#include <sd_pwr_ctrl_by_on_chip_ldo.h>
#include <sdmmc_cmd.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static const char *TAG = "ac_pack_io";

#define MOUNT_POINT "/sdcard"
#define SD_CLK GPIO_NUM_43
#define SD_CMD GPIO_NUM_44
#define SD_D0 GPIO_NUM_39
#define SD_D1 GPIO_NUM_40
#define SD_D2 GPIO_NUM_41
#define SD_D3 GPIO_NUM_42

static bool s_mounted;
static sdmmc_card_t *s_card;
static int s_card_freq_khz;
static char s_pack_id[128];

static RegionFile s_region_index;
static RegionFile s_region_entities;
static RegionFile s_region_evidence;

/* Index header fields (populated by idx_open). */
static uint32_t s_surface_count;
static uint32_t s_gram_count;
static uint64_t s_postings_off;
static uint64_t s_surface_off;
static uint8_t *s_gram_dir;        /* PSRAM: entries + pool, resident */
static uint32_t s_gram_dir_bytes;

/* Entity header fields. */
static uint32_t s_entity_count;

/* Evidence header fields. */
static uint64_t s_evd_directory_off;
static uint64_t s_evd_directory_len;
static uint64_t s_evd_blobs_off;

/* ------------------------------------------------------------------------- */

struct Pager {
  size_t page_count;
  uint8_t *store; /* page_count * PACK_PAGE, PSRAM */
  uint32_t *slot_region; /* per slot: region id */
  uint64_t *slot_page;   /* per slot: page number */
  uint64_t *slot_tick;   /* LRU tick */
  bool *slot_valid;
  uint64_t tick;
  uint8_t current_class;
  PagerStats stats;
};

static uint8_t s_zero_cache_page[PACK_PAGE];

void pager_set_class(Pager *pager, uint8_t page_class) {
  if (pager) pager->current_class = page_class;
}

Pager *pager_create(size_t capacity_bytes) {
  Pager *pager = (Pager *)heap_caps_calloc(1, sizeof(Pager), MALLOC_CAP_INTERNAL);
  if (!pager) return nullptr;
  pager->page_count = capacity_bytes / PACK_PAGE;
  if (pager->page_count) {
    pager->store = (uint8_t *)heap_caps_malloc(pager->page_count * PACK_PAGE,
                                               MALLOC_CAP_SPIRAM);
    pager->slot_page = (uint64_t *)heap_caps_calloc(pager->page_count, 8,
                                                    MALLOC_CAP_INTERNAL);
    pager->slot_tick = (uint64_t *)heap_caps_calloc(pager->page_count, 8,
                                                    MALLOC_CAP_INTERNAL);
    pager->slot_region = (uint32_t *)heap_caps_calloc(pager->page_count, 4,
                                                      MALLOC_CAP_INTERNAL);
    pager->slot_valid = (bool *)heap_caps_calloc(pager->page_count, 1,
                                                 MALLOC_CAP_INTERNAL);
    if (!pager->store || !pager->slot_page || !pager->slot_tick ||
        !pager->slot_region || !pager->slot_valid) {
      pager_destroy(pager);
      return nullptr;
    }
  }
  return pager;
}

void pager_destroy(Pager *pager) {
  if (!pager) return;
  free(pager->store);
  free(pager->slot_page);
  free(pager->slot_tick);
  free(pager->slot_region);
  free(pager->slot_valid);
  free(pager);
}

void pager_reset(Pager *pager) {
  if (!pager) return;
  if (pager->page_count) {
    memset(pager->slot_valid, 0, pager->page_count);
  }
  pager->tick = 0;
  memset(&pager->stats, 0, sizeof(pager->stats));
}

void pager_stats(Pager *pager, PagerStats *out) { *out = pager ? pager->stats : PagerStats{}; }
void pager_stats_reset(Pager *pager) {
  if (pager) memset(&pager->stats, 0, sizeof(pager->stats));
}
size_t pager_capacity(Pager *pager) { return pager ? pager->page_count * PACK_PAGE : 0; }

bool region_read(RegionFile *region, uint64_t offset, void *buffer, size_t length) {
  size_t done = 0;
  while (done < length) {
    ssize_t got = pread(region->fd, (char *)buffer + done, length - done,
                        (off_t)(offset + done));
    if (got <= 0) return false;
    done += (size_t)got;
  }
  return true;
}

const uint8_t *pager_page(Pager *pager, RegionFile *region, uint64_t page_no) {
  int64_t start = esp_timer_get_time();
  pager->stats.pages_touched += 1;
  pager->stats.class_pages[pager->current_class & 3] += 1;
  if (pager->page_count) {
    for (size_t i = 0; i < pager->page_count; i++) {
      if (pager->slot_valid[i] && pager->slot_page[i] == page_no &&
          pager->slot_region[i] == region->region_id) {
        pager->stats.cache_hits += 1;
        pager->slot_tick[i] = ++pager->tick;
        return pager->store + i * PACK_PAGE;
      }
    }
  }
  pager->stats.cache_misses += 1;
  pager->stats.class_misses[pager->current_class & 3] += 1;
  size_t slot = 0;
  if (pager->page_count) {
    uint64_t oldest = UINT64_MAX;
    for (size_t i = 0; i < pager->page_count; i++) {
      if (!pager->slot_valid[i]) {
        slot = i;
        break;
      }
      if (pager->slot_tick[i] < oldest) {
        oldest = pager->slot_tick[i];
        slot = i;
      }
    }
  }
  uint8_t *dest = pager->page_count ? pager->store + slot * PACK_PAGE
                                    : s_zero_cache_page;
  bool ok = region_read(region, page_no * PACK_PAGE, dest, PACK_PAGE);
  pager->stats.physical_reads += 1;
  pager->stats.physical_bytes += PACK_PAGE;
  pager->stats.region_pages[region->region_id & 3] += 1;
  pager->stats.read_time_us += (uint64_t)(esp_timer_get_time() - start);
  if (!ok) return nullptr;
  if (pager->page_count) {
    pager->slot_valid[slot] = true;
    pager->slot_page[slot] = page_no;
    pager->slot_region[slot] = region->region_id;
    pager->slot_tick[slot] = ++pager->tick;
  }
  return dest;
}

/* ------------------------------------------------------------------------- */

bool sd_mount(void) {
  if (s_mounted) return true;
  esp_vfs_fat_sdmmc_mount_config_t mount_config = {};
  mount_config.format_if_mount_failed = false;
  /* Region fds + manifest + trace + dir listing + write-test file; the FATFS
   * VFS consumes additional slots internally (LFN, locking). */
  mount_config.max_files = 32;
  mount_config.allocation_unit_size = 16 * 1024;

  /* The vendor-qualified path on ESP32-P4 rev v1.3 tops out at 20 MHz
   * (SDMMC_FREQ_DEFAULT); the vendor example reports "Speed: 20.00 MHz
   * (limit: 20.00 MHz)" on this exact board. A failed mount attempt wedges
   * the slot driver (send_op_cond timeout on retry), so we start with the
   * known-good frequency and fully deinit between attempts. */
  /* The Waveshare ESP32-P4-WIFI6 powers the TF slot through the chip's
   * on-chip LDO channel 4; without this power-control handle the card never
   * comes up (send_op_cond timeout), matching the vendor 09_sdmmc example. */
  sd_pwr_ctrl_ldo_config_t ldo_config = {};
  ldo_config.ldo_chan_id = 4;
  sd_pwr_ctrl_handle_t pwr_ctrl = nullptr;
  if (sd_pwr_ctrl_new_on_chip_ldo(&ldo_config, &pwr_ctrl) != ESP_OK) {
    ESP_LOGE(TAG, "on-chip LDO power control init failed");
    return false;
  }

  const int freqs[] = {SDMMC_FREQ_DEFAULT, SDMMC_FREQ_HIGHSPEED};
  for (size_t attempt = 0; attempt < 2; attempt++) {
    sdmmc_host_t host = SDMMC_HOST_DEFAULT();
    host.max_freq_khz = freqs[attempt];
    host.pwr_ctrl_handle = pwr_ctrl;
    sdmmc_slot_config_t slot_config = SDMMC_SLOT_CONFIG_DEFAULT();
    slot_config.width = 4;
    slot_config.clk = SD_CLK;
    slot_config.cmd = SD_CMD;
    slot_config.d0 = SD_D0;
    slot_config.d1 = SD_D1;
    slot_config.d2 = SD_D2;
    slot_config.d3 = SD_D3;
    slot_config.flags |= SDMMC_SLOT_FLAG_INTERNAL_PULLUP;
    esp_err_t err = esp_vfs_fat_sdmmc_mount(MOUNT_POINT, &host, &slot_config,
                                            &mount_config, &s_card);
    if (err == ESP_OK) {
      s_mounted = true;
      s_card_freq_khz = freqs[attempt];
      ESP_LOGI(TAG, "card mounted at %d kHz (attempt %d)", freqs[attempt],
               (int)attempt);
      return true;
    }
    ESP_LOGW(TAG, "mount attempt %d at %d kHz failed: %s", (int)attempt,
             freqs[attempt], esp_err_to_name(err));
    /* A failed esp_vfs_fat_sdmmc_mount leaves the slot/host half-initialized;
     * without a full deinit the next attempt times out in send_op_cond. */
    sdmmc_host_deinit();
  }
  return false;
}

bool sd_is_mounted(void) { return s_mounted; }

void sd_card_report(void) {
  if (!s_mounted || !s_card) {
    ESP_LOGW(TAG, "no card");
    return;
  }
  int real_khz = 0;
  sdmmc_host_get_real_freq(s_card->host.slot, &real_khz);
  printf("MEAS {\"phase\":\"storage.card\",\"name\":\"%s\",\"capacity_bytes\":%llu,"
         "\"sector_size\":%u,\"max_freq_khz\":%u,\"real_freq_khz\":%u,"
         "\"speed_class\":%u,\"is_mmc\":%s,\"is_sdio\":%s}\n",
         s_card->cid.name, (unsigned long long)((uint64_t)s_card->csd.capacity *
                                                (uint64_t)s_card->csd.sector_size),
         (unsigned)s_card->csd.sector_size, (unsigned)s_card->max_freq_khz,
         (unsigned)real_khz, (unsigned)s_card->csd.card_command_class,
         s_card->is_mmc ? "true" : "false", s_card->is_sdio ? "true" : "false");
}

/* ------------------------------------------------------------------------- */

static char s_pack_root[128];

/* Non-printf path join: no -Wformat-truncation risk, truncation is checked. */
static bool build_path(char *out, size_t out_size, const char *root,
                       const char *leaf) {
  size_t need = strlen(root) + 1 + strlen(leaf) + 1;
  if (need > out_size) return false;
  strlcpy(out, root, out_size);
  strlcat(out, "/", out_size);
  strlcat(out, leaf, out_size);
  return true;
}

static bool open_region(const char *path, uint8_t region_id, RegionFile *out) {
  int fd = open(path, O_RDONLY);
  if (fd < 0) {
    ESP_LOGE(TAG, "open failed: %s (errno=%d %s)", path, errno,
             strerror(errno));
    return false;
  }
  struct stat st;
  if (fstat(fd, &st) != 0) {
    close(fd);
    return false;
  }
  out->fd = fd;
  out->region_id = region_id;
  out->length = (uint64_t)st.st_size;
  strlcpy(out->path, path, sizeof(out->path));
  return true;
}

bool pack_open(const char *mount_root) {
  char path[192];
  if (!build_path(path, sizeof(path), mount_root, "active-packs.json"))
    return false;
  FILE *file = fopen(path, "rb");
  if (!file) {
    ESP_LOGE(TAG, "active-packs.json missing");
    return false;
  }
  char buffer[1024];
  size_t len = fread(buffer, 1, sizeof(buffer) - 1, file);
  fclose(file);
  buffer[len] = 0;
  cJSON *root = cJSON_Parse(buffer);
  if (!root) return false;
  cJSON *packs = cJSON_GetObjectItem(root, "packs");
  cJSON *first = packs ? cJSON_GetArrayItem(packs, 0) : nullptr;
  cJSON *relpath = first ? cJSON_GetObjectItem(first, "path") : nullptr;
  if (!cJSON_IsString(relpath)) {
    cJSON_Delete(root);
    return false;
  }
  const char *relpath_str = relpath->valuestring;
  if (strlen(mount_root) + 1 + strlen(relpath_str) + 1 > sizeof(s_pack_root)) {
    cJSON_Delete(root);
    return false;
  }
  strlcpy(s_pack_root, mount_root, sizeof(s_pack_root));
  strlcat(s_pack_root, "/", sizeof(s_pack_root));
  strlcat(s_pack_root, relpath_str, sizeof(s_pack_root));
  cJSON_Delete(root);

  if (!build_path(path, sizeof(path), s_pack_root, "manifest.json")) return false;
  file = fopen(path, "rb");
  if (!file) return false;
  fseek(file, 0, SEEK_END);
  long manifest_len = ftell(file);
  fseek(file, 0, SEEK_SET);
  char *manifest_text = (char *)heap_caps_malloc((size_t)manifest_len + 1,
                                                 MALLOC_CAP_INTERNAL);
  if (!manifest_text) {
    fclose(file);
    return false;
  }
  if (fread(manifest_text, 1, (size_t)manifest_len, file) != (size_t)manifest_len) {
    fclose(file);
    free(manifest_text);
    return false;
  }
  fclose(file);
  manifest_text[manifest_len] = 0;
  cJSON *manifest = cJSON_Parse(manifest_text);
  if (!manifest) {
    free(manifest_text);
    return false;
  }
  cJSON *pack_id_item = cJSON_GetObjectItem(manifest, "pack_id");
  if (cJSON_IsString(pack_id_item)) {
    strlcpy(s_pack_id, pack_id_item->valuestring, sizeof(s_pack_id));
  }
  cJSON_Delete(manifest);
  free(manifest_text);

  /* Diagnostic: list the regions directory so a missing-file failure is
   * distinguishable from a driver-level open failure. */
  char dir_path[192];
  if (build_path(dir_path, sizeof(dir_path), s_pack_root, "regions")) {
    DIR *dir = opendir(dir_path);
    if (dir) {
      struct dirent *entry;
      while ((entry = readdir(dir)) != nullptr) {
        ESP_LOGI(TAG, "regions entry: %s", entry->d_name);
      }
      closedir(dir);
    } else {
      ESP_LOGE(TAG, "regions dir missing (errno=%d)", errno);
    }
  }
  if (!build_path(path, sizeof(path), s_pack_root, "regions/addressing-index.bin"))
    return false;
  if (!open_region(path, 0, &s_region_index)) return false;
  if (!build_path(path, sizeof(path), s_pack_root, "regions/canonical-objects.bin"))
    return false;
  if (!open_region(path, 1, &s_region_entities)) return false;
  if (!build_path(path, sizeof(path), s_pack_root, "regions/evidence.bin"))
    return false;
  if (!open_region(path, 2, &s_region_evidence)) return false;
  ESP_LOGI(TAG, "pack open: %s", s_pack_id);
  return true;
}

const char *pack_id(void) { return s_pack_id; }
RegionFile *pack_region_evidence(void) { return &s_region_evidence; }
RegionFile *pack_region_index(void) { return &s_region_index; }

bool pack_verify_regions(void) {
  /* Stream sha256 over each region and compare against manifest.json. */
  char path[192];
  if (!build_path(path, sizeof(path), s_pack_root, "manifest.json")) return false;
  FILE *file = fopen(path, "rb");
  if (!file) return false;
  fseek(file, 0, SEEK_END);
  long manifest_len = ftell(file);
  fseek(file, 0, SEEK_SET);
  char *manifest_text = (char *)heap_caps_malloc((size_t)manifest_len + 1,
                                                 MALLOC_CAP_INTERNAL);
  if (!manifest_text) {
    fclose(file);
    return false;
  }
  if (fread(manifest_text, 1, (size_t)manifest_len, file) != (size_t)manifest_len) {
    fclose(file);
    free(manifest_text);
    return false;
  }
  fclose(file);
  manifest_text[manifest_len] = 0;
  cJSON *manifest = cJSON_Parse(manifest_text);
  free(manifest_text);
  if (!manifest) return false;
  cJSON *regions = cJSON_GetObjectItem(manifest, "regions");
  if (!cJSON_IsArray(regions)) {
    cJSON_Delete(manifest);
    return false;
  }
  bool all_ok = true;
  uint8_t *block = (uint8_t *)heap_caps_malloc(256 * 1024, MALLOC_CAP_SPIRAM);
  cJSON *region;
  int64_t verify_start = esp_timer_get_time();
  cJSON_ArrayForEach(region, regions) {
    cJSON *path_item = cJSON_GetObjectItem(region, "path");
    cJSON *sha_item = cJSON_GetObjectItem(region, "sha256");
    cJSON *len_item = cJSON_GetObjectItem(region, "length");
    if (!cJSON_IsString(path_item) || !cJSON_IsString(sha_item) ||
        !cJSON_IsNumber(len_item)) {
      all_ok = false;
      break;
    }
    if (!build_path(path, sizeof(path), s_pack_root, path_item->valuestring)) {
      all_ok = false;
      break;
    }
    int fd = open(path, O_RDONLY);
    if (fd < 0) {
      all_ok = false;
      break;
    }
    mbedtls_sha256_context ctx;
    mbedtls_sha256_init(&ctx);
    mbedtls_sha256_starts(&ctx, 0);
    uint64_t total = 0;
    for (;;) {
      ssize_t got = read(fd, block, 256 * 1024);
      if (got < 0) {
        all_ok = false;
        break;
      }
      if (got == 0) break;
      mbedtls_sha256_update(&ctx, block, (size_t)got);
      total += (uint64_t)got;
    }
    uint8_t digest[32];
    mbedtls_sha256_finish(&ctx, digest);
    mbedtls_sha256_free(&ctx);
    close(fd);
    char hex[65];
    for (int i = 0; i < 32; i++) snprintf(hex + i * 2, 3, "%02x", digest[i]);
    bool ok = total == (uint64_t)len_item->valuedouble &&
              strcmp(hex, sha_item->valuestring) == 0;
    ESP_LOGI(TAG, "verify %s: %s (%llu bytes)", path_item->valuestring,
             ok ? "OK" : "MISMATCH", (unsigned long long)total);
    printf("MEAS {\"phase\":\"pack.verify\",\"region\":\"%s\",\"bytes\":%llu,"
           "\"sha256_ok\":%s}\n", path_item->valuestring,
           (unsigned long long)total, ok ? "true" : "false");
    if (!ok) all_ok = false;
  }
  free(block);
  cJSON_Delete(manifest);
  double seconds = (double)(esp_timer_get_time() - verify_start) / 1e6;
  printf("MEAS {\"phase\":\"pack.verify\",\"result\":\"%s\",\"seconds\":%.2f}\n",
         all_ok ? "PASS" : "FAIL", seconds);
  return all_ok;
}

/* ------------------------------------------------------------------------- */

static bool read_header(RegionFile *region, uint8_t *header) {
  return region_read(region, 0, header, PACK_PAGE);
}

bool idx_open(void) {
  uint8_t header[PACK_PAGE];
  if (!read_header(&s_region_index, header)) return false;
  if (memcmp(header, IDX_MAGIC, 8) != 0) return false;
  uint32_t page = 0;
  uint64_t gram_dir_off = 0, gram_dir_bytes = 0;
  memcpy(&page, header + 12, 4);
  memcpy(&s_surface_count, header + 16, 4);
  memcpy(&s_gram_count, header + 20, 4);
  memcpy(&gram_dir_off, header + 32, 8);
  memcpy(&gram_dir_bytes, header + 40, 8);
  memcpy(&s_postings_off, header + 48, 8);
  memcpy(&s_surface_off, header + 64, 8);
  if (page != PACK_PAGE) return false;
  s_gram_dir = (uint8_t *)heap_caps_malloc(gram_dir_bytes, MALLOC_CAP_SPIRAM);
  if (!s_gram_dir) return false;
  s_gram_dir_bytes = (uint32_t)gram_dir_bytes;
  if (!region_read(&s_region_index, gram_dir_off, s_gram_dir, gram_dir_bytes))
    return false;
  ESP_LOGI(TAG, "index: surfaces=%u grams=%u gram_dir=%u B resident",
           s_surface_count, s_gram_count, s_gram_dir_bytes);
  return true;
}

uint32_t idx_surface_count(void) { return s_surface_count; }
uint32_t idx_gram_count(void) { return s_gram_count; }

static const uint8_t *gram_entry(uint32_t index) {
  return s_gram_dir + (size_t)index * 16;
}

static int gram_compare(const uint8_t *entry, const char *target,
                        size_t target_len) {
  uint32_t pool_offset = 0;
  uint16_t name_len = 0;
  memcpy(&pool_offset, entry + 8, 4);
  memcpy(&name_len, entry + 12, 2);
  const uint8_t *name = s_gram_dir + (size_t)s_gram_count * 16 + pool_offset;
  size_t shared = name_len < target_len ? name_len : target_len;
  int cmp = memcmp(name, target, shared);
  if (cmp != 0) return cmp;
  if (name_len == target_len) return 0;
  return name_len < target_len ? -1 : 1;
}

static bool find_gram(const char *gram, size_t gram_len, uint32_t *offset_out,
                      uint32_t *length_out) {
  int64_t low = 0, high = (int64_t)s_gram_count - 1;
  while (low <= high) {
    int64_t mid = (low + high) / 2;
    const uint8_t *entry = gram_entry((uint32_t)mid);
    int cmp = gram_compare(entry, gram, gram_len);
    if (cmp == 0) {
      memcpy(offset_out, entry, 4);
      memcpy(length_out, entry + 4, 4);
      return true;
    }
    if (cmp < 0) low = mid + 1;
    else high = mid - 1;
  }
  return false;
}

/* UTF-8 codepoint-aware 3-gram extraction over a normalized surface,
 * equivalent to edge_runtime.layout._trigrams on the normalized text.
 * Writes (byte offset, byte length) pairs into the padded string. */
static size_t surface_trigrams(const uint8_t *padded, size_t padded_len,
                               uint32_t *offsets_out, size_t max_grams) {
  uint32_t cps[260];
  size_t count = 0;
  for (size_t i = 0; i < padded_len && count < 260;) {
    cps[count++] = (uint32_t)i;
    uint8_t byte = padded[i];
    size_t step = 1;
    if (byte >= 0xF0) step = 4;
    else if (byte >= 0xE0) step = 3;
    else if (byte >= 0xC0) step = 2;
    for (size_t k = 0; k < step && i < padded_len; k++, i++) {
    }
  }
  if (count >= 260) return 0;
  cps[count] = (uint32_t)padded_len;
  uint32_t gram_off[258];
  uint16_t gram_len[258];
  size_t gram_count = 0;
  for (size_t i = 0; i + 3 <= count; i++) {
    gram_off[gram_count] = cps[i];
    gram_len[gram_count] = (uint16_t)(cps[i + 3] - cps[i]);
    gram_count++;
  }
  /* insertion sort by bytes (UTF-8 byte order == codepoint order), dedup */
  for (size_t i = 1; i < gram_count; i++) {
    uint32_t off = gram_off[i];
    uint16_t len = gram_len[i];
    size_t j = i;
    while (j > 0) {
      uint16_t prev_len = gram_len[j - 1];
      size_t shared = prev_len < len ? prev_len : len;
      int cmp = memcmp(padded + gram_off[j - 1], padded + off, shared);
      bool greater = cmp > 0 || (cmp == 0 && prev_len > len);
      if (!greater) break;
      gram_off[j] = gram_off[j - 1];
      gram_len[j] = gram_len[j - 1];
      j--;
    }
    gram_off[j] = off;
    gram_len[j] = len;
  }
  size_t unique = 0;
  for (size_t i = 0; i < gram_count && unique < max_grams; i++) {
    if (i > 0 && gram_len[i] == gram_len[i - 1] &&
        memcmp(padded + gram_off[i], padded + gram_off[i - 1], gram_len[i]) == 0) {
      continue;
    }
    offsets_out[unique * 2] = gram_off[i];
    offsets_out[unique * 2 + 1] = gram_len[i];
    unique++;
  }
  return unique;
}

typedef struct {
  uint32_t offset;
  uint32_t length;
  uint32_t gram_off;
  uint16_t gram_len;
} GramRef;

/* Union of posting ids with reference accounting. On success the caller owns
 * (*union_out) (PSRAM; free()) holding *id_count_out sorted ids with
 * duplicates retained (run lengths give per-id gram overlap counts). */
static bool idx_query_union(Pager *pager, const char *normalized_surface,
                            uint64_t *digest_out, uint32_t *count_out,
                            uint32_t **union_out, uint32_t *id_count_out) {
  size_t surface_len = strlen(normalized_surface);
  size_t padded_len = surface_len + 4;
  if (padded_len > 1024) return false;
  uint8_t padded[1028];
  padded[0] = ' ';
  padded[1] = ' ';
  memcpy(padded + 2, normalized_surface, surface_len);
  padded[2 + surface_len] = ' ';
  padded[3 + surface_len] = ' ';

  uint32_t grams[516];
  size_t gram_count = surface_trigrams(padded, padded_len, grams, 258);
  if (gram_count == 0 && padded_len >= 5) return false;

  GramRef refs[258];
  size_t present = 0;
  for (size_t i = 0; i < gram_count; i++) {
    uint32_t off = grams[i * 2], len = grams[i * 2 + 1];
    uint32_t postings_offset = 0, postings_len = 0;
    if (find_gram((const char *)padded + off, len, &postings_offset, &postings_len)) {
      refs[present].offset = postings_offset;
      refs[present].length = postings_len;
      refs[present].gram_off = off;
      refs[present].gram_len = (uint16_t)len;
      present++;
    }
  }
  /* order by (posting bytes, gram bytes) exactly like the reference */
  for (size_t i = 1; i < present; i++) {
    GramRef ref = refs[i];
    size_t j = i;
    while (j > 0) {
      bool greater = refs[j - 1].length > ref.length;
      if (refs[j - 1].length == ref.length) {
        size_t shared = refs[j - 1].gram_len < ref.gram_len ? refs[j - 1].gram_len
                                                            : ref.gram_len;
        int cmp = memcmp(padded + refs[j - 1].gram_off, padded + ref.gram_off,
                         shared);
        greater = cmp > 0 || (cmp == 0 && refs[j - 1].gram_len > ref.gram_len);
      }
      if (!greater) break;
      refs[j] = refs[j - 1];
      j--;
    }
    refs[j] = ref;
  }

  /* Union of posting ids via the pager with reference page accounting. */
  uint64_t digest = 14695981039346656037ull;
  uint32_t count = 0;
  size_t merge_cap = 256 * 1024;
  uint8_t *merge = (uint8_t *)heap_caps_malloc(merge_cap, MALLOC_CAP_SPIRAM);
  size_t merge_used = 0;
  bool ok = merge != nullptr;
  uint64_t query_random = 0, query_sequential = 0;
  for (size_t i = 0; i < present && ok; i++) {
    uint32_t offset = refs[i].offset, length = refs[i].length;
    if (length == 0) continue;
    uint64_t first_page = (s_postings_off + offset) / PACK_PAGE;
    uint64_t last_page = (s_postings_off + offset + length - 1) / PACK_PAGE;
    for (uint64_t page = first_page; page <= last_page; page++) {
      uint64_t misses_before = pager->stats.cache_misses;
      pager_set_class(pager, CLASS_POSTINGS);
      const uint8_t *data = pager_page(pager, &s_region_index, page);
      if (pager->stats.cache_misses > misses_before) {
        if (page == first_page) query_random++;
        else query_sequential++;
      }
      if (!data) {
        ESP_LOGE(TAG, "pager_page failed: idx page %llu",
                 (unsigned long long)page);
        ok = false;
        break;
      }
      uint64_t page_start = page * PACK_PAGE;
      uint64_t span_start = s_postings_off + offset;
      uint64_t span_end = span_start + length;
      uint64_t copy_start = span_start > page_start ? span_start : page_start;
      uint64_t copy_end = span_end < page_start + PACK_PAGE ? span_end
                                                            : page_start + PACK_PAGE;
      size_t need = merge_used + (size_t)(copy_end - copy_start);
      while (need > merge_cap) {
        size_t new_cap = merge_cap * 2;
        if (new_cap > (12u << 20)) {
          ok = false;
          break;
        }
        uint8_t *grown = (uint8_t *)heap_caps_realloc(merge, new_cap,
                                                      MALLOC_CAP_SPIRAM);
        if (!grown) {
          ok = false;
          break;
        }
        merge = grown;
        merge_cap = new_cap;
      }
      if (!ok) break;
      memcpy(merge + merge_used, data + (copy_start - page_start),
             (size_t)(copy_end - copy_start));
      merge_used += (size_t)(copy_end - copy_start);
    }
  }
  if (ok) {
    uint32_t *ids = (uint32_t *)merge;
    size_t id_count = merge_used / 4;
    qsort(ids, id_count, 4, [](const void *a, const void *b) -> int {
      uint32_t va = *(const uint32_t *)a, vb = *(const uint32_t *)b;
      return va < vb ? -1 : va > vb ? 1 : 0;
    });
    for (size_t i = 0; i < id_count; i++) {
      if (i > 0 && ids[i] == ids[i - 1]) continue;
      uint32_t id = ids[i];
      count++;
      for (int b = 0; b < 4; b++) {
        digest ^= (id >> (8 * b)) & 0xFF;
        digest *= 1099511628211ull;
      }
    }
  }
  pager->stats.random_reads += query_random;
  pager->stats.sequential_reads += query_sequential;
  pager_set_class(pager, CLASS_OTHER);
  if (!ok) {
    free(merge);
    return false;
  }
  *digest_out = digest;
  *count_out = count;
  *union_out = (uint32_t *)merge;
  *id_count_out = (uint32_t)(merge_used / 4);
  return true;
}

/* The full bounded address contract: union digest, gram-overlap top-64 entity
 * resolution, occurrence totals. */
bool idx_query_address(Pager *pager, const char *surface, AddressResult *out) {
  memset(out, 0, sizeof(*out));
  out->cand_digest = 14695981039346656037ull;
  out->entity_digest = 14695981039346656037ull;
  uint32_t *ids = nullptr;
  uint32_t id_count = 0;
  if (!idx_query_union(pager, surface, &out->cand_digest, &out->cand_count, &ids,
                       &id_count)) {
    ESP_LOGE(TAG, "union failed for surface '%s'", surface);
    return false;
  }

  /* run-length pass over the sorted ids: per-id overlap count, keep the best
   * 64 by (-count, surface id) exactly like the exported contract */
  uint32_t top_ids[64];
  uint32_t top_counts[64];
  size_t top_n = 0;
  size_t i = 0;
  while (i < id_count) {
    uint32_t id = ids[i];
    uint32_t overlap = 1;
    while (i + overlap < id_count && ids[i + overlap] == id) overlap++;
    i += overlap;
    /* insert into top-64 if better */
    size_t pos = top_n;
    if (top_n < 64) {
      pos = top_n++;
    } else if (overlap > top_counts[63] ||
               (overlap == top_counts[63] && id < top_ids[63])) {
      pos = 63;
    } else {
      continue;
    }
    while (pos > 0 &&
           (overlap > top_counts[pos - 1] ||
            (overlap == top_counts[pos - 1] && id < top_ids[pos - 1]))) {
      if (pos < 64) {
        top_ids[pos] = top_ids[pos - 1];
        top_counts[pos] = top_counts[pos - 1];
      }
      pos--;
    }
    top_ids[pos] = id;
    top_counts[pos] = overlap;
  }
  free(ids);

  /* resolve top surface ids to entity indexes (surface directory pages) */
  uint32_t entities[64];
  size_t entity_n = 0;
  for (size_t t = 0; t < top_n; t++) {
    uint32_t entity_idx = 0;
    uint16_t state = 0;
    if (!idx_surface_entity(pager, top_ids[t], &entity_idx, &state)) {
      ESP_LOGE(TAG, "surface directory read failed: id %u",
               (unsigned)top_ids[t]);
      return false;
    }
    if (entity_idx != 0xFFFFFFFFu) {
      bool seen = false;
      for (size_t e = 0; e < entity_n; e++)
        if (entities[e] == entity_idx) {
          seen = true;
          break;
        }
      if (!seen) entities[entity_n++] = entity_idx;
    }
  }
  /* sort entity indexes ascending, digest, occurrences (evidence directory) */
  for (size_t a = 1; a < entity_n; a++) {
    uint32_t v = entities[a];
    size_t b = a;
    while (b > 0 && entities[b - 1] > v) {
      entities[b] = entities[b - 1];
      b--;
    }
    entities[b] = v;
  }
  for (size_t e = 0; e < entity_n; e++) {
    uint32_t id = entities[e];
    for (int b = 0; b < 4; b++) {
      out->entity_digest ^= (id >> (8 * b)) & 0xFF;
      out->entity_digest *= 1099511628211ull;
    }
  }
  out->entity_count = (uint32_t)entity_n;
  out->has_entity = entity_n > 0;
  out->first_entity_idx = entity_n ? entities[0] : 0xFFFFFFFFu;
  /* Host contract: a missing evidence directory entry contributes 0. */
  uint32_t occurrences = 0;
  for (size_t e = 0; e < entity_n; e++) {
    bool found = false;
    occurrences += evd_occurrences(pager, entities[e], &found);
  }
  out->occurrence_total = occurrences;
  return true;
}

bool idx_surface_entity(Pager *pager, uint32_t surface_id, uint32_t *entity_idx_out,
                        uint16_t *state_out) {
  uint64_t byte_offset = s_surface_off + (uint64_t)(surface_id - 1) * 16;
  pager_set_class(pager, CLASS_SURFACE);
  const uint8_t *page = pager_page(pager, &s_region_index, byte_offset / PACK_PAGE);
  pager_set_class(pager, CLASS_OTHER);
  if (!page) return false;
  const uint8_t *entry = page + (byte_offset % PACK_PAGE);
  memcpy(entity_idx_out, entry, 4);
  memcpy(state_out, entry + 4, 2);
  return true;
}

/* ------------------------------------------------------------------------- */

bool ent_open(void) {
  uint8_t header[PACK_PAGE];
  if (!read_header(&s_region_entities, header)) return false;
  if (memcmp(header, ENT_MAGIC, 8) != 0) return false;
  memcpy(&s_entity_count, header + 12, 4);
  ESP_LOGI(TAG, "entities: %u", s_entity_count);
  return true;
}

uint32_t ent_count(void) { return s_entity_count; }

bool ent_key_at(uint32_t entity_idx, uint64_t *key_out) {
  if (entity_idx >= s_entity_count) return false;
  uint64_t byte_offset = PACK_PAGE + (uint64_t)entity_idx * 20;
  uint8_t entry[20];
  if (!region_read(&s_region_entities, byte_offset, entry, sizeof(entry)))
    return false;
  memcpy(key_out, entry, 8);
  return true;
}

/* ------------------------------------------------------------------------- */

bool evd_open(void) {
  uint8_t header[PACK_PAGE];
  if (!read_header(&s_region_evidence, header)) return false;
  if (memcmp(header, EVD_MAGIC, 8) != 0) return false;
  memcpy(&s_evd_directory_off, header + 24, 8);
  memcpy(&s_evd_directory_len, header + 32, 8);
  memcpy(&s_evd_blobs_off, header + 40, 8);
  return true;
}

/* V15: evidence-directory mode + zero-SD proof counter. */
static int s_evd_mode = EVD_MODE_V14_PAGED;
static uint64_t s_evd_dir_sd_reads;

void evd_set_mode(int mode) { s_evd_mode = mode; }
int evd_mode(void) { return s_evd_mode; }
uint64_t evd_directory_offset(void) { return s_evd_directory_off; }
uint64_t evd_directory_length(void) { return s_evd_directory_len; }
const char *pack_root_path(void) { return s_pack_root; }
uint64_t evd_dir_sd_reads(void) { return s_evd_dir_sd_reads; }

static bool evd_find(uint32_t entity_idx, uint32_t *blob_off, uint32_t *blob_len,
                     uint32_t *count, Pager *pager) {
  if (s_evd_mode == EVD_MODE_V2_DIRECT) {
    /* Pack-v2 resident direct table: this branch must never touch SD. */
    if (!packv2_active()) return false;
    uint64_t reads_before = pager ? pager->stats.physical_reads : 0;
    bool found = packv2_find(entity_idx, blob_off, blob_len, count);
    uint64_t reads_after = pager ? pager->stats.physical_reads : 0;
    s_evd_dir_sd_reads += reads_after - reads_before;
    return found;
  }
  uint32_t entries = (uint32_t)(s_evd_directory_len / 16);
  int64_t low = 0, high = (int64_t)entries - 1;
  uint64_t reads_before = pager ? pager->stats.physical_reads : 0;
  while (low <= high) {
    int64_t mid = (low + high) / 2;
    uint64_t byte_offset = s_evd_directory_off + (uint64_t)mid * 16;
    pager_set_class(pager, CLASS_EVIDENCE);
    const uint8_t *page = pager_page(pager, &s_region_evidence,
                                     byte_offset / PACK_PAGE);
    if (!page) return false;
    const uint8_t *entry = page + (byte_offset % PACK_PAGE);
    uint32_t idx;
    memcpy(&idx, entry, 4);
    if (idx == entity_idx) {
      memcpy(blob_off, entry + 4, 4);
      memcpy(blob_len, entry + 8, 4);
      memcpy(count, entry + 12, 4);
      if (pager) s_evd_dir_sd_reads += pager->stats.physical_reads - reads_before;
      return true;
    }
    if (idx < entity_idx) low = mid + 1;
    else high = mid - 1;
  }
  /* V14/degraded paged path: account directory probes that hit storage. */
  if (pager) s_evd_dir_sd_reads += pager->stats.physical_reads - reads_before;
  return false;
}

uint32_t evd_occurrences(Pager *pager, uint32_t entity_idx, bool *found_out) {
  uint32_t off = 0, len = 0, count = 0;
  bool found = evd_find(entity_idx, &off, &len, &count, pager);
  if (found_out) *found_out = found;
  return found ? count : 0;
}

bool evd_blob_head(Pager *pager, uint32_t entity_idx, uint8_t *buffer,
                   size_t length, size_t *read_out) {
  uint32_t off = 0, len = 0, count = 0;
  if (!evd_find(entity_idx, &off, &len, &count, pager)) return false;
  size_t want = len < length ? len : length;
  uint64_t start = s_evd_blobs_off + off;
  size_t done = 0;
  while (done < want) {
    uint64_t byte_offset = start + done;
    const uint8_t *page = pager_page(pager, &s_region_evidence,
                                     byte_offset / PACK_PAGE);
    if (!page) return false;
    size_t in_page = PACK_PAGE - (byte_offset % PACK_PAGE);
    size_t take = want - done < in_page ? want - done : in_page;
    memcpy(buffer + done, page + (byte_offset % PACK_PAGE), take);
    done += take;
  }
  if (read_out) *read_out = done;
  return true;
}
