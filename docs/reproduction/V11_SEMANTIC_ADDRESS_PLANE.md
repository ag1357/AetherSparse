# Reproducing Semantic Address Plane v1 qualification

Semantic Address Plane v1 consumes an external occurrence-statistics gzip and
its manifest without copying the private payload into Git. It verifies both
compressed and decompressed hashes, the source hard-negative identity, all
Laplace probabilities, ambiguity entropies, canonical entity IDs, and support
counts before exposing an address distribution.

The plane returns a subdistribution over authoritative corpus entity IDs. Any
probability assigned to an anchor target that the source pack could not map to
one canonical entity is retained as `unresolved_probability_mass`; it is never
renormalized onto the surviving IDs. A retained linker candidate without
occurrence support is exposed as unsupported and receives no invented anchor
probability.

## Qualification command

```bash
PYTHONPATH=src:. python scripts/droid/v11_semantic_address_qualify.py \
  --anchor-statistics /private/entity-anchor-statistics-10k.json.gz \
  --anchor-manifest /private/entity-anchor-statistics-10k.json.gz.manifest.json \
  --hard-negatives /private/ENTITY_HARD_NEGATIVES_V11.json.gz \
  --hard-negatives-manifest /private/ENTITY_HARD_NEGATIVES_V11.manifest.json \
  --output reports/droid/v11/semantic-address-plane-qualification.json \
  --manifest-output \
    reports/droid/v11/semantic-address-plane-qualification.manifest.json
```

The output is deterministic for byte-identical inputs. The manifest records
the four external input hashes and the report hash, but no row-level payload.

## Split policy

- Only `development` and `tuning` case groups may enter qualification.
- Every replica of a case remains in the case's one partition.
- Development is the only future fitting partition.
- Tuning is reserved for calibration, successive-halving decisions, and model
  selection.
- Evaluation and final-held cases are rejected before any statistic is used.
- The current run performs no fitting or contextual specialist training.

Occurrence counts are unsupervised corpus measurements, not answer labels.
The supplied target set nevertheless covers only mention surfaces selected from
the development/tuning residual. It is therefore a targeted overlay, not a
claim of full-corpus coverage.

## Conservative failure semantics

The gold-aware qualification taxonomy says only what retained replay state can
prove:

- an entirely empty mention set;
- no required address in the retained set;
- only part of the required address set retained;
- all required addresses top-ranked but not selected;
- all required addresses retained but selection incomplete; or
- all required addresses selected.

It never emits `outside_cap` or `never_generated`. Those require the missing
pre-cap candidate pool and generation provenance. Likewise, case-level correct
entity IDs cannot label a partially missing mention because every supplied
`correct_entity_per_mention` field is null.

## Runtime integration boundary

`aethersparse.controller.semantic_address.SemanticAddressPlane` is a bounded,
immutable lookup plane. A future linker may use its raw features—occurrence
support, distinct-source support, source diversity, P(entity|mention), entropy,
title prior, redirect prior/support, and alias types—as deterministic or learned
inputs. Context, relation compatibility, and candidate-generation channels must
remain separate features; they must not overwrite the corpus distribution.

Before deployment, compile the selected address table into a bounded lookup and
measure it with the existing analytical P4 digital twin. Do not emulate device
latency with sleeps and do not run another full-corpus product battery until a
development/tuning candidate-generation repair passes the Mission 6 gate.
