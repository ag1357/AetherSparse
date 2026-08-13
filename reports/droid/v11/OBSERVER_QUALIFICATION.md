# Mission 6 observer qualification

## Result

The optional AetherCore research observer is implemented and isolated from
production inference. A source dependency audit found **0** observer imports
outside `src/aethersparse/observer`. The controller, verifier, and runtime do
not construct or depend on telemetry.

This checkpoint qualifies the observer mechanism. It does not claim specialist
model quality or product accuracy; those require real entity/value/workspace
records from the other Mission 6 lanes.

## Measured contract results

| Measurement | Result |
|---|---:|
| Mandatory sampling cases retained | failure: yes; high uncertainty: yes |
| Novel routes retained | 2 / 2 |
| Confident success at configured 0% rate | 1 / 1 dropped |
| Route repeat determinism | signature and SHA-256 identical |
| Known causal ablations attributed | 8 / 8 |
| Production imports of observer | 0 |
| Full activations retained by default | 0 |
| Maximum selected activation values | 256 |
| Mean compact record size in qualification | 1,393.25 bytes |
| Maximum compact record size in qualification | 1,720 bytes |

The eight controlled causal cases cover gate failure, expert failure, fusion
failure, insufficient depth, excessive depth, bad upstream state, missing
evidence, and verifier rejection. These checks validate attribution semantics;
they do not establish causal performance on the corpus.

## Implemented analysis

The analyzer reports activation histograms, dead-unit and saturation alerts,
deterministic covariance PCA/SVD, deterministic hidden-state clustering,
routing-signature clustering, expert utilization, expert coactivation, depth
and compute distributions, reliability/ECE/Brier/NLL, risk/coverage, entropy
versus correctness, disagreement versus correctness, and correctness/compute by
route and tier.
Counterfactual summaries separately identify causally under-deep and over-deep
routes and never infer those labels from route/failure correlation alone.

Every route is stored in compact readable form and content-addressed with
SHA-256. Novelty is therefore deterministic rather than dependent on process
hash randomization.

## Data discipline and safety

- Counterfactual label replay rejects every partition except `development` and
  `tuning`.
- Full hidden activations are never retained by default. Summary statistics are
  always bounded; selected vectors are opt-in and capped at 256 values.
- The observer returns no action or reward and cannot alter production control
  flow.
- Optimization proposals are inert `proposed` records. They cannot rewrite
  active code or weights.
- The sealed architecture registry describes both the active exact controller
  and the training-only observer, including costs, dependencies, failures, and
  calibration/model artifact fields.

## Integration handoff

Specialist/workspace/depth lanes should construct `CycleTelemetry` only after a
cycle completes and call `ResearchObserver.observe` from their research harness.
They should not import the observer from production controller modules. Real
counterfactual replay callbacks must preserve the split guard and exact
provenance verifier.

## Exact unresolved limitation

No learned Mission 6 expert output exists on this worker branch. Consequently,
activation clusters, calibration, and route transfer measurements here are
controlled contract measurements only. Corpus-level observer findings must be
generated after integration receives real specialist cycles.
