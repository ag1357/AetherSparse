"""Measured entity-recovery primitives for the Mission 6 specialist lane.

This module deliberately separates facts available in a controller replay from
facts that require the occurrence-level v0.5 pack.  The replay can support a
small candidate-relevance scorer.  Anchor priors are exported only from the
canonical SQLite occurrence rows; they are never reconstructed from distinct
anchor aliases.
"""

from __future__ import annotations

import hashlib
import math
import sqlite3
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from aethersparse.controller.models import EntityCandidate, EntityMention, ResolutionMethod

ENTITY_FEATURE_NAMES = (
    "bias",
    "name_score",
    "relation_score",
    "context_score",
    "method_exact_title",
    "method_redirect",
    "method_alias",
    "method_anchor",
    "method_fuzzy",
)


def _normalize(value: str) -> str:
    replaced = value.replace("_", " ")
    return " ".join(unicodedata.normalize("NFKC", replaced).casefold().split())


def _entity_id(title: str) -> str:
    digest = hashlib.sha256(_normalize(title).encode("utf-8")).hexdigest()[:24]
    return f"as:v050:entity:{digest}"


def entity_features(candidate: EntityCandidate) -> tuple[float, ...]:
    """Return replay-supported features without treating neutral type=1 as evidence."""

    return (
        1.0,
        candidate.name_score,
        candidate.relation_score,
        candidate.context_score,
        float(candidate.method is ResolutionMethod.EXACT_TITLE),
        float(candidate.method is ResolutionMethod.REDIRECT),
        float(candidate.method is ResolutionMethod.ALIAS),
        float(candidate.method is ResolutionMethod.ANCHOR),
        float(candidate.method is ResolutionMethod.FUZZY),
    )


class WeightedCandidate(NamedTuple):
    candidate: EntityCandidate
    relevant: bool
    weight: float


@dataclass(frozen=True)
class LinearEntityRanker:
    """Compact candidate-relevance logistic scorer fitted on development only."""

    weights: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.weights) != len(ENTITY_FEATURE_NAMES):
            raise ValueError("entity ranker weight count does not match feature schema")

    def probability(self, candidate: EntityCandidate) -> float:
        logit = sum(
            weight * feature
            for weight, feature in zip(self.weights, entity_features(candidate), strict=True)
        )
        bounded = max(-30.0, min(30.0, logit))
        return 1.0 / (1.0 + math.exp(-bounded))


def fit_linear_entity_ranker(
    observations: Sequence[WeightedCandidate],
    *,
    epochs: int = 3_000,
    learning_rate: float = 0.3,
    l2: float = 0.001,
) -> LinearEntityRanker:
    """Fit deterministic weighted logistic regression with full-batch descent."""

    if not observations:
        raise ValueError("at least one candidate observation is required")
    if epochs < 1:
        raise ValueError("epochs must be positive")
    if learning_rate <= 0.0:
        raise ValueError("learning_rate must be positive")
    if l2 < 0.0:
        raise ValueError("l2 must be non-negative")
    denominator = sum(item.weight for item in observations)
    if denominator <= 0.0 or any(item.weight <= 0.0 for item in observations):
        raise ValueError("candidate weights must be positive")
    weights = [0.0] * len(ENTITY_FEATURE_NAMES)
    for epoch in range(epochs):
        gradient = [0.0] * len(weights)
        for candidate, relevant, sample_weight in observations:
            features = entity_features(candidate)
            logit = sum(weight * feature for weight, feature in zip(weights, features, strict=True))
            bounded = max(-30.0, min(30.0, logit))
            probability = 1.0 / (1.0 + math.exp(-bounded))
            error = (probability - float(relevant)) * sample_weight
            for index, feature in enumerate(features):
                gradient[index] += error * feature
        # The mild decay keeps the final iterations stable and is part of the
        # frozen fitting identity used by the Mission 6 report.
        rate = learning_rate * max(0.1, 1.0 - epoch / (epochs + 1_000))
        for index in range(len(weights)):
            penalty = 0.0 if index == 0 else l2 * weights[index]
            weights[index] -= rate * (gradient[index] / denominator + penalty)
    return LinearEntityRanker(tuple(weights))


def classify_entity_residual(
    required_entity_ids: Sequence[str], mentions: Sequence[EntityMention]
) -> str:
    """Assign only a failure class directly observable from retained replay state."""

    required = set(required_entity_ids)
    if not mentions:
        return "mention_not_detected"
    generated = {candidate.entity_id for mention in mentions for candidate in mention.candidates}
    if not required.intersection(generated):
        return "correct_entity_not_generated"
    if not required.issubset(generated):
        return "correct_entity_not_generated_partial"
    top_ranked = {mention.candidates[0].entity_id for mention in mentions if mention.candidates}
    selected = {
        mention.selected_entity_id for mention in mentions if mention.selected_entity_id is not None
    }
    if required.issubset(top_ranked) and not required.issubset(selected):
        return "correct_entity_top_ranked_but_rejected"
    return "correct_entity_present_but_misranked"


@dataclass(frozen=True)
class AnchorStatistic:
    mention: str
    target_title: str
    target_entity_id: str | None
    occurrence_count: int
    total_mention_occurrences: int
    probability: float
    ambiguity_count: int
    entropy_nats: float
    source_document_count: int
    title_indicator: bool
    title_prior: float
    redirect_indicator: bool
    redirect_support_count: int
    redirect_prior: float
    alias_types: tuple[str, ...]


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        )
    }


def extract_anchor_statistics(
    pack_path: Path,
    *,
    alpha: float = 1.0,
    mentions: Sequence[str] | None = None,
) -> tuple[AnchorStatistic, ...]:
    """Aggregate occurrence-level anchor priors from a canonical v0.5 pack.

    Every row in ``anchors`` is one hyperlink occurrence.  Distinct source
    document support is retained separately from the raw occurrence count.
    """

    if alpha <= 0.0:
        raise ValueError("alpha must be positive")
    if not pack_path.is_file():
        raise FileNotFoundError(pack_path)
    connection = sqlite3.connect(f"{pack_path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        required_tables = {"documents", "aliases", "redirects", "anchors"}
        missing = required_tables - _table_names(connection)
        if missing:
            raise ValueError(f"v0.5 anchor export lacks tables: {sorted(missing)}")
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version != 500:
            raise ValueError(f"expected v0.5 schema user_version=500, received {version}")
        selected_mentions = tuple(sorted({_normalize(item) for item in mentions or () if item}))
        if selected_mentions:
            placeholders = ",".join("?" for _ in selected_mentions)
            rows = list(
                connection.execute(
                    f"""SELECT anchor_text,target_title,source_document_id
                           FROM anchors
                          WHERE anchor_text IN ({placeholders})
                          ORDER BY anchor_text,target_title,source_document_id,
                                   raw_start,anchor_id""",
                    selected_mentions,
                )
            )
        else:
            rows = list(
                connection.execute(
                    """SELECT anchor_text,target_title,source_document_id
                         FROM anchors
                        ORDER BY anchor_text,target_title,source_document_id,raw_start,anchor_id"""
                )
            )
        documents: dict[str, list[str]] = defaultdict(list)
        for title, normalized_title, redirect_target in connection.execute(
            """SELECT title,normalized_title,redirect_target
                 FROM documents ORDER BY normalized_title,document_id"""
        ):
            if redirect_target is None:
                documents[_normalize(str(normalized_title))].append(str(title))
        redirect_support: dict[tuple[str, str], int] = defaultdict(int)
        redirect_targets: dict[str, set[str]] = defaultdict(set)
        for alias, target_title in connection.execute(
            """SELECT a.alias,r.target_title
                 FROM redirects AS r JOIN aliases AS a
                   ON a.document_id=r.source_document_id
                ORDER BY a.alias,r.target_title"""
        ):
            mention = _normalize(str(alias))
            target = _normalize(str(target_title))
            redirect_support[(mention, target)] += 1
            redirect_targets[mention].add(target)
    finally:
        connection.close()

    pair_counts: dict[tuple[str, str], int] = defaultdict(int)
    source_documents: dict[tuple[str, str], set[str]] = defaultdict(set)
    mention_targets: dict[str, set[str]] = defaultdict(set)
    for anchor_text, target_title, source_document_id in rows:
        mention = _normalize(str(anchor_text))
        target = _normalize(str(target_title))
        pair_counts[(mention, target)] += 1
        source_documents[(mention, target)].add(str(source_document_id))
        mention_targets[mention].add(target)

    result: list[AnchorStatistic] = []
    for mention in sorted(mention_targets):
        targets = sorted(mention_targets[mention])
        total = sum(pair_counts[(mention, target)] for target in targets)
        ambiguity = len(targets)
        denominator = total + alpha * ambiguity
        probabilities = [
            (pair_counts[(mention, target)] + alpha) / denominator for target in targets
        ]
        entropy = -sum(probability * math.log(probability) for probability in probabilities)
        title_matches = sum(
            int(target == mention and bool(documents.get(target))) for target in targets
        )
        redirect_total = sum(redirect_support[(mention, target)] for target in targets)
        for target, probability in zip(targets, probabilities, strict=True):
            canonical_titles = documents.get(target, [])
            target_entity_id = (
                _entity_id(canonical_titles[0]) if len(canonical_titles) == 1 else None
            )
            title_indicator = target == mention and bool(canonical_titles)
            redirect_count = redirect_support[(mention, target)]
            alias_types = ["anchor"]
            if title_indicator:
                alias_types.append("title")
            if redirect_count:
                alias_types.append("redirect")
            result.append(
                AnchorStatistic(
                    mention=mention,
                    target_title=target,
                    target_entity_id=target_entity_id,
                    occurrence_count=pair_counts[(mention, target)],
                    total_mention_occurrences=total,
                    probability=probability,
                    ambiguity_count=ambiguity,
                    entropy_nats=entropy,
                    source_document_count=len(source_documents[(mention, target)]),
                    title_indicator=title_indicator,
                    title_prior=float(title_indicator) / max(1, title_matches),
                    redirect_indicator=bool(redirect_count),
                    redirect_support_count=redirect_count,
                    redirect_prior=redirect_count / max(1, redirect_total),
                    alias_types=tuple(alias_types),
                )
            )
    return tuple(result)
