/* Pack-v2 direct_compact_resident evidence directory for Device B.
 *
 * The derived image (12-byte LE records indexed by entity index) is a pure
 * acceleration structure: it is verified at load time against the
 * authoritative pack (pack_id, evidence-directory sha256, image sha256) and
 * blob CONTENT still comes from the authoritative ACP1EVD1 evidence.bin.
 * If verification fails the module stays inactive and the caller must run
 * the marked V14 paged fallback (DEGRADED_V14_LOOKUP) instead.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Locate <pack_root>/derived/evidence-directory-v2.bin(.json), verify the
 * descriptor bindings and image hash, and load the direct table into PSRAM.
 * evd_fd/evd_directory_* describe the authoritative V14 evidence region and
 * are used to re-hash the source directory span (cheap, ~4 MB). */
bool packv2_load(const char *pack_root, const char *active_pack_id, int evd_fd,
                 uint64_t evd_directory_off, uint64_t evd_directory_len);

bool packv2_active(void);
const char *packv2_layout(void);
uint32_t packv2_entity_capacity(void);
size_t packv2_resident_bytes(void);

/* O(1) resident lookup. No pager, no SD access. */
bool packv2_find(uint32_t entity_idx, uint32_t *blob_off, uint32_t *blob_len,
                 uint32_t *count);

typedef struct {
  uint64_t lookups;
  uint64_t misses;       /* holes (0xFFFFFFFF) + out-of-range */
  uint64_t sd_dir_reads; /* directory reads served from SD; must stay 0 */
  uint64_t cpu_us;       /* total lookup CPU */
} PackV2Stats;
void packv2_stats(PackV2Stats *out);
void packv2_stats_reset(void);
