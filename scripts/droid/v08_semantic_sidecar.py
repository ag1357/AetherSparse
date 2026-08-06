#!/usr/bin/env python3
"""Build the semantic sidecar for a corpus pack (Mission 3 Phase 4).

Embeds every chunk (ordered by SQLite rowid) with model2vec potion-base-8M,
fits PCA to 96 dims on a random 30k-chunk sample, transforms all vectors, and
quantizes to int8 with per-dimension scale/zeropoint.  Outputs:

  <pack-stem>.semantic.int8.npy    (n_chunks, 96) int8
  <pack-stem>.semantic.scales.npy  (96,) float32 scales
  <pack-stem>.semantic.meta.json   rowids, PCA components/mean, model id, sha

Diagnostic only; the pack itself is untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--model", default="minishlab/potion-base-8M")
    parser.add_argument("--pca-dims", type=int, default=96)
    parser.add_argument("--pca-sample", type=int, default=30000)
    parser.add_argument("--batch-size", type=int, default=1024)
    args = parser.parse_args()

    pack = Path(args.pack)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = pack.stem

    from model2vec import StaticModel

    model = StaticModel.from_pretrained(args.model)

    db = sqlite3.connect(f"file:{pack}?mode=ro", uri=True)
    rows = db.execute(
        "SELECT c.rowid, d.title, c.section_path, c.raw_text "
        "FROM chunks c JOIN documents d ON d.document_id=c.document_id "
        "ORDER BY c.rowid"
    )
    rowids: list[int] = []
    texts: list[str] = []
    for rowid, title, section, raw in rows:
        rowids.append(int(rowid))
        texts.append(f"{title} {section} {raw}"[:1024])
    n = len(rowids)
    print(f"embedding {n} chunks", flush=True)

    vectors = model.encode(texts, batch_size=args.batch_size, show_progress_bar=True)
    vectors = np.asarray(vectors, dtype=np.float32)
    print("embedded:", vectors.shape, flush=True)

    rng = np.random.default_rng(20260612)
    sample_idx = rng.choice(n, size=min(args.pca_sample, n), replace=False)
    sample = vectors[sample_idx]
    mean = sample.mean(axis=0)
    centered = sample - mean
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    components = vt[: args.pca_dims].astype(np.float32)
    reduced = (vectors - mean) @ components.T
    print("reduced:", reduced.shape, flush=True)

    # Symmetric per-dimension int8 quantization.
    absmax = np.maximum(np.abs(reduced).max(axis=0), 1e-8)
    scales = (absmax / 127.0).astype(np.float32)
    q = np.clip(np.round(reduced / scales), -127, 127).astype(np.int8)

    int8_path = out_dir / f"{stem}.semantic.int8.npy"
    scales_path = out_dir / f"{stem}.semantic.scales.npy"
    np.save(int8_path, q)
    np.save(scales_path, scales)

    digest = hashlib.sha256()
    digest.update(q.tobytes())
    digest.update(scales.tobytes())
    meta = {
        "pack": pack.name,
        "model": args.model,
        "n_chunks": n,
        "pca_dims": args.pca_dims,
        "pca_sample": int(len(sample_idx)),
        "rowids": rowids,
        "pca_mean": mean.astype(float).tolist(),
        "pca_components": components.astype(float).tolist(),
        "sidecar_sha256": digest.hexdigest(),
    }
    meta_path = out_dir / f"{stem}.semantic.meta.json"
    meta_path.write_text(json.dumps(meta))
    print(f"sidecar={int8_path}")
    print(f"meta={meta_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
