"""Autonomous architecture-qualification components.

This package is intentionally separate from the frozen Phase 0 implementation.
"""

from aethersparse.autonomy.extraction import (
    AdjudicationDecision,
    AdjudicationResult,
    IndependentAdjudicator,
    IndependentExtractor,
    IndependentValidator,
)
from aethersparse.autonomy.synthetic import (
    DEBUG_SCALE,
    DECISIVE_SCALE,
    INTERMEDIATE_SCALE,
    ScaleConfig,
    SyntheticWorld,
    generate_partition_pair,
    generate_world,
)

__all__ = [
    "DEBUG_SCALE",
    "DECISIVE_SCALE",
    "INTERMEDIATE_SCALE",
    "AdjudicationDecision",
    "AdjudicationResult",
    "IndependentAdjudicator",
    "IndependentExtractor",
    "IndependentValidator",
    "ScaleConfig",
    "SyntheticWorld",
    "generate_partition_pair",
    "generate_world",
]
