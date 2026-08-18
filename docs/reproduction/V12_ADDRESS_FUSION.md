# Reproduce Mission 7 address-fusion lane

The lane qualifies the canonical union and calibration boundary using only the
committed v11 development/tuning aggregates. It does not read evaluation,
final-held, the unauthenticated replay extraction, or the post-cap 397k
candidate diagnostic.

```bash
PYTHONPATH=src python scripts/droid/v12_address_fusion_qualify.py
PYTHONPATH=src pytest \
  tests/controller/test_address_fusion.py \
  tests/observer/test_address_fusion_observer.py -q
ruff check \
  src/aethersparse/controller/address_fusion.py \
  src/aethersparse/observer/address_fusion.py \
  scripts/droid/v12_address_fusion_qualify.py \
  tests/controller/test_address_fusion.py \
  tests/observer/test_address_fusion_observer.py
mypy --strict \
  src/aethersparse/controller/address_fusion.py \
  src/aethersparse/observer/address_fusion.py \
  scripts/droid/v12_address_fusion_qualify.py
```

The command writes both the deterministic aggregate and a manifest containing
its SHA-256, byte count, base commit, and exact source-aggregate hashes.

After the FST, fuzzy, semantic/ANN, and address-data lanes produce the v12
channel outputs, construct one `AddressChannelOutput` per channel and mention,
including zero-result outputs. Record generated/emitted counts, the optional
channel cap, pre-cap completeness, source artifact SHA-256, and source schema
version and source-bundle SHA-256 on each output. Every proposal must carry its
typed source subchannel, source-record ID, channel-local pre-cap rank,
source-native raw score, bounded score, and the declared score transform. The
channel/subchannel mapping is exact: canonical title, alias, redirect, anchor
occurrence, fuzzy title, retained candidate, or semantic ANN. Then call
`union_address_channels` at K=8/16/32/64.
Corpus IDs must match the authoritative canonical-title hash; the union rejects
invalid syntax, conflicting titles, and overlapping retained/pruned sets. The
union records global pre-cap ranks and retains complete candidate/provenance
objects in the pruned sidecar so its emitted-record digests can be reconstructed.
Fit
`fit_address_fusion` on development only, select scalar temperature with
`select_temperature` on tuning only, and evaluate with
`evaluate_address_fusion`. The fixed 0.25M/1M/3M/5M successive-halving hook is
authorized only when `assess_specialist_readiness` receives a hashed tuning
`AddressQualification` bound to hash-matched pre-cap and mention-alignment
manifests plus a hash-matched source artifact/schema/bundle manifest. Raw scalar
claims cannot open the gate. The pre-cap manifest must cover all seven
generation channels for every tuning case. Persist unions and beliefs in the
versioned `aethersparse.address-union-envelope.v12` and
`aethersparse.address-belief-envelope.v12` content-addressed wire envelopes.

Calibration output has two non-interchangeable scopes. Availability-state
NLL/Brier/ECE score `P(E1..EN, UNRESOLVED)`. Resolved-address ECE and selective
risk score only returned entity IDs; `UNRESOLVED` is an abstention and reduces
coverage using the complete example count as denominator.
