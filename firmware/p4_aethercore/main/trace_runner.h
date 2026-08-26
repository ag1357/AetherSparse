/* Phases 4-6 on-device: raw storage characterization, cache ladder, and the
 * witnessed 260-case trace-equivalent replay. All results are emitted as
 * single-line JSON records prefixed with MEAS for host-side parsing. */
#pragma once

/* Runs the SD-dependent phases. Safe to call with no card present (logs and
 * returns false). */
bool run_sd_phases(void);

/* Split boot so the interactive service mode reuses the identical verified
 * path: mount + pack verify + Pack-v2 load (boot), then optionally the
 * qualification ladder/replay (qual). */
bool run_pack_boot(void);
bool run_qual_phases(void);
