# Droid mission environment (baseline)

Recorded at mission start, before any source change.

## Host

- Machine: Raspberry Pi CM5 (pibrick), `Linux raspberrypi 6.18.34+rpt-rpi-2712 #1 SMP PREEMPT Debian 1:6.18.34-1+rpt1 (2026-06-09) aarch64`
- OS: Debian GNU/Linux 13 (trixie)
- CPU cores (`nproc`): 4
- RAM (`free -h`): 15 Gi total, 10 Gi available at record time; swap 2 Gi
- Python: 3.13.5 (venv `.venv`); SQLite library 3.46.1; git 2.47.3

## Disk (gate: >= 15 GB free)

- `/` (`/dev/mmcblk0p2`): 57 G total, **7.4 G free — FAILS the 15 GB gate**
- `/media/cloud/2982-E16B` (`/dev/sda1`, USB/SATA 452 G): **271 G free — passes**

Deviation from mission section 1: the working copy is therefore at
`/media/cloud/2982-E16B/work/AetherSparse` (not `~/work/AetherSparse`), and all
artifacts (dumps, packs, reports) live under `/media/cloud/2982-E16B/work/`.
The repository is self-contained; no step depends on the home path.

## Repository

- URL: https://github.com/ag1357/AetherSparse.git
- Base ref: `v0.5.0-structured-controller` (branch tip 3ff2b7e "Bind edge decision to canonical progressive packs")
- Mission branch: `droid/retrieval-accuracy-v06`
- Install: `pip install -e ".[dev]"` + numpy + scipy — OK

## Test gate

- `python -m pytest tests` (addopts already carries `-q`; a second `-q` hides the summary line):
  **176 passed, 0 failed** in ~7 s. GATE PASS.

## Attached hardware

- ESP32-P4 dev board on USB (Phase 8 only).

## Mission evaluation architecture (resolved during reading)

Two retrieval stacks exist in this repo:

1. `EvidenceSelector` (`selection/selector.py`) over the legacy `CorpusStore`
   schema (`mw:{pageid}:{revid}:{hash}` document IDs; categories/links/aliases
   tables). This is the stack the mission patch (`bm25_char3gram.patch`) and
   Phases 2-7 target (`candidate_limit`, `selection train`).
2. `SQLiteControllerProvider` (`controller/sqlite_provider.py`) over the
   canonical v0.5 pack schema (`simplewiki:{pageid}:{revid}`), driven by
   `scripts/run_v050_qualification.py`.

The V050 benchmark (`data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json`,
2,050 cases; partitions tuning 414 / development 271 / evaluation 1,019 /
final_held 346; 1,280 ANSWER cases; 705 distinct gold pageids) is the mission
metric source. Gold document IDs are `simplewiki:{pageid}:{revid}`.

Consequence: the selector is evaluated against the V050 benchmark through a
mission harness (`scripts/droid/`), matching gold at the **pageid** component
only (mission Phase 0 mitigation). The Phase 1 metric fix still lands in
`controller/evaluation.py` as specified, and the harness mirrors the same
lenient/strict definitions for the selector path.

## Tuning discipline

- Fit (weights, constants, thresholds) on `tuning` + `development` only.
- Keep/revert gate decisions use `tuning` + `development` numbers only.
- `evaluation` + `final_held` are reported for information at gates and in the
  final deliverable; they are never read to pick weights or make keep/revert
  decisions.
