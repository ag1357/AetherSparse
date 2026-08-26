/* SD/MMC + paged pack IO for the V14 P4 qualification.
 *
 * Owns the physical storage path (SDMMC slot 1 on the Waveshare
 * ESP32-P4-WIFI6 TF slot: CLK=43 CMD=44 D0..D3=39..42), a PSRAM-backed
 * LRU page cache with reference-compatible accounting, and typed readers
 * for the ACP1IDX1 / ACP1ENT1 / ACP1EVD1 regions.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#define PACK_PAGE 4096
#define IDX_MAGIC "ACP1IDX1"
#define ENT_MAGIC "ACP1ENT1"
#define EVD_MAGIC "ACP1EVD1"
#define NO_ENTITY 0xFFFFFFFFu

/* Request classes: CLASS_POSTINGS follows the host reference accounting
 * (first page of a posting list = random read on miss, rest sequential). */
enum PageClass {
  CLASS_POSTINGS = 0,
  CLASS_SURFACE = 1,
  CLASS_EVIDENCE = 2,
  CLASS_OTHER = 3,
};

typedef struct {
  uint64_t pages_touched;   /* page requests (all classes) */
  uint64_t class_pages[4];  /* requests by PageClass */
  uint64_t class_misses[4]; /* cache misses by PageClass */
  uint64_t cache_hits;
  uint64_t cache_misses;
  uint64_t random_reads;    /* posting-list first-page misses */
  uint64_t sequential_reads;
  uint64_t physical_reads;  /* actual pread() calls served by the pager */
  uint64_t physical_bytes;
  uint64_t read_time_us;    /* time inside physical page reads */
  uint32_t region_pages[4]; /* physical pages by region: 0=idx 1=ent 2=evd 3=other */
} PagerStats;

typedef struct Pager Pager;

/* A paged region file. */
typedef struct {
  int fd;
  uint8_t region_id;
  uint64_t length;
  char path[96];
} RegionFile;

/* Mount the TF slot (4-bit SDMMC). Returns true on success. */
bool sd_mount(void);
void sd_card_report(void);
bool sd_is_mounted(void);

/* Pager lifecycle. Capacity in bytes; page store lives in PSRAM. */
Pager *pager_create(size_t capacity_bytes);
void pager_reset(Pager *pager);
void pager_destroy(Pager *pager);
void pager_stats(Pager *pager, PagerStats *out);
void pager_stats_reset(Pager *pager);
void pager_set_class(Pager *pager, uint8_t page_class);
size_t pager_capacity(Pager *pager);

/* Read one 4 KiB page (page_no * 4096 relative to the region file). */
const uint8_t *pager_page(Pager *pager, RegionFile *region, uint64_t page_no);

/* Direct (uncached, unmeasured-cache) read for benches and streaming verify. */
bool region_read(RegionFile *region, uint64_t offset, void *buffer, size_t length);

/* Pack discovery + integrity. */
bool pack_open(const char *mount_root);
bool pack_verify_regions(void);
const char *pack_id(void);

/* Addressing index (ACP1IDX1). */
bool idx_open(void);
uint32_t idx_surface_count(void);
uint32_t idx_gram_count(void);

/* Full address-path contract for a normalized surface:
 *  1. union of posting surface ids (grams in (bytes, gram) order, reference
 *     page accounting), digested as FNV-1a-64 over the sorted u32 LE ids;
 *  2. gram-overlap ranking of the union, capped at the controller's 64
 *     (ties by surface id), resolved to distinct entity indexes via the
 *     paged surface directory;
 *  3. occurrence totals over those entities via the paged evidence
 *     directory. */
typedef struct {
  uint64_t cand_digest;
  uint32_t cand_count;
  uint64_t entity_digest;   /* FNV-1a-64 over sorted u32 LE entity indexes */
  uint32_t entity_count;
  uint32_t occurrence_total;
  uint32_t first_entity_idx; /* smallest entity index (probe target) */
  bool has_entity;
} AddressResult;

bool idx_query_address(Pager *pager, const char *normalized_surface,
                       AddressResult *out);

/* Entity index + state for a 1-based surface id. */
bool idx_surface_entity(Pager *pager, uint32_t surface_id, uint32_t *entity_idx_out,
                        uint16_t *state_out);

/* Canonical objects (ACP1ENT1). */
bool ent_open(void);
uint32_t ent_count(void);
bool ent_key_at(uint32_t entity_idx, uint64_t *key_out);

/* Evidence (ACP1EVD1). */
bool evd_open(void);
uint32_t evd_occurrences(Pager *pager, uint32_t entity_idx, bool *found_out);
/* Read up to `length` bytes of an entity's occurrence blob (latency probe). */
bool evd_blob_head(Pager *pager, uint32_t entity_idx, uint8_t *buffer, size_t length,
                   size_t *read_out);

/* Evidence-directory lookup mode (V15 Pack-v2). The V14 paged binary search
 * stays compiled in for parity testing, rollback, and A/B diagnosis; the
 * DEGRADED mode is a loud marker used when Pack-v2 verification fails but
 * service continues on the V14 path. */
enum EvdMode {
  EVD_MODE_V14_PAGED = 0,
  EVD_MODE_V2_DIRECT = 1,
  EVD_MODE_DEGRADED_V14 = 2,
};
void evd_set_mode(int mode);
int evd_mode(void);
/* Authoritative directory span inside evidence.bin (for Pack-v2 binding). */
uint64_t evd_directory_offset(void);
uint64_t evd_directory_length(void);
/* Mounted pack root (for derived/ resolution). */
const char *pack_root_path(void);
/* Evidence-directory lookups that touched SD (any mode); must be 0 for a
 * whole PERFORMANCE-mode workload. */
uint64_t evd_dir_sd_reads(void);

/* The deployment region files (for benches). */
RegionFile *pack_region_evidence(void);
RegionFile *pack_region_index(void);
