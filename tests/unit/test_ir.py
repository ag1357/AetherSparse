from __future__ import annotations

from aethersparse.ir import estimate_hardware, load_profiles
from aethersparse.models import CostSummary


def test_hardware_profiles_are_estimates_and_exclude_touchscreen_backend() -> None:
    profiles = load_profiles()

    assert len(profiles) == 6
    assert all(profile.estimated for profile in profiles)
    assert not any(
        "waveshare" in profile.profile_id.casefold() and profile.reasoning_backend
        for profile in profiles
    )
    assert any(
        profile.profile_id == "max78002_specialist_only" and not profile.reasoning_backend
        for profile in profiles
    )


def test_estimates_preserve_measured_cost_shape() -> None:
    cost = CostSummary(
        operation_count=6,
        bytes_read=1024,
        storage_reads=1,
        integer_ops=5000,
        peak_working_ram_bytes=4096,
        measured_host_latency_us=250,
    )
    estimates = estimate_hardware(cost)

    assert estimates
    assert all(estimate.estimated for estimate in estimates)
    assert all(estimate.bytes_read == 1024 for estimate in estimates)
    assert all(estimate.peak_working_ram_bytes == 4096 for estimate in estimates)

