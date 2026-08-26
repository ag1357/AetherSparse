"""Grounded live self/capability state; no hardware identity lives in policy weights."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .tools import ToolKind


class RuntimeMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    value: int | float | str
    unit: str = ""
    source: str


class OperationalSelfModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: str = "aethercore.self.v1"
    build_version: str
    source_identity: str
    runtime_abi: str
    cog_schema: str
    memory_schema: str
    hardware_class: str
    available_memory_bytes: int = Field(ge=0)
    storage_identity: str
    active_pack_identities: tuple[str, ...] = Field(default=(), max_length=64)
    cache_profile: str
    available_tools: tuple[ToolKind, ...] = Field(default=(), max_length=64)
    available_specialists: tuple[str, ...] = Field(default=(), max_length=64)
    unavailable_capabilities: tuple[str, ...] = Field(default=(), max_length=64)
    permissions: tuple[str, ...] = Field(default=(), max_length=64)
    active_transport: str
    service_status: str
    measured_metrics: tuple[RuntimeMetric, ...] = Field(default=(), max_length=64)
    bottleneck_classification: str

    def supports_tool(self, tool: ToolKind) -> bool:
        return tool in self.available_tools


def host_capability_model(source_identity: str) -> OperationalSelfModel:
    return OperationalSelfModel(
        build_version="15.0-work-candidate",
        source_identity=source_identity,
        runtime_abi="aethercore-c-abi.v15",
        cog_schema="aethercore.cog.v1",
        memory_schema="aethercore.operational-state.v1",
        hardware_class="HOST",
        available_memory_bytes=0,
        storage_identity="host-filesystem:observed-at-runtime",
        cache_profile="HOST",
        available_tools=tuple(ToolKind),
        available_specialists=("semantic-address-v2", "cog-controller-v14"),
        unavailable_capabilities=("physical-actuation",),
        permissions=("sandbox-write", "build", "test", "integration-request-only"),
        active_transport="IN_PROCESS_OR_CONFIGURED",
        service_status="READY",
        bottleneck_classification="STORAGE_PROFILE_DEPENDENT",
    )


def accessory_p4_capability_model(source_identity: str) -> OperationalSelfModel:
    """Only capabilities physically present in the qualified P4 runtime are exposed."""

    return OperationalSelfModel(
        build_version="15.0-work-candidate",
        source_identity=source_identity,
        runtime_abi="aethercore-c-abi.v15",
        cog_schema="aethercore.cog.v1",
        memory_schema="aethercore.operational-state.v1",
        hardware_class="WAVESHARE_ESP32_P4_WIFI6_ACCESSORY_SKU_32020",
        available_memory_bytes=32 * 1024 * 1024,
        storage_identity="removable-microsd:runtime-observed",
        cache_profile="P4_PERFORMANCE",
        available_tools=(ToolKind.SEARCH_KNOWLEDGE, ToolKind.REPORT_RESULT),
        available_specialists=("semantic-address-v2", "cog-controller-v14"),
        unavailable_capabilities=(
            "host-worktree",
            "host-build",
            "host-test-runner",
            "automatic-integration",
        ),
        permissions=("knowledge-read", "session-persist", "integration-request-only"),
        active_transport="CONFIGURED_BY_FACTORY",
        service_status="READY_FOR_FACTORY",
        measured_metrics=(
            RuntimeMetric(
                name="physical_v14_resident",
                value=2_060_000,
                unit="bytes",
                source="v14-p4 qualification",
            ),
            RuntimeMetric(
                name="physical_v14_policy_p50",
                value=638,
                unit="us",
                source="v14-p4 qualification",
            ),
        ),
        bottleneck_classification="P4_RETAIN_STORAGE_UPGRADE",
    )
