"""Bounded hidden-state capture for sampled observer records."""

from __future__ import annotations

import math
from collections.abc import Sequence

from aethersparse.observer.models import HiddenStateSummary


def summarize_hidden_state(
    values: Sequence[float],
    *,
    saturation_threshold: float = 0.99,
    dead_threshold: float = 1e-8,
    selected_indices: Sequence[int] = (),
) -> HiddenStateSummary:
    """Summarize a vector and retain only explicitly selected dimensions.

    Full vectors are never retained by default.  At most 256 selected values
    may cross the observer boundary.
    """

    if saturation_threshold <= 0.0:
        raise ValueError("saturation threshold must be positive")
    if dead_threshold < 0.0:
        raise ValueError("dead threshold cannot be negative")
    if len(selected_indices) > 256:
        raise ValueError("at most 256 selected activations may be retained")
    if len(set(selected_indices)) != len(selected_indices):
        raise ValueError("selected activation indices must be unique")
    if any(index < 0 or index >= len(values) for index in selected_indices):
        raise ValueError("selected activation index is out of bounds")

    vector = tuple(float(value) for value in values)
    if not vector:
        return HiddenStateSummary(
            dimension=0,
            mean=0.0,
            variance=0.0,
            l2_norm=0.0,
            saturation_fraction=0.0,
            dead_unit_fraction=0.0,
        )
    mean = sum(vector) / len(vector)
    variance = sum((value - mean) ** 2 for value in vector) / len(vector)
    return HiddenStateSummary(
        dimension=len(vector),
        mean=mean,
        variance=variance,
        l2_norm=math.sqrt(sum(value * value for value in vector)),
        saturation_fraction=(
            sum(abs(value) >= saturation_threshold for value in vector) / len(vector)
        ),
        dead_unit_fraction=sum(abs(value) <= dead_threshold for value in vector) / len(vector),
        selected_activation=tuple(vector[index] for index in selected_indices),
    )
