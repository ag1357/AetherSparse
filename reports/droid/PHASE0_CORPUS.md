# Phase 0 — corpus rebuild

## Source

- Dump: `simplewiki-20260701-pages-articles.xml.bz2` (352 MB compressed)
- Resolved from the official `dumpstatus.json` for dump date **20260701** — the
  same dump date the frozen v0.5 qualification packs were built from
  (`docs/reproduction/V050_QUALIFICATION.md`), so revision IDs align with the
  benchmark's gold evidence instead of drifting.
- Acquisition verified against official SHA-1/MD5
  (`scripts/acquire_simplewiki_dump.py`); SHA-256 of the archive:
  `541a2547b6cc72e91449719226d05181234cfadb2531a69faca1969245c8cb5d`.

## Packs

| pack | schema | documents | chunks | bytes | build wall time |
|---|---|---:|---:|---:|---:|
| `simplewiki-v050-20260701-10k.sqlite` | v0.5 canonical (user_version 500) | 10,000 | 147,549 | 725,901,312 | ~2.5 min |
| `selector-10k.sqlite` | legacy CorpusStore (selector-native) | 10,000 | 147,549 | 498,409,472 | ~2.5 min |
| `selector-full.sqlite` | legacy CorpusStore | (building) | | | |

Both 10k packs were built in parallel from the verified dump on the CM5 (4
cores); no offload trigger fired (RAM stayed above 9 Gi available, disk > 250 GB
free, estimated wall time far below 4 h).

v050 10k pack integrity (builder-verified): `sqlite_integrity=ok`,
0 foreign-key violations, 0 source-binding failures; 2,349 redirects;
779,447 anchors; 255 duplicate-source-hash groups preserved (638 documents).
Selector 10k pack: 12,153 aliases (titles + redirect targets), 608,393 links,
24,891 category rows.

## Gold document coverage (gate)

Gold: 705 distinct documents / 705 distinct pageids across 2,050 cases
(1,280 ANSWER cases).

| pack | exact `simplewiki:{pageid}:{revid}` | pageid |
|---|---:|---:|
| v050-10k | **705/705 = 100.00%** | 100.00% |
| selector-10k | 0/705 (different ID scheme, expected) | **705/705 = 100.00%** |

Every one of the 2,050 cases has all of its gold pageids present in both packs.
Coverage is 100% (>= 95% gate): proceed with **no recall-ceiling adjustment**.

Because the dump date matches the original ingestion, exact IDs and raw
wikitext bytes also match: span offsets transfer exactly, so span-level
degradation artifacts (mission warning 3) do not apply to this rebuild.

## ID alignment mitigation (as required)

- `scripts/droid/v050_common.py:pageid()` parses `simplewiki:{pageid}:{revid}`
  and `mw:{pageid}:{revid}:{hash}` and compares on the pageid component only.
- Article-level (pageid) recall is the primary metric; span recall, where
  reported, is separate.

## Historical pack-hash note

The rebuilt v050 10k pack SHA-256
(`8e2e03d35b847a67364256afd1144c908128765ed02280c059a3d3299d542521`) differs
from the historical R2 parent pack hash recorded in
`reports/v050/V050_EVALUATION_INTEGRITY.md`: the builder code has evolved on
this branch since that run (e.g. distinct-source-page handling), while the
dump bytes are identical. Document-level content (IDs, revisions, wikitext)
reproduces exactly, as the 100% exact-ID coverage shows.
