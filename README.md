# AetherSparse host emulator

> Current classification: `DETERMINISTIC_RUNTIME_VALIDATED` /
> `SCALABLE_KNOWLEDGE_ARCHITECTURE_UNVALIDATED`.
>
> Frozen flat evidence-selection baseline: `REAL_CORPUS_ARCHITECTURE_FAILED`.
>
> Current v0.4 gate: `COGNITIVE_CELL_TOPOLOGY_QUALIFICATION_PENDING`.
>
> Current hardware action: no purchase.

The real-corpus qualification track adds immutable MediaWiki revisions,
article/section chunks, universal SQLite/FTS5 indexes, optional packet
compatibility, query-time temporary claims, a schema-flexible query state,
bounded traversal operations, an external FastAPI boundary, and an Android
browser corpus explorer.

The frozen 10,527-article, 2,000-question evidence-selection run did not qualify
the architecture. The compact reranker reached 84.10% article recall and 83.30%
span recall, versus 79.65% and 78.45% for static top-k. Targeted traversal did
not improve recall and increased p95 latency. See
[`reports/EVIDENCE_SELECTION_QUALIFICATION.md`](reports/EVIDENCE_SELECTION_QUALIFICATION.md).

AetherSparse is a deterministic, provenance-bound reasoning accessory emulator.
It autonomously tests whether source-aligned typed packets and bounded symbolic
programs can beat simple retrieval without relying on mass human labeling.

The Waveshare ESP32-P4/C6 touchscreen is **terminal-only**. It displays the web
client and sends small semantic requests to this external service. Parsing,
retrieval, packet storage, planning, verification, realization, traces, and
discourse state stay in the accessory.

## Reproduce

```bash
uv sync --extra dev
uv run aethersparse compile
uv run aethersparse evaluate
uv run aethersparse autonomy compile-silver
uv run aethersparse autonomy qualify --scale decisive
uv run pytest -q
uv run ruff check src tests
uv run mypy src
```

Run the two sides in separate processes:

```bash
uv run aethersparse serve --host 127.0.0.1 --port 8000
python -m http.server 8080 --directory web/terminal_simulator
```

Open `http://127.0.0.1:8000/` from Android. The evidence-selection UI exposes
the initial and reranked candidates, score components, selected evidence,
missing facets, targeted traversal, evidence path, bytes, latency, and model
MACs through the external API boundary.

> When did Apollo 11 land on the Moon?

The phone UI calls the external `/v2/selection/query` API. A self-contained
client is at `web/traversal_lab/index.html`; `deploy/Dockerfile`,
`render.yaml`, and `railway.json` provide hosted-service paths.

## Current scope

Phase 0 is frozen under tag `phase0-reference-v0.1.0`.

Earlier synthetic results validate deterministic execution only; they do not
override the failed real-corpus evidence-selection result.

## v0.4 cognitive-cell realignment

The failed flat-index result is retained as the matched baseline, not generalized
to an untested HKC topology. v0.4 adds four comparative cell constructions:
category-derived, entity/link communities, semantic buckets, and a bounded
overlapping hybrid. A 1,024-bit binary VSA supports approximate routing beside
an authoritative exact evidence ledger. Generated cell IDs are validated against
the canonical registry and may only act as hints.

```bash
uv run aethersparse cells build --kind hybrid
uv run aethersparse cells qualify
```

The topology gate remains pending until those commands are rerun over the frozen
1k, 10k, and 50k corpus packs. Tiny-corpus tests validate contracts only.
