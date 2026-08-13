# Reproduce the Mission 6 observer checkpoint

Run from the repository root:

```bash
UV_CACHE_DIR=/tmp/aethersparse-v11-observer-uv uv sync --extra dev
UV_CACHE_DIR=/tmp/aethersparse-v11-observer-uv uv run pytest tests/observer -q
UV_CACHE_DIR=/tmp/aethersparse-v11-observer-uv uv run ruff check \
  src/aethersparse/observer tests/observer \
  scripts/droid/v11_observer_analyze.py \
  scripts/droid/v11_observer_qualify.py
UV_CACHE_DIR=/tmp/aethersparse-v11-observer-uv uv run mypy --strict \
  src/aethersparse/observer \
  scripts/droid/v11_observer_analyze.py \
  scripts/droid/v11_observer_qualify.py
UV_CACHE_DIR=/tmp/aethersparse-v11-observer-uv uv run python \
  scripts/droid/v11_observer_qualify.py \
  --output reports/droid/v11/observer-qualification.json
```

Analyze a real sampled telemetry stream with:

```bash
UV_CACHE_DIR=/tmp/aethersparse-v11-observer-uv uv run python \
  scripts/droid/v11_observer_analyze.py telemetry.jsonl \
  --output observer-analysis.json
```

The input JSONL is expected to contain `aethercore.observer.v1` records. Do not
commit large telemetry streams, full activation dumps, or model caches. Commit
only compact analysis reports and external-artifact manifests/hashes.
