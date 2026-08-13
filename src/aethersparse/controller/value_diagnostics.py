"""Versioned compiler/runtime value-boundary telemetry contracts."""

from __future__ import annotations

from pydantic import Field

from aethersparse.controller.models import AnswerShape, FrozenModel


class EnumerationRegionTrace(FrozenModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str
    score: int
    rank: int = Field(ge=1)
    selected_top8: bool


class EnumerationMatchTrace(FrozenModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    surface: str
    value_shape: AnswerShape
    speaker: str | None = None
    unit: str | None = None
    region_rank: int | None = Field(default=None, ge=1)
    selected_top8: bool
    exact_surface_bound: bool


class RuntimeEnumerationDiagnostic(FrozenModel):
    """A complete trace through the current runtime extraction boundary."""

    schema_version: str = "aethersparse.value-runtime-boundary.v11"
    regions: tuple[EnumerationRegionTrace, ...]
    all_matches_before_region_pruning: tuple[EnumerationMatchTrace, ...]
    top8_matches_before_deduplication: tuple[EnumerationMatchTrace, ...]
    pre_dedup_values: tuple[str, ...]
    post_dedup_values: tuple[str, ...]
    pre_cap_values: tuple[str, ...]
    post_cap_values: tuple[str, ...]
    region_cap: int = Field(default=8, ge=1)
    value_cap: int = Field(default=4, ge=1)
