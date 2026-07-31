"""Estimated target profiles over measured host operation traces."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from aethersparse.models import CostSummary

ROOT = Path(__file__).resolve().parents[2]
PROFILE_DIR = ROOT / "hardware_profiles"


class HardwareProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str
    reasoning_backend: bool
    estimated: bool
    integer_mops: float
    sequential_read_mb_s: float
    fixed_request_overhead_ms: float
    active_power_w: float
    notes: str


class HardwareEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str
    estimated: bool
    latency_ms: float
    energy_mj: float
    peak_working_ram_bytes: int
    bytes_read: int
    storage_reads: int


def load_profiles(profile_dir: Path = PROFILE_DIR) -> tuple[HardwareProfile, ...]:
    profiles = []
    for path in sorted(profile_dir.glob("*.json")):
        profiles.append(HardwareProfile.model_validate_json(path.read_text(encoding="utf-8")))
    return tuple(profiles)


def estimate_hardware(
    cost: CostSummary,
    profiles: tuple[HardwareProfile, ...] | None = None,
) -> tuple[HardwareEstimate, ...]:
    result: list[HardwareEstimate] = []
    for profile in profiles or load_profiles():
        if not profile.reasoning_backend:
            continue
        compute_ms = cost.integer_ops / (profile.integer_mops * 1_000_000) * 1000
        storage_ms = cost.bytes_read / (profile.sequential_read_mb_s * 1_000_000) * 1000
        latency_ms = profile.fixed_request_overhead_ms + compute_ms + storage_ms
        result.append(
            HardwareEstimate(
                profile_id=profile.profile_id,
                estimated=profile.estimated,
                latency_ms=round(latency_ms, 4),
                energy_mj=round(latency_ms * profile.active_power_w, 4),
                peak_working_ram_bytes=cost.peak_working_ram_bytes,
                bytes_read=cost.bytes_read,
                storage_reads=cost.storage_reads,
            )
        )
    return tuple(sorted(result, key=lambda item: item.latency_ms))


def estimates_as_json(cost: CostSummary) -> list[dict[str, Any]]:
    return [estimate.model_dump(mode="json") for estimate in estimate_hardware(cost)]
