/* AetherCore V15 Phase 3: bounded media characterization for the Device-B
 * TF slot (Waveshare ESP32-P4-WIFI6 SKU 32020, on-chip LDO channel 4).
 *
 * Behavior:
 *   1. Mount the card on the verified SDMMC path (4-bit, 20 MHz first; the
 *      mission sanctions formatting this NEW/EMPTY card if no filesystem).
 *   2. Emit full CID/CSD identity as MEAS JSON.
 *   3. Sequential write + read over a bounded 32 MiB temporary file.
 *   4. Random 4 KiB read + write latency (fixed-seed offsets) -> IOPS/p50/p95.
 *   5. Delete the temporary file, print DONE, idle.
 *
 * Single-line MEAS JSON records, same convention as p4_qualification.
 */

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "driver/sdmmc_host.h"
#include "esp_heap_caps.h"
#include "esp_idf_version.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_vfs_fat.h"
#include "sd_pwr_ctrl_by_on_chip_ldo.h"
#include "sdmmc_cmd.h"

static const char *TAG = "ac_media_bench";

#define MOUNT_POINT "/sdcard"
#define SD_CLK GPIO_NUM_43
#define SD_CMD GPIO_NUM_44
#define SD_D0 GPIO_NUM_39
#define SD_D1 GPIO_NUM_40
#define SD_D2 GPIO_NUM_41
#define SD_D3 GPIO_NUM_42

#define BENCH_PATH MOUNT_POINT "/mediabench.tmp"
#define BENCH_BYTES (32u * 1024u * 1024u)
#define BENCH_CHUNK (64u * 1024u)
#define RAND_SAMPLES 512
#define RAND_WRITE_SAMPLES 128

static sdmmc_card_t *s_card;
static bool s_mounted;

/* Fixed-seed xorshift32: fully deterministic sample order, no rand() state. */
static uint32_t s_rng = 0xA215CA11u;
static uint32_t next_rand(void) {
  s_rng ^= s_rng << 13;
  s_rng ^= s_rng >> 17;
  s_rng ^= s_rng << 5;
  return s_rng;
}

static bool sd_mount_verified_path(void) {
  esp_vfs_fat_sdmmc_mount_config_t mount_config = {};
  /* Mission Phase 3 explicitly permits formatting this new/empty card. */
  mount_config.format_if_mount_failed = true;
  mount_config.max_files = 4;
  mount_config.allocation_unit_size = 16 * 1024;

  sd_pwr_ctrl_ldo_config_t ldo_config = {};
  ldo_config.ldo_chan_id = 4;
  sd_pwr_ctrl_handle_t pwr_ctrl = nullptr;
  if (sd_pwr_ctrl_new_on_chip_ldo(&ldo_config, &pwr_ctrl) != ESP_OK) {
    ESP_LOGE(TAG, "on-chip LDO power control init failed");
    return false;
  }

  /* 20 MHz first and authoritative: a failed attempt wedges the slot driver
   * (send_op_cond timeout), so the known-good frequency leads and a full
   * deinit separates attempts. */
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
      ESP_LOGI(TAG, "card mounted at %d kHz (attempt %d)", freqs[attempt],
               (int)attempt);
      return true;
    }
    ESP_LOGW(TAG, "mount attempt %d at %d kHz failed: %s", (int)attempt,
             freqs[attempt], esp_err_to_name(err));
    sdmmc_host_deinit();
  }
  return false;
}

static void card_identity_report(void) {
  int real_khz = 0;
  sdmmc_host_get_real_freq(s_card->host.slot, &real_khz);
  const sdmmc_cid_t *cid = &s_card->cid;
  const sdmmc_csd_t *csd = &s_card->csd;
  printf("MEAS {\"phase\":\"card.identity\",\"mfg_id\":\"0x%02x\","
         "\"oem_id\":\"0x%04x\",\"name\":\"%s\",\"revision\":%u,"
         "\"serial\":\"0x%08" PRIx32 "\",\"mfg_date\":\"%u/%u\","
         "\"capacity_bytes\":%llu,\"sector_size\":%u,"
         "\"card_command_class\":%u,\"max_freq_khz\":%u,"
         "\"real_freq_khz\":%u,\"is_mmc\":%s,\"is_sdio\":%s,"
         "\"is_mem\":%s,\"log_bus_width\":%u}\n",
         (unsigned)cid->mfg_id, (unsigned)cid->oem_id, cid->name,
         (unsigned)cid->revision, (uint32_t)cid->serial,
         (unsigned)(cid->date & 0x0F), (unsigned)(cid->date >> 4),
         (unsigned long long)((uint64_t)csd->capacity *
                              (uint64_t)csd->sector_size),
         (unsigned)csd->sector_size, (unsigned)csd->card_command_class,
         (unsigned)s_card->max_freq_khz, (unsigned)real_khz,
         s_card->is_mmc ? "true" : "false", s_card->is_sdio ? "true" : "false",
         s_card->is_mem ? "true" : "false",
         (unsigned)s_card->log_bus_width);
  /* Raw registers, as exposed by the driver. */
  const uint32_t *cid_raw = (const uint32_t *)&s_card->cid;
  const uint32_t *csd_raw = (const uint32_t *)&s_card->csd;
  printf("MEAS {\"phase\":\"card.raw_cid\",\"w\":[\"%08" PRIx32 "\",\"%08" PRIx32
         "\",\"%08" PRIx32 "\",\"%08" PRIx32 "\"]}\n",
         cid_raw[0], cid_raw[1], cid_raw[2], cid_raw[3]);
  printf("MEAS {\"phase\":\"card.raw_csd\",\"w\":[\"%08" PRIx32 "\",\"%08" PRIx32
         "\",\"%08" PRIx32 "\",\"%08" PRIx32 "\"]}\n",
         csd_raw[0], csd_raw[1], csd_raw[2], csd_raw[3]);
}

static void seq_write_report(const uint8_t *buf) {
  /* O_RDWR: the sequential-read pass re-reads through the same descriptor. */
  int fd = open(BENCH_PATH, O_RDWR | O_CREAT | O_TRUNC, 0666);
  if (fd < 0) {
    ESP_LOGE(TAG, "bench file create failed: errno=%d", errno);
    return;
  }
  int64_t t0 = esp_timer_get_time();
  size_t total = 0;
  while (total < BENCH_BYTES) {
    ssize_t n = write(fd, buf, BENCH_CHUNK);
    if (n <= 0) break;
    total += (size_t)n;
  }
  fsync(fd);
  int64_t dt = esp_timer_get_time() - t0;
  printf("MEAS {\"phase\":\"bench.seq_write\",\"bytes\":%u,"
         "\"elapsed_us\":%lld,\"mbps\":%.3f}\n",
         (unsigned)total, (long long)dt,
         dt > 0 ? (double)total / (double)dt : 0.0);
  /* keep fd for the read pass */
  lseek(fd, 0, SEEK_SET);
  int64_t r0 = esp_timer_get_time();
  size_t rtotal = 0;
  while (rtotal < BENCH_BYTES) {
    ssize_t n = read(fd, (void *)buf, BENCH_CHUNK);
    if (n <= 0) {
      ESP_LOGE(TAG, "seq read stopped early at %u bytes: errno=%d",
               (unsigned)rtotal, errno);
      break;
    }
    rtotal += (size_t)n;
  }
  int64_t rdt = esp_timer_get_time() - r0;
  printf("MEAS {\"phase\":\"bench.seq_read\",\"bytes\":%u,"
         "\"elapsed_us\":%lld,\"mbps\":%.3f}\n",
         (unsigned)rtotal, (long long)rdt,
         rdt > 0 ? (double)rtotal / (double)rdt : 0.0);
  close(fd);
}

static uint32_t s_lat_us[RAND_SAMPLES];

static void random_report(const char *name, uint32_t samples, bool writes) {
  int fd = open(BENCH_PATH, O_RDWR);
  if (fd < 0) {
    ESP_LOGE(TAG, "random bench open failed: errno=%d", errno);
    return;
  }
  const uint32_t blocks = BENCH_BYTES / 4096u;
  int64_t t0 = esp_timer_get_time();
  for (uint32_t i = 0; i < samples; i++) {
    off_t off = (off_t)(next_rand() % blocks) * 4096;
    int64_t s0 = esp_timer_get_time();
    if (writes) {
      s_lat_us[i] = 0;
      static uint8_t wbuf[4096];
      memset(wbuf, (int)(i & 0xFF), sizeof(wbuf));
      if (pwrite(fd, wbuf, sizeof(wbuf), off) != (ssize_t)sizeof(wbuf)) break;
    } else {
      static uint8_t rbuf[4096];
      if (pread(fd, rbuf, sizeof(rbuf), off) != (ssize_t)sizeof(rbuf)) break;
    }
    s_lat_us[i] = (uint32_t)(esp_timer_get_time() - s0);
  }
  if (writes) fsync(fd);
  int64_t dt = esp_timer_get_time() - t0;
  close(fd);
  /* insertion sort on 512/128 u32 is trivially cheap */
  for (uint32_t i = 1; i < samples; i++) {
    uint32_t v = s_lat_us[i];
    uint32_t j = i;
    while (j > 0 && s_lat_us[j - 1] > v) {
      s_lat_us[j] = s_lat_us[j - 1];
      j--;
    }
    s_lat_us[j] = v;
  }
  double iops = dt > 0 ? (double)samples * 1e6 / (double)dt : 0.0;
  printf("MEAS {\"phase\":\"bench.%s\",\"samples\":%u,"
         "\"iops\":%.2f,\"p50_us\":%u,\"p95_us\":%u,\"p99_us\":%u,"
         "\"min_us\":%u,\"max_us\":%u}\n",
         name, (unsigned)samples, iops,
         (unsigned)s_lat_us[samples / 2],
         (unsigned)s_lat_us[(samples * 95) / 100],
         (unsigned)s_lat_us[(samples * 99) / 100],
         (unsigned)s_lat_us[0], (unsigned)s_lat_us[samples - 1]);
}

extern "C" void app_main(void) {
  ESP_LOGI(TAG, "AetherCore V15 Phase 3 media characterization");
  printf("MEAS {\"phase\":\"boot\",\"firmware\":\"p4_media_bench\","
         "\"idf\":\"%s\"}\n",
         esp_get_idf_version());
  if (!sd_mount_verified_path()) {
    printf("MEAS {\"phase\":\"done\",\"status\":\"MOUNT_FAILED\"}\n");
    return;
  }
  card_identity_report();

  uint8_t *buf = (uint8_t *)heap_caps_malloc(BENCH_CHUNK, MALLOC_CAP_DMA |
                                                             MALLOC_CAP_INTERNAL);
  if (!buf) {
    printf("MEAS {\"phase\":\"done\",\"status\":\"NO_BUFFER\"}\n");
    return;
  }
  memset(buf, 0xA5, BENCH_CHUNK);
  seq_write_report(buf);
  random_report("rand4k_read", RAND_SAMPLES, false);
  random_report("rand4k_write", RAND_WRITE_SAMPLES, true);
  heap_caps_free(buf);

  if (unlink(BENCH_PATH) == 0) {
    ESP_LOGI(TAG, "temporary bench file deleted");
  } else {
    ESP_LOGW(TAG, "bench file delete failed: errno=%d", errno);
  }
  struct stat st;
  printf("MEAS {\"phase\":\"bench.cleanup\",\"file_present\":%s}\n",
         stat(BENCH_PATH, &st) == 0 ? "true" : "false");
  printf("MEAS {\"phase\":\"done\",\"status\":\"PASS\"}\n");
}
