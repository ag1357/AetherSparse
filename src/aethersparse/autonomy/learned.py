"""Deterministic compact learned components for architecture qualification.

These models are deliberately small CPU baselines.  They use fixed-shape hashed
UTF-8 byte n-grams, deterministic multiclass logistic training, and a portable
JSON int8 export.  They do not perform free-form realization and they never
canonicalize evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import tracemalloc
import unicodedata
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Literal

LEARNED_COMPONENT_VERSION = "aethersparse.byte-linear.v1"
QUANTIZED_FORMAT: Literal["aethersparse.fixed-int8.v1"] = "aethersparse.fixed-int8.v1"
_UNKNOWN_PATTERN = re.compile(r"(?<![\w-])(?:[A-Z]{2,}[\w-]*|[\w-]*\d[\w-]*)(?![\w-])")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: object) -> str:
    payload = value if isinstance(value, str) else _canonical_json(value)
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _normalized_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


@dataclass(frozen=True)
class TextExample:
    """One supervised text classification example."""

    text: str
    label: str


@dataclass(frozen=True)
class PairExample:
    """One supervised query/evidence or claim/claim example."""

    left: str
    right: str
    label: str


@dataclass(frozen=True)
class Classification:
    """A deterministic classifier result with scores in label order."""

    label: str
    confidence: float
    scores: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class UnknownSpanCopy:
    """Verbatim unknown span retained for downstream clarification."""

    surface: str
    char_start: int
    char_end: int
    byte_start: int
    byte_end: int
    status: Literal["unknown_but_copyable"] = "unknown_but_copyable"


@dataclass(frozen=True)
class ParserPrediction:
    frame_label: str
    confidence: float
    scores: tuple[tuple[str, float], ...]
    unknown_spans: tuple[UnknownSpanCopy, ...]


@dataclass(frozen=True)
class AliasExample:
    alias: str
    entity_id: str


@dataclass(frozen=True)
class EntityLinkPrediction:
    entity_id: str | None
    confidence: float
    method: Literal["exact_alias", "learned_fallback", "unknown_copy"]
    unknown_span: UnknownSpanCopy | None
    scores: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class EvidenceExample:
    query: str
    evidence: str
    relevant: bool


@dataclass(frozen=True)
class EvidenceCandidate:
    candidate_id: str
    text: str


@dataclass(frozen=True)
class RankedEvidence:
    candidate_id: str
    score: float
    rank: int


@dataclass(frozen=True)
class ProbeExample:
    left: str
    right: str
    positive: bool


@dataclass(frozen=True)
class ProbePrediction:
    detected: bool
    confidence: float
    positive_probability: float


@dataclass(frozen=True)
class InferenceProfile:
    """Measured Python peak plus exact target-independent operation counts."""

    parameter_count: int
    float_parameter_bytes: int
    quantized_parameter_bytes: int
    active_feature_count: int
    active_macs: int
    dense_worst_case_macs: int
    target_feature_buffer_bytes: int
    measured_python_peak_working_ram_bytes: int


@dataclass(frozen=True)
class QuantizedLinearArtifact:
    """Portable fixed-shape int8 weights with per-output-row scales."""

    format: Literal["aethersparse.fixed-int8.v1"]
    component_kind: str
    component_version: str
    training_cache_hash: str
    labels: tuple[str, ...]
    feature_dim: int
    ngram_min: int
    ngram_max: int
    hash_seed: int
    parameter_count: int
    weight_dtype: Literal["int8"]
    bias_dtype: Literal["int32"]
    weights: tuple[tuple[int, ...], ...]
    biases: tuple[int, ...]
    scales: tuple[float, ...]
    artifact_hash: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @classmethod
    def from_json(cls, payload: str) -> QuantizedLinearArtifact:
        raw = json.loads(payload)
        if not isinstance(raw, dict):
            raise ValueError("quantized artifact must be a JSON object")
        artifact = cls(
            format=raw["format"],
            component_kind=str(raw["component_kind"]),
            component_version=str(raw["component_version"]),
            training_cache_hash=str(raw["training_cache_hash"]),
            labels=tuple(str(label) for label in raw["labels"]),
            feature_dim=int(raw["feature_dim"]),
            ngram_min=int(raw["ngram_min"]),
            ngram_max=int(raw["ngram_max"]),
            hash_seed=int(raw["hash_seed"]),
            parameter_count=int(raw["parameter_count"]),
            weight_dtype=raw["weight_dtype"],
            bias_dtype=raw["bias_dtype"],
            weights=tuple(tuple(int(value) for value in row) for row in raw["weights"]),
            biases=tuple(int(value) for value in raw["biases"]),
            scales=tuple(float(value) for value in raw["scales"]),
            artifact_hash=str(raw["artifact_hash"]),
        )
        artifact.verify()
        return artifact

    def verify(self) -> None:
        if self.format != QUANTIZED_FORMAT:
            raise ValueError(f"unsupported quantized format: {self.format}")
        expected_parameters = len(self.labels) * (self.feature_dim + 1)
        if self.parameter_count != expected_parameters:
            raise ValueError("parameter count does not match artifact shape")
        if len(self.weights) != len(self.labels) or len(self.biases) != len(self.labels):
            raise ValueError("output row count does not match labels")
        if len(self.scales) != len(self.labels):
            raise ValueError("quantization scales do not match labels")
        if any(len(row) != self.feature_dim for row in self.weights):
            raise ValueError("weight row does not match feature_dim")
        if any(value < -127 or value > 127 for row in self.weights for value in row):
            raise ValueError("weight outside signed int8 symmetric range")
        unsigned = self.to_dict()
        unsigned["artifact_hash"] = ""
        if self.artifact_hash != _sha256(unsigned):
            raise ValueError("artifact hash mismatch")

    def predict_text(self, text: str, *, namespace: str = "text") -> Classification:
        vectorizer = HashedByteVectorizer(
            feature_dim=self.feature_dim,
            ngram_min=self.ngram_min,
            ngram_max=self.ngram_max,
            hash_seed=self.hash_seed,
        )
        return self._predict_features(vectorizer.transform_parts(((namespace, text),)))

    def predict_pair(self, left: str, right: str) -> Classification:
        vectorizer = HashedByteVectorizer(
            feature_dim=self.feature_dim,
            ngram_min=self.ngram_min,
            ngram_max=self.ngram_max,
            hash_seed=self.hash_seed,
        )
        features = vectorizer.transform_pair(left, right)
        return self._predict_features(features)

    def _predict_features(self, features: tuple[tuple[int, float], ...]) -> Classification:
        scores: list[float] = []
        for row, bias, scale in zip(self.weights, self.biases, self.scales, strict=True):
            accumulator = float(bias)
            for index, value in features:
                accumulator += row[index] * value
            scores.append(accumulator * scale)
        return _classification(self.labels, scores)


class HashedByteVectorizer:
    """Fixed-shape signed hashing over normalized UTF-8 byte n-grams."""

    def __init__(
        self,
        *,
        feature_dim: int = 512,
        ngram_min: int = 1,
        ngram_max: int = 4,
        hash_seed: int = 0xA37E,
    ) -> None:
        if feature_dim < 16:
            raise ValueError("feature_dim must be at least 16")
        if ngram_min < 1 or ngram_max < ngram_min:
            raise ValueError("invalid n-gram range")
        self.feature_dim = feature_dim
        self.ngram_min = ngram_min
        self.ngram_max = ngram_max
        self.hash_seed = hash_seed

    def transform_parts(
        self,
        parts: Sequence[tuple[str, str]],
    ) -> tuple[tuple[int, float], ...]:
        counts: dict[int, float] = {}
        for namespace, text in parts:
            normalized = _normalized_text(text)
            framed = b"^" + normalized.encode("utf-8") + b"$"
            prefix = namespace.encode("utf-8") + b"\x1f"
            for size in range(self.ngram_min, self.ngram_max + 1):
                if size > len(framed):
                    continue
                for start in range(len(framed) - size + 1):
                    index, sign = self._bucket(prefix + framed[start : start + size])
                    counts[index] = counts.get(index, 0.0) + sign
        if not counts:
            return ()
        scale = max(1.0, math.sqrt(sum(value * value for value in counts.values())))
        return tuple((index, counts[index] / scale) for index in sorted(counts))

    def transform_pair(self, left: str, right: str) -> tuple[tuple[int, float], ...]:
        left_tokens = set(_normalized_text(left).split())
        right_tokens = set(_normalized_text(right).split())
        overlap = " ".join(sorted(left_tokens & right_tokens))
        return self.transform_parts(
            (
                ("left", left),
                ("right", right),
                ("overlap", overlap),
            )
        )

    def _bucket(self, token: bytes) -> tuple[int, float]:
        seed = self.hash_seed.to_bytes(8, "little", signed=False)
        digest = hashlib.blake2b(token, digest_size=8, key=seed).digest()
        hashed = int.from_bytes(digest, "little", signed=False)
        return hashed % self.feature_dim, 1.0 if hashed & (1 << 63) == 0 else -1.0


class FixedShapeLinearClassifier:
    """Deterministic multiclass logistic regression over sparse byte features."""

    def __init__(
        self,
        labels: Sequence[str],
        *,
        component_kind: str,
        feature_dim: int = 512,
        epochs: int = 32,
        learning_rate: float = 0.35,
        l2: float = 0.0005,
        hash_seed: int = 0xA37E,
    ) -> None:
        canonical_labels = tuple(sorted(set(labels)))
        if len(canonical_labels) < 2:
            raise ValueError("at least two unique labels are required")
        if epochs < 1 or learning_rate <= 0.0 or l2 < 0.0:
            raise ValueError("invalid training configuration")
        self.labels = canonical_labels
        self.component_kind = component_kind
        self.epochs = epochs
        self.learning_rate = learning_rate
        self.l2 = l2
        self.vectorizer = HashedByteVectorizer(
            feature_dim=feature_dim,
            hash_seed=hash_seed,
        )
        self._weights = [[0.0] * feature_dim for _ in self.labels]
        self._biases = [0.0] * len(self.labels)
        self.training_cache_hash = "UNTRAINED"
        self.model_version = f"{LEARNED_COMPONENT_VERSION}:UNTRAINED"

    @property
    def parameter_count(self) -> int:
        return len(self.labels) * (self.vectorizer.feature_dim + 1)

    def fit_text(self, examples: Sequence[TextExample]) -> None:
        prepared = [
            (self.vectorizer.transform_parts((("text", example.text),)), example.label)
            for example in examples
        ]
        cache_records = [
            {"text": _normalized_text(example.text), "label": example.label}
            for example in examples
        ]
        self._fit(prepared, cache_records)

    def fit_pairs(self, examples: Sequence[PairExample]) -> None:
        prepared = [
            (self.vectorizer.transform_pair(example.left, example.right), example.label)
            for example in examples
        ]
        cache_records = [
            {
                "left": _normalized_text(example.left),
                "right": _normalized_text(example.right),
                "label": example.label,
            }
            for example in examples
        ]
        self._fit(prepared, cache_records)

    def predict_text(self, text: str) -> Classification:
        self._require_trained()
        features = self.vectorizer.transform_parts((("text", text),))
        return self._predict(features)

    def predict_pair(self, left: str, right: str) -> Classification:
        self._require_trained()
        return self._predict(self.vectorizer.transform_pair(left, right))

    def profile_text(self, text: str) -> InferenceProfile:
        return self._profile(lambda: self.vectorizer.transform_parts((("text", text),)))

    def profile_pair(self, left: str, right: str) -> InferenceProfile:
        return self._profile(lambda: self.vectorizer.transform_pair(left, right))

    def export_int8(self) -> QuantizedLinearArtifact:
        self._require_trained()
        quantized_rows: list[tuple[int, ...]] = []
        quantized_biases: list[int] = []
        scales: list[float] = []
        for row, bias in zip(self._weights, self._biases, strict=True):
            maximum = max((abs(value) for value in (*row, bias)), default=0.0)
            scale = maximum / 127.0 if maximum > 0.0 else 1.0
            quantized_rows.append(tuple(_symmetric_int8(value / scale) for value in row))
            quantized_biases.append(round(bias / scale))
            scales.append(scale)
        unsigned: dict[str, object] = {
            "format": QUANTIZED_FORMAT,
            "component_kind": self.component_kind,
            "component_version": self.model_version,
            "training_cache_hash": self.training_cache_hash,
            "labels": self.labels,
            "feature_dim": self.vectorizer.feature_dim,
            "ngram_min": self.vectorizer.ngram_min,
            "ngram_max": self.vectorizer.ngram_max,
            "hash_seed": self.vectorizer.hash_seed,
            "parameter_count": self.parameter_count,
            "weight_dtype": "int8",
            "bias_dtype": "int32",
            "weights": tuple(quantized_rows),
            "biases": tuple(quantized_biases),
            "scales": tuple(scales),
            "artifact_hash": "",
        }
        artifact = QuantizedLinearArtifact(
            format=QUANTIZED_FORMAT,
            component_kind=self.component_kind,
            component_version=self.model_version,
            training_cache_hash=self.training_cache_hash,
            labels=self.labels,
            feature_dim=self.vectorizer.feature_dim,
            ngram_min=self.vectorizer.ngram_min,
            ngram_max=self.vectorizer.ngram_max,
            hash_seed=self.vectorizer.hash_seed,
            parameter_count=self.parameter_count,
            weight_dtype="int8",
            bias_dtype="int32",
            weights=tuple(quantized_rows),
            biases=tuple(quantized_biases),
            scales=tuple(scales),
            artifact_hash=_sha256(unsigned),
        )
        artifact.verify()
        return artifact

    def _fit(
        self,
        prepared: Sequence[tuple[tuple[tuple[int, float], ...], str]],
        cache_records: Sequence[dict[str, str]],
    ) -> None:
        if not prepared:
            raise ValueError("training examples may not be empty")
        unknown_labels = sorted({label for _, label in prepared} - set(self.labels))
        if unknown_labels:
            raise ValueError(f"examples contain labels outside model: {unknown_labels}")
        missing_labels = sorted(set(self.labels) - {label for _, label in prepared})
        if missing_labels:
            raise ValueError(f"every model label needs an example: {missing_labels}")
        self._weights = [
            [0.0] * self.vectorizer.feature_dim
            for _ in self.labels
        ]
        self._biases = [0.0] * len(self.labels)
        configuration = {
            "component_version": LEARNED_COMPONENT_VERSION,
            "component_kind": self.component_kind,
            "labels": self.labels,
            "feature_dim": self.vectorizer.feature_dim,
            "ngram_min": self.vectorizer.ngram_min,
            "ngram_max": self.vectorizer.ngram_max,
            "hash_seed": self.vectorizer.hash_seed,
            "epochs": self.epochs,
            "learning_rate": self.learning_rate,
            "l2": self.l2,
            "examples": cache_records,
        }
        self.training_cache_hash = _sha256(configuration)
        for epoch in range(self.epochs):
            order = sorted(
                range(len(prepared)),
                key=lambda index: _sha256(
                    {
                        "cache": self.training_cache_hash,
                        "epoch": epoch,
                        "index": index,
                    }
                ),
            )
            epoch_rate = self.learning_rate / (1.0 + 0.04 * epoch)
            for index in order:
                features, label = prepared[index]
                probabilities = _softmax(self._scores(features))
                expected_index = self.labels.index(label)
                for output_index, probability in enumerate(probabilities):
                    gradient = (1.0 if output_index == expected_index else 0.0) - probability
                    row = self._weights[output_index]
                    for feature_index, value in features:
                        row[feature_index] += epoch_rate * (
                            gradient * value - self.l2 * row[feature_index]
                        )
                    self._biases[output_index] += epoch_rate * gradient
        model_payload = {
            "training_cache_hash": self.training_cache_hash,
            "weights": self._weights,
            "biases": self._biases,
        }
        self.model_version = f"{LEARNED_COMPONENT_VERSION}:{_sha256(model_payload)[7:23]}"

    def _scores(self, features: tuple[tuple[int, float], ...]) -> list[float]:
        return [
            self._biases[row_index]
            + sum(self._weights[row_index][index] * value for index, value in features)
            for row_index in range(len(self.labels))
        ]

    def _predict(self, features: tuple[tuple[int, float], ...]) -> Classification:
        return _classification(self.labels, self._scores(features))

    def _profile(
        self,
        feature_builder: object,
    ) -> InferenceProfile:
        self._require_trained()
        if not callable(feature_builder):
            raise TypeError("feature_builder must be callable")
        already_tracing = tracemalloc.is_tracing()
        if already_tracing:
            before_current, _ = tracemalloc.get_traced_memory()
            tracemalloc.reset_peak()
        else:
            tracemalloc.start()
            before_current = 0
        features = feature_builder()
        if not isinstance(features, tuple):
            raise TypeError("feature builder returned invalid features")
        self._predict(features)
        _, peak = tracemalloc.get_traced_memory()
        measured_peak = max(0, peak - before_current)
        if not already_tracing:
            tracemalloc.stop()
        output_count = len(self.labels)
        feature_dim = self.vectorizer.feature_dim
        return InferenceProfile(
            parameter_count=self.parameter_count,
            float_parameter_bytes=self.parameter_count * 4,
            quantized_parameter_bytes=(
                output_count * feature_dim
                + output_count * 4
                + output_count * 4
            ),
            active_feature_count=len(features),
            active_macs=len(features) * output_count,
            dense_worst_case_macs=feature_dim * output_count,
            target_feature_buffer_bytes=feature_dim * 2,
            measured_python_peak_working_ram_bytes=measured_peak,
        )

    def _require_trained(self) -> None:
        if self.training_cache_hash == "UNTRAINED":
            raise RuntimeError("model must be trained before inference or export")


class QueryFrameParser:
    """Fixed-shape frame classifier with fail-open copying of unknown IDs."""

    def __init__(
        self,
        frame_labels: Sequence[str],
        *,
        known_terms: Sequence[str] = (),
        feature_dim: int = 512,
    ) -> None:
        self.classifier = FixedShapeLinearClassifier(
            frame_labels,
            component_kind="query_frame_parser",
            feature_dim=feature_dim,
        )
        self.known_terms = frozenset(_normalized_text(term) for term in known_terms)

    def fit(self, examples: Sequence[TextExample]) -> None:
        self.classifier.fit_text(examples)

    def predict(self, text: str) -> ParserPrediction:
        result = self.classifier.predict_text(text)
        return ParserPrediction(
            frame_label=result.label,
            confidence=result.confidence,
            scores=result.scores,
            unknown_spans=self.copy_unknown_spans(text),
        )

    def copy_unknown_spans(self, text: str) -> tuple[UnknownSpanCopy, ...]:
        spans: list[UnknownSpanCopy] = []
        for match in _UNKNOWN_PATTERN.finditer(text):
            if _normalized_text(match.group(0)) in self.known_terms:
                continue
            spans.append(_copy_span(text, match.start(), match.end()))
        return tuple(spans)

    def export_int8(self) -> QuantizedLinearArtifact:
        return self.classifier.export_int8()

    def profile(self, text: str) -> InferenceProfile:
        return self.classifier.profile_text(text)


class EntityAliasLinker:
    """Exact alias table followed by a thresholded learned fallback."""

    def __init__(
        self,
        entity_ids: Sequence[str],
        *,
        learned_threshold: float = 0.7,
        feature_dim: int = 512,
    ) -> None:
        if not 0.5 <= learned_threshold <= 1.0:
            raise ValueError("learned_threshold must be between 0.5 and 1.0")
        self.classifier = FixedShapeLinearClassifier(
            entity_ids,
            component_kind="entity_alias_linker",
            feature_dim=feature_dim,
        )
        self.learned_threshold = learned_threshold
        self._aliases: dict[str, str] = {}

    def fit(self, examples: Sequence[AliasExample]) -> None:
        aliases: dict[str, str] = {}
        for example in examples:
            key = _normalized_text(example.alias)
            previous = aliases.get(key)
            if previous is not None and previous != example.entity_id:
                raise ValueError(f"ambiguous training alias: {example.alias}")
            aliases[key] = example.entity_id
        self._aliases = aliases
        self.classifier.fit_text(
            [TextExample(text=example.alias, label=example.entity_id) for example in examples]
        )

    def link(
        self,
        surface: str,
        *,
        source_text: str | None = None,
        char_start: int = 0,
    ) -> EntityLinkPrediction:
        raw = source_text if source_text is not None else surface
        char_end = char_start + len(surface)
        if char_start < 0 or char_end > len(raw) or raw[char_start:char_end] != surface:
            raise ValueError("surface must align exactly to source_text at char_start")
        exact = self._aliases.get(_normalized_text(surface))
        if exact is not None:
            return EntityLinkPrediction(
                entity_id=exact,
                confidence=1.0,
                method="exact_alias",
                unknown_span=None,
                scores=((exact, 1.0),),
            )
        prediction = self.classifier.predict_text(surface)
        if prediction.confidence >= self.learned_threshold:
            return EntityLinkPrediction(
                entity_id=prediction.label,
                confidence=prediction.confidence,
                method="learned_fallback",
                unknown_span=None,
                scores=prediction.scores,
            )
        return EntityLinkPrediction(
            entity_id=None,
            confidence=1.0 - prediction.confidence,
            method="unknown_copy",
            unknown_span=_copy_span(raw, char_start, char_end),
            scores=prediction.scores,
        )

    def export_int8(self) -> QuantizedLinearArtifact:
        return self.classifier.export_int8()

    def profile(self, surface: str) -> InferenceProfile:
        return self.classifier.profile_text(surface)


class EvidenceReranker:
    """Binary query/evidence relevance model used only to order candidates."""

    def __init__(self, *, feature_dim: int = 512) -> None:
        self.classifier = FixedShapeLinearClassifier(
            ("irrelevant", "relevant"),
            component_kind="evidence_reranker",
            feature_dim=feature_dim,
        )

    def fit(self, examples: Sequence[EvidenceExample]) -> None:
        self.classifier.fit_pairs(
            [
                PairExample(
                    left=example.query,
                    right=example.evidence,
                    label="relevant" if example.relevant else "irrelevant",
                )
                for example in examples
            ]
        )

    def score(self, query: str, evidence: str) -> float:
        result = self.classifier.predict_pair(query, evidence)
        return dict(result.scores)["relevant"]

    def rank(
        self,
        query: str,
        candidates: Sequence[EvidenceCandidate],
    ) -> tuple[RankedEvidence, ...]:
        scored = [
            (candidate.candidate_id, self.score(query, candidate.text))
            for candidate in candidates
        ]
        scored.sort(key=lambda item: (-item[1], item[0]))
        return tuple(
            RankedEvidence(candidate_id=candidate_id, score=score, rank=index + 1)
            for index, (candidate_id, score) in enumerate(scored)
        )

    def export_int8(self) -> QuantizedLinearArtifact:
        return self.classifier.export_int8()

    def profile(self, query: str, evidence: str) -> InferenceProfile:
        return self.classifier.profile_pair(query, evidence)


class _BinaryProbe:
    def __init__(self, *, component_kind: str, feature_dim: int) -> None:
        self.classifier = FixedShapeLinearClassifier(
            ("negative", "positive"),
            component_kind=component_kind,
            feature_dim=feature_dim,
        )

    def fit(self, examples: Sequence[ProbeExample]) -> None:
        self.classifier.fit_pairs(
            [
                PairExample(
                    left=example.left,
                    right=example.right,
                    label="positive" if example.positive else "negative",
                )
                for example in examples
            ]
        )

    def predict(self, left: str, right: str) -> ProbePrediction:
        result = self.classifier.predict_pair(left, right)
        positive_probability = dict(result.scores)["positive"]
        return ProbePrediction(
            detected=result.label == "positive",
            confidence=result.confidence,
            positive_probability=positive_probability,
        )

    def export_int8(self) -> QuantizedLinearArtifact:
        return self.classifier.export_int8()

    def profile(self, left: str, right: str) -> InferenceProfile:
        return self.classifier.profile_pair(left, right)


class ContradictionProbe(_BinaryProbe):
    """Learned pair probe; canonical decisions still require deterministic checks."""

    def __init__(self, *, feature_dim: int = 512) -> None:
        super().__init__(component_kind="contradiction_probe", feature_dim=feature_dim)


class EvidenceGapProbe(_BinaryProbe):
    """Predicts whether selected evidence leaves the requested answer unsupported."""

    def __init__(self, *, feature_dim: int = 512) -> None:
        super().__init__(component_kind="evidence_gap_probe", feature_dim=feature_dim)


def _copy_span(text: str, char_start: int, char_end: int) -> UnknownSpanCopy:
    surface = text[char_start:char_end]
    byte_start = len(text[:char_start].encode("utf-8"))
    byte_end = byte_start + len(surface.encode("utf-8"))
    return UnknownSpanCopy(
        surface=surface,
        char_start=char_start,
        char_end=char_end,
        byte_start=byte_start,
        byte_end=byte_end,
    )


def _classification(labels: Sequence[str], scores: Sequence[float]) -> Classification:
    probabilities = _softmax(scores)
    best_index = max(range(len(labels)), key=lambda index: (probabilities[index], -index))
    return Classification(
        label=labels[best_index],
        confidence=probabilities[best_index],
        scores=tuple(
            (label, probabilities[index])
            for index, label in enumerate(labels)
        ),
    )


def _softmax(scores: Sequence[float]) -> tuple[float, ...]:
    maximum = max(scores)
    exponentials = [math.exp(score - maximum) for score in scores]
    denominator = sum(exponentials)
    return tuple(value / denominator for value in exponentials)


def _symmetric_int8(value: float) -> int:
    return max(-127, min(127, round(value)))
