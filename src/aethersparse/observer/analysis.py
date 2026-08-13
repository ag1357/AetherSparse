"""Deterministic, dependency-free analysis for sampled AetherCore telemetry."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

from aethersparse.observer.models import (
    CausalAttribution,
    CounterfactualRecord,
    HiddenStateSummary,
    TelemetryRecord,
)


def activation_histogram(values: Sequence[float], *, bins: int = 20) -> dict[str, Any]:
    if bins < 1:
        raise ValueError("histogram bins must be positive")
    if not values:
        return {"minimum": 0.0, "maximum": 0.0, "edges": [], "counts": []}
    low = min(values)
    high = max(values)
    if low == high:
        return {"minimum": low, "maximum": high, "edges": [low, high], "counts": [len(values)]}
    width = (high - low) / bins
    counts = [0] * bins
    for value in values:
        index = min(bins - 1, int((value - low) / width))
        counts[index] += 1
    return {
        "minimum": low,
        "maximum": high,
        "edges": [low + index * width for index in range(bins + 1)],
        "counts": counts,
    }


def _hidden_by_module(
    records: Iterable[TelemetryRecord],
) -> dict[str, list[HiddenStateSummary]]:
    groups: dict[str, list[HiddenStateSummary]] = defaultdict(list)
    for record in records:
        for cycle in record.cycles:
            for expert in cycle.experts:
                if expert.hidden_state is not None:
                    groups[expert.module_id].append(expert.hidden_state)
    return groups


def hidden_state_diagnostics(
    records: Iterable[TelemetryRecord],
    *,
    dead_unit_alert: float = 0.5,
    saturation_alert: float = 0.5,
) -> dict[str, Any]:
    """Summarize activation scale, dead units, and saturation by module."""

    report: dict[str, Any] = {}
    for module_id, summaries in sorted(_hidden_by_module(records).items()):
        count = len(summaries)
        dead = sum(item.dead_unit_fraction for item in summaries) / count
        saturated = sum(item.saturation_fraction for item in summaries) / count
        selected = [value for item in summaries for value in item.selected_activation]
        report[module_id] = {
            "sample_count": count,
            "mean_of_means": sum(item.mean for item in summaries) / count,
            "mean_variance": sum(item.variance for item in summaries) / count,
            "mean_l2_norm": sum(item.l2_norm for item in summaries) / count,
            "mean_dead_unit_fraction": dead,
            "mean_saturation_fraction": saturated,
            "dead_unit_alert": dead >= dead_unit_alert,
            "saturation_alert": saturated >= saturation_alert,
            "selected_activation_histogram": activation_histogram(selected),
        }
    return report


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def _norm(vector: Sequence[float]) -> float:
    return math.sqrt(_dot(vector, vector))


def _matvec(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> list[float]:
    return [_dot(row, vector) for row in matrix]


def pca_svd(
    vectors: Sequence[Sequence[float]], *, components: int = 2, iterations: int = 64
) -> dict[str, Any]:
    """Run deterministic PCA via covariance power iteration and deflation."""

    if components < 1 or iterations < 1:
        raise ValueError("PCA components and iterations must be positive")
    if not vectors:
        return {"sample_count": 0, "dimension": 0, "components": [], "singular_values": []}
    dimension = len(vectors[0])
    if dimension == 0 or any(len(vector) != dimension for vector in vectors):
        raise ValueError("PCA vectors must share one non-zero dimension")
    means = [sum(row[column] for row in vectors) / len(vectors) for column in range(dimension)]
    centered = [[row[column] - means[column] for column in range(dimension)] for row in vectors]
    denominator = max(1, len(centered) - 1)
    covariance = [
        [sum(row[i] * row[j] for row in centered) / denominator for j in range(dimension)]
        for i in range(dimension)
    ]
    axes: list[list[float]] = []
    eigenvalues: list[float] = []
    working = [row[:] for row in covariance]
    for component_index in range(min(components, dimension)):
        vector = [1.0 + (index == component_index) for index in range(dimension)]
        vector_norm = _norm(vector)
        vector = [value / vector_norm for value in vector]
        for _ in range(iterations):
            candidate = _matvec(working, vector)
            candidate_norm = _norm(candidate)
            if candidate_norm <= 1e-15:
                break
            vector = [value / candidate_norm for value in candidate]
        eigenvalue = max(0.0, _dot(vector, _matvec(working, vector)))
        if eigenvalue <= 1e-15:
            break
        axes.append(vector)
        eigenvalues.append(eigenvalue)
        for row_index in range(dimension):
            for column_index in range(dimension):
                working[row_index][column_index] -= (
                    eigenvalue * vector[row_index] * vector[column_index]
                )
    projections = [[_dot(row, axis) for axis in axes] for row in centered]
    singular_values = [math.sqrt(value * denominator) for value in eigenvalues]
    total_variance = sum(covariance[index][index] for index in range(dimension))
    return {
        "sample_count": len(vectors),
        "dimension": dimension,
        "means": means,
        "components": axes,
        "singular_values": singular_values,
        "explained_variance_ratio": [
            value / total_variance if total_variance else 0.0 for value in eigenvalues
        ],
        "projections": projections,
    }


def hidden_state_clustering(
    vectors: Sequence[Sequence[float]], *, clusters: int = 3, iterations: int = 50
) -> dict[str, Any]:
    """Deterministic farthest-first k-means for compact selected activations."""

    if clusters < 1 or iterations < 1:
        raise ValueError("cluster count and iterations must be positive")
    if not vectors:
        return {"sample_count": 0, "cluster_count": 0, "centroids": [], "assignments": []}
    dimension = len(vectors[0])
    if dimension == 0 or any(len(vector) != dimension for vector in vectors):
        raise ValueError("cluster vectors must share one non-zero dimension")
    rows = [list(map(float, vector)) for vector in vectors]
    count = min(clusters, len(rows))
    centroids = [rows[0][:]]
    while len(centroids) < count:
        next_index = max(
            range(len(rows)),
            key=lambda index: min(
                sum((left - right) ** 2 for left, right in zip(rows[index], center, strict=True))
                for center in centroids
            ),
        )
        centroids.append(rows[next_index][:])
    assignments = [-1] * len(rows)
    for _ in range(iterations):
        updated = [
            min(
                range(count),
                key=lambda cluster: sum(
                    (left - right) ** 2
                    for left, right in zip(rows[index], centroids[cluster], strict=True)
                ),
            )
            for index in range(len(rows))
        ]
        if updated == assignments:
            break
        assignments = updated
        for cluster in range(count):
            members = [
                rows[index]
                for index, assigned in enumerate(assignments)
                if assigned == cluster
            ]
            if members:
                centroids[cluster] = [
                    sum(row[column] for row in members) / len(members)
                    for column in range(dimension)
                ]
    sizes = Counter(assignments)
    return {
        "sample_count": len(rows),
        "cluster_count": count,
        "centroids": centroids,
        "assignments": assignments,
        "cluster_sizes": {str(key): sizes[key] for key in sorted(sizes)},
    }


def expert_utilization(records: Iterable[TelemetryRecord]) -> dict[str, Any]:
    rows = tuple(records)
    case_counts: Counter[str] = Counter()
    cycle_counts: Counter[str] = Counter()
    all_experts: set[str] = set()
    coactivation: Counter[tuple[str, str]] = Counter()
    active_expert_counts: list[int] = []
    for record in rows:
        case_active: set[str] = set()
        case_activation_count = 0
        for cycle in record.cycles:
            active = sorted(cycle.active_experts)
            case_activation_count += len(active)
            all_experts.update(active)
            case_active.update(active)
            cycle_counts.update(active)
            for left_index, left in enumerate(active):
                for right in active[left_index:]:
                    coactivation[(left, right)] += 1
        case_counts.update(case_active)
        active_expert_counts.append(case_activation_count)
    experts = sorted(all_experts)
    return {
        "case_count": len(rows),
        "experts": experts,
        "cases_active": dict(sorted(case_counts.items())),
        "cycles_active": dict(sorted(cycle_counts.items())),
        "mean_active_expert_activations_per_case": (
            sum(active_expert_counts) / len(active_expert_counts)
            if active_expert_counts
            else 0.0
        ),
        "p95_active_expert_activations_per_case": _percentile(active_expert_counts, 0.95),
        "coactivation_matrix": {
            left: {
                right: coactivation[(left, right) if left <= right else (right, left)]
                for right in experts
            }
            for left in experts
        },
    }


def _confidence(record: TelemetryRecord) -> float:
    values = [
        expert.confidence
        for cycle in record.cycles
        for expert in cycle.experts
        if expert.active
    ]
    return max(values, default=0.0)


def _binned_correctness(
    values: Sequence[tuple[float, bool]], *, bins: int
) -> list[dict[str, float | int]]:
    if not values:
        return []
    low = min(value for value, _correct in values)
    high = max(value for value, _correct in values)
    if low == high:
        return [
            {
                "lower": low,
                "upper": high,
                "count": len(values),
                "mean_value": low,
                "accuracy": sum(correct for _value, correct in values) / len(values),
            }
        ]
    width = (high - low) / bins
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for value, correct in values:
        buckets[min(bins - 1, int((value - low) / width))].append((value, correct))
    return [
        {
            "lower": low + index * width,
            "upper": low + (index + 1) * width,
            "count": len(bucket),
            "mean_value": sum(value for value, _correct in bucket) / len(bucket),
            "accuracy": sum(correct for _value, correct in bucket) / len(bucket),
        }
        for index, bucket in enumerate(buckets)
        if bucket
    ]


def _risk_coverage(records: Sequence[TelemetryRecord]) -> list[dict[str, float | int]]:
    ranked = sorted(records, key=_confidence, reverse=True)
    points: list[dict[str, float | int]] = []
    for retained in range(1, len(ranked) + 1):
        selected = ranked[:retained]
        accuracy = sum(record.final_semantic_correctness for record in selected) / retained
        points.append(
            {
                "retained_cases": retained,
                "coverage": retained / len(ranked),
                "risk": 1.0 - accuracy,
                "minimum_confidence": _confidence(selected[-1]),
            }
        )
    return points


def uncertainty_calibration(
    records: Iterable[TelemetryRecord], *, bins: int = 10
) -> dict[str, Any]:
    rows = tuple(records)
    if bins < 1:
        raise ValueError("calibration bins must be positive")
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for record in rows:
        confidence = _confidence(record)
        buckets[min(bins - 1, int(confidence * bins))].append(
            (confidence, record.final_semantic_correctness)
        )
    reliability = []
    ece = 0.0
    for index, bucket in enumerate(buckets):
        if not bucket:
            continue
        mean_confidence = sum(item[0] for item in bucket) / len(bucket)
        accuracy = sum(item[1] for item in bucket) / len(bucket)
        ece += len(bucket) / max(1, len(rows)) * abs(mean_confidence - accuracy)
        reliability.append(
            {
                "lower": index / bins,
                "upper": (index + 1) / bins,
                "count": len(bucket),
                "mean_confidence": mean_confidence,
                "accuracy": accuracy,
            }
        )
    probabilities = [_confidence(record) for record in rows]
    labels = [float(record.final_semantic_correctness) for record in rows]
    entropy = [
        (
            max(max(cycle.entropy_before, cycle.entropy_after) for cycle in record.cycles),
            record.final_semantic_correctness,
        )
        for record in rows
    ]
    disagreement = [
        (
            max(
                max(cycle.disagreement_before, cycle.disagreement_after)
                for cycle in record.cycles
            ),
            record.final_semantic_correctness,
        )
        for record in rows
    ]
    epsilon = 1e-12
    return {
        "case_count": len(rows),
        "ece": ece,
        "brier_score": (
            sum(
                (probability - label) ** 2
                for probability, label in zip(probabilities, labels, strict=True)
            )
            / len(rows)
            if rows
            else 0.0
        ),
        "nll": (
            -sum(
                label * math.log(max(epsilon, probability))
                + (1.0 - label) * math.log(max(epsilon, 1.0 - probability))
                for probability, label in zip(probabilities, labels, strict=True)
            )
            / len(rows)
            if rows
            else 0.0
        ),
        "reliability": reliability,
        "risk_coverage": _risk_coverage(rows),
        "entropy_vs_correctness": _binned_correctness(entropy, bins=bins),
        "disagreement_vs_correctness": _binned_correctness(disagreement, bins=bins),
    }


def _percentile(values: Sequence[int], proportion: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return float(ordered[max(0, math.ceil(proportion * len(ordered)) - 1)])


def route_analysis(records: Iterable[TelemetryRecord]) -> dict[str, Any]:
    groups: dict[str, list[TelemetryRecord]] = defaultdict(list)
    for record in records:
        groups[record.route_sha256].append(record)
    routes: dict[str, Any] = {}
    for route_hash, rows in sorted(groups.items()):
        macs = [sum(cycle.active_macs for cycle in row.cycles) for row in rows]
        parameter_executions = [
            sum(cycle.active_parameter_count for cycle in row.cycles) for row in rows
        ]
        depths = [len(row.cycles) for row in rows]
        tier_accuracy = {
            tier: (
                sum(row.final_semantic_correctness for row in rows if row.tier == tier)
                / sum(row.tier == tier for row in rows)
            )
            for tier in sorted({row.tier for row in rows})
        }
        routes[route_hash] = {
            "signature": rows[0].route_signature,
            "case_count": len(rows),
            "correct_count": sum(row.final_semantic_correctness for row in rows),
            "semantic_accuracy": sum(row.final_semantic_correctness for row in rows) / len(rows),
            "provenance_accuracy": (
                sum(row.final_provenance_correctness for row in rows) / len(rows)
            ),
            "mean_active_macs": sum(macs) / len(macs),
            "p95_active_macs": _percentile(macs, 0.95),
            "mean_active_parameter_executions": (
                sum(parameter_executions) / len(parameter_executions)
            ),
            "p95_active_parameter_executions": _percentile(parameter_executions, 0.95),
            "mean_depth": sum(depths) / len(depths),
            "tiers": dict(sorted(Counter(row.tier for row in rows).items())),
            "tier_semantic_accuracy": tier_accuracy,
            "tier_accuracy_range": (
                max(tier_accuracy.values()) - min(tier_accuracy.values())
                if tier_accuracy
                else 0.0
            ),
            "novel_case_count": sum("novel_route" in row.sampled_because for row in rows),
        }
    return routes


def routing_signature_clustering(
    records: Iterable[TelemetryRecord], *, clusters: int = 3
) -> dict[str, Any]:
    rows = tuple(records)
    route_groups: dict[str, list[TelemetryRecord]] = defaultdict(list)
    experts = sorted(
        {
            expert
            for record in rows
            for cycle in record.cycles
            for expert in cycle.active_experts
        }
    )
    for record in rows:
        route_groups[record.route_sha256].append(record)
    hashes = sorted(route_groups)
    vectors: list[list[float]] = []
    for route_hash in hashes:
        route_rows = route_groups[route_hash]
        active_counts = Counter(
            expert
            for row in route_rows
            for cycle in row.cycles
            for expert in cycle.active_experts
        )
        cycle_total = sum(len(row.cycles) for row in route_rows)
        vectors.append(
            [active_counts[expert] / max(1, cycle_total) for expert in experts]
            + [sum(len(row.cycles) for row in route_rows) / len(route_rows)]
        )
    clustered: dict[str, Any]
    if vectors:
        clustered = hidden_state_clustering(vectors, clusters=clusters)
    else:
        clustered = {
            "sample_count": 0,
            "cluster_count": 0,
            "centroids": [],
            "assignments": [],
            "cluster_sizes": {},
        }
    assignments: list[int] = clustered["assignments"]
    clustered["routes"] = [
        {"route_sha256": route_hash, "cluster": assignments[index]}
        for index, route_hash in enumerate(hashes)
    ]
    clustered["feature_order"] = [*experts, "depth"]
    return clustered


def depth_distribution(records: Iterable[TelemetryRecord]) -> dict[str, Any]:
    rows = tuple(records)
    depths = [len(record.cycles) for record in rows]
    macs = [sum(cycle.active_macs for cycle in record.cycles) for record in rows]
    parameter_executions = [
        sum(cycle.active_parameter_count for cycle in record.cycles) for record in rows
    ]
    return {
        "case_count": len(rows),
        "counts": {str(key): value for key, value in sorted(Counter(depths).items())},
        "mean_cycles": sum(depths) / len(depths) if depths else 0.0,
        "p95_cycles": _percentile(depths, 0.95),
        "mean_active_macs": sum(macs) / len(macs) if macs else 0.0,
        "p95_active_macs": _percentile(macs, 0.95),
        "mean_active_parameter_executions": (
            sum(parameter_executions) / len(parameter_executions)
            if parameter_executions
            else 0.0
        ),
        "p95_active_parameter_executions": _percentile(parameter_executions, 0.95),
    }


def counterfactual_analysis(records: Iterable[CounterfactualRecord]) -> dict[str, Any]:
    """Summarize causal diagnoses without converting correlation into reward."""

    rows = tuple(records)
    counts = Counter(record.attribution.value for record in rows)
    under_deep = sorted(
        {
            record.actual.route_signature
            for record in rows
            if record.attribution is CausalAttribution.INSUFFICIENT_DEPTH
        }
    )
    over_deep = sorted(
        {
            record.actual.route_signature
            for record in rows
            if record.attribution is CausalAttribution.EXCESSIVE_DEPTH
        }
    )
    improvements = [record for record in rows if record.correctness_delta > 0]
    return {
        "record_count": len(rows),
        "attribution_counts": dict(sorted(counts.items())),
        "causal_improvement_count": len(improvements),
        "mean_mac_delta_for_improvements": (
            sum(record.mac_delta for record in improvements) / len(improvements)
            if improvements
            else 0.0
        ),
        "under_deep_signatures": under_deep,
        "over_deep_signatures": over_deep,
    }


def selected_hidden_vectors(records: Iterable[TelemetryRecord]) -> dict[str, list[list[float]]]:
    values: dict[str, list[list[float]]] = defaultdict(list)
    dimensions: dict[str, int] = {}
    for module_id, summaries in _hidden_by_module(records).items():
        for summary in summaries:
            vector = list(summary.selected_activation)
            if not vector:
                continue
            if module_id not in dimensions:
                dimensions[module_id] = len(vector)
            if len(vector) == dimensions[module_id]:
                values[module_id].append(vector)
    return dict(values)


def analyze_records(records: Iterable[TelemetryRecord]) -> dict[str, Any]:
    """Produce the required route/expert/depth/calibration diagnostic bundle."""

    rows = tuple(records)
    hidden = selected_hidden_vectors(rows)
    return {
        "schema_version": "aethercore.observer-analysis.v1",
        "case_count": len(rows),
        "hidden_state_diagnostics": hidden_state_diagnostics(rows),
        "hidden_state_pca_svd": {
            module_id: pca_svd(vectors) for module_id, vectors in sorted(hidden.items())
        },
        "hidden_state_clusters": {
            module_id: hidden_state_clustering(vectors)
            for module_id, vectors in sorted(hidden.items())
        },
        "routing_signature_clusters": routing_signature_clustering(rows),
        "expert_utilization": expert_utilization(rows),
        "depth_distribution": depth_distribution(rows),
        "uncertainty_calibration": uncertainty_calibration(rows),
        "correctness_and_compute_by_route": route_analysis(rows),
    }
