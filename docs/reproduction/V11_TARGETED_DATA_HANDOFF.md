# Mission 6 minimal targeted data handoff

Do not run a broad corpus battery. The Work qualification is blocked only on
two narrow training-side captures.

## Entity capture

For the mention surfaces named by `ENTITY_HARD_NEGATIVES_V11.manifest.json`,
run the read-only occurrence exporter documented in `V11_ENTITY_SPECIALIST.md`
against the existing 10k/25k/397k v0.5 SQLite packs. Return occurrence counts,
distinct source-document support, aliases, redirects, title signals, and the
mention-aligned candidate-generation state. Preserve all tier replicas under
their existing development/tuning case partition.

## Value capture

For only the 43 remaining `VALUE_NOT_ENUMERATED` development/tuning replicas
identified by `reports/droid/v11/reachability-rerun.json`, run the exporter in
`V11_VALUE_ENUMERATION_HANDOFF.md`. Return selected chunk text, all regions and
matches before top-eight pruning, pre/post deduplication, pre/post cap, and
exact rebinding results. Do not return evaluation or final-held labels.

## Integrity

The consumer must verify:

- replay bundle SHA-256
  `099cd28b5c2a090d1b08d1e07d39e59ae6583854971c539085741fbb369f0246`;
- Mission 5 report SHA-256
  `280b314b313b69c72583702898bf135b614d725405587725d4d5f047601327cd`;
- benchmark SHA-256
  `1e8b89427898df3c3e5efef55135192cf6d48240f0f568c95eb1570470ead113`.

Return manifests/hashes with compact targeted outputs. Do not rebuild the 100k
cache, rerun Wikipedia retrieval, or run a final product battery before Work
has a candidate-generation repair that exceeds the Mission 6 reachability gate.
