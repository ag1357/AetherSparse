"""Shared question/evidence encoder and shadow COG advisor.

This module is intentionally outside the runtime dependency path. PyTorch is
required only by the V15 qualification script; deterministic retrieval,
controller legality, and exact verification remain authoritative.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

ENCODER_BUCKETS = 8192
ENCODER_DIM = 192
ENCODER_HIDDEN = 1536
COG_FIELDS = 19
COG_HIDDEN = 512
COG_BOTTLENECK = 288
COG_LABELS = (
    "ABSTAIN",
    "ANSWER",
    "CLARIFY",
    "INCORRECT_PREMISE",
    "OUT_OF_CORPUS",
)
HASH_SEED = b"aether-v15-shared-encoder"


def encoder_parameter_count() -> int:
    return (
        ENCODER_BUCKETS * ENCODER_DIM
        + ENCODER_DIM * ENCODER_HIDDEN
        + ENCODER_HIDDEN
        + ENCODER_HIDDEN * ENCODER_DIM
        + ENCODER_DIM
    )


def cog_parameter_count() -> int:
    input_dim = ENCODER_DIM + COG_FIELDS
    return (
        input_dim * COG_HIDDEN
        + COG_HIDDEN
        + COG_HIDDEN * COG_BOTTLENECK
        + COG_BOTTLENECK
        + COG_BOTTLENECK * len(COG_LABELS)
        + len(COG_LABELS)
    )


def hashed_ngrams(text: str, *, max_bytes: int = 512) -> tuple[int, ...]:
    """Deterministic UTF-8 2..4-gram ids for EmbeddingBag."""

    normalized = " ".join(text.casefold().split()).encode("utf-8")[:max_bytes]
    framed = b"^" + normalized + b"$"
    ids: list[int] = []
    for width in range(2, 5):
        for start in range(max(0, len(framed) - width + 1)):
            digest = hashlib.blake2b(
                framed[start : start + width], digest_size=8, key=HASH_SEED
            ).digest()
            ids.append(int.from_bytes(digest, "little") % ENCODER_BUCKETS)
    return tuple(ids or (0,))


def sparse_hash_baseline(text: str) -> frozenset[int]:
    return frozenset(hashed_ngrams(text))


def set_cosine(left: frozenset[int], right: frozenset[int]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / math.sqrt(len(left) * len(right))


def require_torch() -> tuple[object, object]:
    try:
        import torch
        from torch import nn
    except ImportError as exc:  # pragma: no cover - qualification environment issue
        raise RuntimeError("the shadow learned experiment requires PyTorch") from exc
    return torch, nn


def make_encoder() -> object:
    torch, nn = require_torch()

    class SharedEncoder(nn.Module):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__()
            self.embedding = nn.EmbeddingBag(
                ENCODER_BUCKETS, ENCODER_DIM, mode="mean"
            )
            self.project = nn.Sequential(
                nn.Linear(ENCODER_DIM, ENCODER_HIDDEN),
                nn.ReLU(),
                nn.Linear(ENCODER_HIDDEN, ENCODER_DIM),
            )

        def forward(self, ids: object, offsets: object) -> object:
            value = self.project(self.embedding(ids, offsets))
            return torch.nn.functional.normalize(value, dim=-1)

    model = SharedEncoder()
    actual = sum(parameter.numel() for parameter in model.parameters())
    if actual != encoder_parameter_count():
        raise AssertionError(f"encoder parameter mismatch: {actual}")
    return model


def make_cog_advisor() -> object:
    _, nn = require_torch()

    class CogAdvisor(nn.Module):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__()
            self.layers = nn.Sequential(
                nn.Linear(ENCODER_DIM + COG_FIELDS, COG_HIDDEN),
                nn.ReLU(),
                nn.Linear(COG_HIDDEN, COG_BOTTLENECK),
                nn.ReLU(),
                nn.Linear(COG_BOTTLENECK, len(COG_LABELS)),
            )

        def forward(self, encoded: object, cog: object) -> object:
            return self.layers(__import__("torch").cat((encoded, cog), dim=-1))

    model = CogAdvisor()
    actual = sum(parameter.numel() for parameter in model.parameters())
    if actual != cog_parameter_count():
        raise AssertionError(f"COG advisor parameter mismatch: {actual}")
    return model


def packed_batch(texts: Sequence[str]) -> tuple[object, object]:
    torch, _ = require_torch()
    flat: list[int] = []
    offsets: list[int] = []
    for text in texts:
        offsets.append(len(flat))
        flat.extend(hashed_ngrams(text))
    return (
        torch.tensor(flat, dtype=torch.long),
        torch.tensor(offsets, dtype=torch.long),
    )


def encode_texts(
    model: object, texts: Sequence[str], *, batch_size: int = 32
) -> object:
    torch, _ = require_torch()
    outputs: list[object] = []
    model.eval()
    with torch.no_grad():
        for start in range(0, len(texts), batch_size):
            ids, offsets = packed_batch(texts[start : start + batch_size])
            outputs.append(model(ids, offsets))
    return torch.cat(outputs, dim=0)


@dataclass(frozen=True)
class RetrievalMetrics:
    recall_at_1: float
    recall_at_5: float
    recall_at_10: float
    mean_reciprocal_rank: float


def retrieval_metrics(
    query_vectors: object,
    evidence_vectors: object,
    gold_indexes: Sequence[set[int]],
) -> RetrievalMetrics:
    torch, _ = require_torch()
    scores = query_vectors @ evidence_vectors.T
    ranked = torch.argsort(scores, dim=1, descending=True)
    hits = {1: 0, 5: 0, 10: 0}
    reciprocal = 0.0
    for row, gold in zip(ranked.tolist(), gold_indexes, strict=True):
        rank = next((index + 1 for index, item in enumerate(row) if item in gold), None)
        if rank is None:
            continue
        reciprocal += 1.0 / rank
        for cutoff in hits:
            hits[cutoff] += int(rank <= cutoff)
    count = max(1, len(gold_indexes))
    return RetrievalMetrics(
        recall_at_1=hits[1] / count,
        recall_at_5=hits[5] / count,
        recall_at_10=hits[10] / count,
        mean_reciprocal_rank=reciprocal / count,
    )


def train_shared_encoder(
    model: object,
    pairs: Sequence[tuple[str, str]],
    *,
    epochs: int = 4,
    batch_size: int = 24,
    learning_rate: float = 0.002,
    seed: int = 15015,
) -> list[float]:
    torch, _ = require_torch()
    torch.manual_seed(seed)
    randomizer = random.Random(seed)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)
    losses: list[float] = []
    order = list(range(len(pairs)))
    for _ in range(epochs):
        randomizer.shuffle(order)
        epoch_loss = 0.0
        batches = 0
        model.train()
        for start in range(0, len(order), batch_size):
            indexes = order[start : start + batch_size]
            questions = [pairs[index][0] for index in indexes]
            evidence = [pairs[index][1] for index in indexes]
            q_ids, q_offsets = packed_batch(questions)
            e_ids, e_offsets = packed_batch(evidence)
            q_vec = model(q_ids, q_offsets)
            e_vec = model(e_ids, e_offsets)
            logits = (q_vec @ e_vec.T) / 0.08
            target = torch.arange(len(indexes), dtype=torch.long)
            loss = (
                torch.nn.functional.cross_entropy(logits, target)
                + torch.nn.functional.cross_entropy(logits.T, target)
            ) / 2
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            epoch_loss += float(loss.detach())
            batches += 1
        losses.append(epoch_loss / max(1, batches))
    return losses


def train_cog_advisor(
    advisor: object,
    frozen_vectors: object,
    cog_vectors: object,
    labels: Sequence[int],
    *,
    epochs: int = 40,
    learning_rate: float = 0.003,
    seed: int = 15015,
) -> list[float]:
    torch, _ = require_torch()
    torch.manual_seed(seed)
    target = torch.tensor(labels, dtype=torch.long)
    counts = torch.bincount(target, minlength=len(COG_LABELS)).float()
    weights = target.numel() / (len(COG_LABELS) * counts.clamp_min(1))
    optimizer = torch.optim.AdamW(advisor.parameters(), lr=learning_rate)
    losses: list[float] = []
    for _ in range(epochs):
        advisor.train()
        logits = advisor(frozen_vectors, cog_vectors)
        loss = torch.nn.functional.cross_entropy(logits, target, weight=weights)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        losses.append(float(loss.detach()))
    return losses


def classification_metrics(predicted: Sequence[int], expected: Sequence[int]) -> dict[str, object]:
    confusion = [[0 for _ in COG_LABELS] for _ in COG_LABELS]
    for want, got in zip(expected, predicted, strict=True):
        confusion[want][got] += 1
    recalls: list[float] = []
    f1s: list[float] = []
    for index in range(len(COG_LABELS)):
        true_positive = confusion[index][index]
        actual = sum(confusion[index])
        predicted_count = sum(row[index] for row in confusion)
        recall = true_positive / actual if actual else 0.0
        precision = true_positive / predicted_count if predicted_count else 0.0
        recalls.append(recall)
        f1s.append(
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
    return {
        "balanced_accuracy": sum(recalls) / len(recalls),
        "macro_f1": sum(f1s) / len(f1s),
        "confusion_matrix": confusion,
    }


def iter_named_parameters(models: Iterable[tuple[str, object]]) -> Iterable[tuple[str, object]]:
    for prefix, model in models:
        for name, parameter in model.state_dict().items():
            yield f"{prefix}.{name}", parameter
