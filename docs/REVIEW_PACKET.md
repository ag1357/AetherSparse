
# V15 review packet

## Decision

`V15_READY_WITH_STORAGE_EXPERIMENT_PENDING`

## Reproducible checkpoint

- Source main: `c3aa2ef61e6ae77a12063e47221c6e4decae3762`
- Source tree: `09888952949745677b6a1b4939b90f14ccfe83d8`
- Candidate branch: `work/aethercore-v15-operational-system`
- Qualification source: `93b22ff27a0304f41f323b32832bee667c937583`

## Headline evidence

- Frozen V14 cognition: 242/260 autonomous; 138/150 tuning; zero illegal actions,
  verifier bypasses, premature halts, or runaways.
- Native boundary: selected pinning and VERIFIED/TERMINAL freeze pass; 18 session
  and four COG CRC-valid forgeries reject; 180-byte COG roundtrip is byte-exact.
- Memory: four tiers, authorized user CRUD, multi-session restart/resume, checkpoint
  restore and deterministic delta replay pass.
- Deployment: PERFORMANCE projects 6,421,665 resident bytes and eliminates modeled
  evidence-directory media misses; physical Pack-v2 measurement remains pending.
- Agent: retained 5/5 real sandbox tasks, 55 operations, and zero integrations.
- Tactility: AetherLink C++17 roundtrip passes; AetherChat is 34.68% of Chat's C++
  lines. Exact custom Device-A BSP build remains pending because its source was not
  available and no pins are invented.

## Negative results retained

- DAgger: 243 roll-in states, 231/260 versus selected 242/260.
- Passage-context specialist: 54 int8 parameters, 239/260 and 129/150 tuning.
- Recurrence, adaptive learned depth, factorized heads, and cognitive lookup memory
  remain untested/deferred—not falsely rejected.

## Next physical action

Run the Kingston A2 card against the unchanged V14 binary first, then deploy V15
Pack-v2 and AetherChat using the Factory handoff. Record media-control and Pack-v2
results separately.
