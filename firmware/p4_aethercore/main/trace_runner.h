/* Phases 4-6 on-device: raw storage characterization, cache ladder, and the
 * witnessed 260-case trace-equivalent replay. All results are emitted as
 * single-line JSON records prefixed with MEAS for host-side parsing. */
#pragma once

/* Runs the SD-dependent phases. Safe to call with no card present (logs and
 * returns false). */
bool run_sd_phases(void);
