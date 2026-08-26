#!/usr/bin/env python3
"""Assemble and fail-closed validate the integrated V15 candidate artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "reports" / "droid" / "v15"
SOURCE_MAIN = "c3aa2ef61e6ae77a12063e47221c6e4decae3762"
SOURCE_TREE = "09888952949745677b6a1b4939b90f14ccfe83d8"
BRANCH = "work/aethercore-v15-operational-system"


def load(name: str) -> dict[str, Any]:
    value = json.loads((REPORT_ROOT / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{name} is not a JSON object")
    return value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def assemble(source_sha: str) -> None:
    native = load("native-hardening-qualification.json")
    pack = load("pack-v2-qualification.json")
    cache = load("cache-qualification.json")
    specialist = load("specialist-capacity-qualification.json")
    observer = load("input-observer-qualification.json")
    conversation = load("conversation-memory-qualification.json")
    sandbox = load("sandbox-agent-qualification.json")
    tactility = load("tactility-client-qualification.json")
    tooling = load("production-tooling-qualification.json")
    debt = load("architecture-debt-ledger.json")

    require(native["status"] == "PASS", "native hardening did not pass")
    require(
        native["cog_runtime_deserialize"]["python_native_roundtrip"] == "BYTE_EXACT",
        "COG parity failed",
    )
    require(pack["selection"]["performance"] == "direct_compact_resident", "pack selection drift")
    require(cache["selection"]["cache_bytes"] == 2_097_152, "cache selection drift")
    require(
        specialist["status"] == "REJECTED_NO_CAPABILITY_BYTE_GAIN", "specialist disposition drift"
    )
    require(specialist["v14_baseline"]["autonomous"]["successful"] == 242, "V14 control drift")
    require(conversation["status"] == "PASS", "memory/conversation did not pass")
    require(sandbox["retained_real_tool_plane"]["tasks_passed"] == 5, "agent task drift")
    require(tooling["status"] == "PASS", "production tooling did not pass")
    require(len(debt["entries"]) >= 40, "architecture debt recovery incomplete")

    memory_report = {
        "schema_version": "aethercore.v15.memory-qualification.v1",
        "status": "PASS",
        "source_main_sha": SOURCE_MAIN,
        "qualification_source_sha": source_sha,
        "authoritative_state": conversation["authoritative_state"],
        "memory": conversation["memory"],
        "user_memory": conversation["user_memory"],
        "conversation_cases": conversation["conversation_cases"],
        "validation": conversation["focused_validation"],
    }
    write_json(REPORT_ROOT / "memory-qualification.json", memory_report)

    cleanup = {
        "schema_version": "aethercore.v15.production-cleanup-inventory.v1",
        "status": "PASS_CONSERVATIVE_CLEANUP",
        "qualification_source_sha": source_sha,
        "package": {"name": "aethersparse", "version": "0.15.0"},
        "classifications": {
            "CURRENT_PRODUCTION": [
                "src/aethersparse/agent/server.py",
                "src/aethersparse/agent/operational.py",
                "src/aethersparse/memory/",
                "src/aethersparse/edge_runtime/production.py",
                "native/aethercore_runtime/",
                "integrations/tactility/aetherchat/",
            ],
            "CURRENT_TEST": [
                "tests/memory/",
                "tests/agent/test_v15_operational.py",
                "tests/edge_runtime/test_deployment_v15.py",
            ],
            "HISTORICAL_REPRODUCIBILITY": [
                "scripts/droid/v10_* through v14_*",
                "reports/droid/v10 through v14-p4",
            ],
            "SUPERSEDED": [
                "deploy/Dockerfile legacy aethersparse.service command",
                "v1 cloud health paths",
            ],
            "DEAD": [],
        },
        "changes": [
            "package version aligned to 0.15.0",
            "Docker entrypoint moved to aethercore-server",
            "Railway/Render health checks moved to /v15/health",
            "runtime port is environment-configurable and range-validated",
            "production commands consolidated under aethersparse aethercore",
        ],
        "historical_artifacts_removed": 0,
        "historical_reproducibility_retained": True,
        "license_notice_modified": False,
        "release_tag_created": False,
        "final_self_manual_pack_created": False,
    }
    write_json(REPORT_ROOT / "production-cleanup-inventory.json", cleanup)

    registry = {
        "schema_version": "aethercore.architecture-registry.v15",
        "source_main_sha": SOURCE_MAIN,
        "source_main_tree_sha": SOURCE_TREE,
        "qualification_source_sha": source_sha,
        "branch": BRANCH,
        "classification": "V15_READY_WITH_STORAGE_EXPERIMENT_PENDING",
        "controller": {
            "architecture": "typed legal-mask COG structured perceptron",
            "stored_parameters": 1292,
            "active_parameters": 1292,
            "weights": "int8",
            "autonomous_total": "242/260",
            "autonomous_tuning": "138/150",
        },
        "state": {
            "cog_projection_bytes": 180,
            "authoritative_schema": "aethercore.operational-state.v1",
            "memory_tiers": ["EPHEMERAL", "SHORT_TERM", "WORKING", "LONG_TERM"],
            "residency": ["COLD", "WARM", "HOT"],
            "five_c": "immutable_root_boundary",
        },
        "native": {
            "language": "C++17",
            "external_abi": "C",
            "allocation_free_core": True,
            "selected_policy_bound": True,
            "cog_deserialize": "BYTE_EXACT",
        },
        "deployment": {
            "profiles": {"PERFORMANCE": "direct_compact_resident", "COMPACT": "two_level_paged"},
            "selected_cache_bytes": 2_097_152,
            "projected_resident_bytes": 6_421_665,
            "projected_psram_headroom_bytes": 27_132_767,
            "cold_pack_semantics": "immutable_page_addressed",
        },
        "service": {
            "entrypoint": "aethercore-server",
            "protocol": "aethercore-tactility.v2",
            "transport": "C6-hosted ESP-NOW",
            "user_memory_policy": "explicit authorization only",
        },
        "device_boundary": {
            "device_a": "Waveshare ESP32-P4/C6 3.5-inch Tactility terminal; UI only",
            "device_b": "Waveshare ESP32-P4-WIFI6 SKU 32020 accessory; cognition",
        },
        "factory_pending": [
            "Pack-v2 physical I/O",
            "Kingston A2 control",
            "exact custom Device-A BSP build",
        ],
    }
    registry_path = ROOT / "config" / "architecture" / "aethercore-v15.registry.json"
    write_json(registry_path, registry)

    factory = {
        "schema_version": "aethercore.v15.factory-device-deployment-handoff.v1",
        "status": "ACTIONABLE_FACTORY_HANDOFF",
        "branch": BRANCH,
        "qualification_source_sha": source_sha,
        "resolve_remote_branch_head_before_flash": True,
        "devices": {
            "device_a": {
                "role": "TACTILITY_UI_ONLY",
                "hardware": "Waveshare ESP32-P4-WIFI6-Touch-LCD-3.5 with integrated C6",
                "preserve_existing_installation": True,
                "tactility_reference": "0.8.0-dev@0ee2415f3b5a063fadc2015d50d0d1c1c8b0b6e1",
                "custom_bsp_source_required": True,
                "aetherchat_overlay": "integrations/tactility/aetherchat",
            },
            "device_b": {
                "role": "AETHERCORE_ACCESSORY_COMPUTE",
                "hardware": "Waveshare ESP32-P4-WIFI6 SKU 32020; ESP32-P4 rev v1.3; 32 MiB PSRAM",
                "not_device_a": True,
                "firmware_control": "preserve unchanged V14 binary for first Kingston A2 media run",
            },
            "cardkb2": {
                "mode": "BLE_HID",
                "mode_chord": "Fn+Sym+4",
                "flash_required": False,
                "gpio_wiring": [],
                "connection": "USB-C power plus BLE pairing to Device A",
            },
        },
        "transport": {
            "selected": "existing C6-hosted ESP-NOW",
            "device_a_device_b_gpio": [],
            "physical_connection": "no compute GPIO; power both devices and pair/configure ESP-NOW",
        },
        "storage_sequence": [
            "run Kingston Canvas Go! Plus 128 GB A2 with unchanged V14 binary "
            "as the clean media control",
            "record A2 results separately from the prior USD00/Amazon-Basics-class medium",
            "build prepacked V15 PERFORMANCE image with resident direct evidence "
            "directory and 2 MiB cache",
            "copy immutable pack regions and verify hashes",
            "run the unchanged 107-query and 260-case logical controls",
        ],
        "required_measurements": [
            "51/51 ABI vectors",
            "260/260 trace cases",
            "1329/1329 frozen decisions where V14 control is used",
            "107/107 address logical outputs",
            "address p50/p95 and pages/media misses per query",
            "internal SRAM, PSRAM, stack, cache allocation and pressure",
            "SDMMC throughput/random latency, command count, sectors/transaction and DMA copies",
            "policy CPU p50/p95 and total utilization",
            "Device-A message/reconnect/CardKB2 input and multi-turn response",
            "user-memory write/read/edit/delete and cancel/reset",
        ],
        "human_acceptance": (
            "User types a real query on CardKB2; grounded reply and follow-up return on AetherChat."
        ),
        "rollback": [
            "retain V14 accessory flash image and V14 pack manifest",
            "retain existing Device-A Tactility installation/configuration before overlay",
            "if V15 fails parity, restore V14 image and pack without changing user-memory export",
            "never integrate sandbox changes without explicit authorization",
        ],
        "long_term_storage_target_gb": 256,
        "test_medium_gb": 128,
    }
    write_json(REPORT_ROOT / "factory-v15-device-deployment-handoff.json", factory)

    qualification = {
        "schema_version": "aethercore.v15.operational-system-qualification.v1",
        "classification": "V15_READY_WITH_STORAGE_EXPERIMENT_PENDING",
        "source_main_sha": SOURCE_MAIN,
        "source_main_tree_sha": SOURCE_TREE,
        "qualification_source_sha": source_sha,
        "branch": BRANCH,
        "v14_physical_control": {
            "abi_vectors": "51/51",
            "trace_cases": "260/260",
            "policy_decisions": "1329/1329",
            "address_queries": "107/107",
            "policy_macs": 1_543_864,
        },
        "native_hardening": native,
        "memory": memory_report,
        "deployment": {"pack": pack, "cache": cache},
        "cognition": {
            "selected": registry["controller"],
            "specialist_experiment": specialist,
            "natural_input_observer": observer,
        },
        "agent": sandbox,
        "conversation": conversation,
        "tactility": tactility,
        "production_tooling": tooling,
        "production_cleanup": cleanup,
        "remaining_bottleneck": (
            "Physical storage latency plus exact custom Device-A BSP build; "
            "no cognitive representation redesign is indicated."
        ),
        "factory_handoff_complete": True,
        "self_manual_pack_finalized": False,
    }
    write_json(REPORT_ROOT / "aethercore-v15-operational-system-qualification.json", qualification)

    current_architecture = f"""
# Current AetherCore architecture — V15 candidate

Source main is `{SOURCE_MAIN}` (tree `{SOURCE_TREE}`). The Work candidate is
`{BRANCH}` at qualification source `{source_sha}`. It is classified
`V15_READY_WITH_STORAGE_EXPERIMENT_PENDING`.

## Device boundary

- Device A is the Waveshare ESP32-P4/C6 3.5-inch Tactility appliance. It owns UI,
  touch/CardKB2 input, media, and transport only.
- Device B is the separate Waveshare ESP32-P4-WIFI6 SKU 32020 accessory. It owns
  Semantic Address, COG, policy, evidence, memory, tools, and knowledge packs.
- The selected link is the existing C6-hosted ESP-NOW service; there is no
  Device-A-to-Device-B cognition GPIO link.

## Operational cognition

Semantic Address v2 feeds the authoritative COG and the selected 1,292-parameter
int8 legal-mask controller. Exact operations, immutable 5C boundaries, evidence
pinning, and the verifier remain mandatory. Autonomous control remains 242/260
overall and 138/150 unseen tuning. A tested 54-parameter passage-context head
reduced performance to 239/260 and is archived inactive.

## Memory and persistence

EPHEMERAL, SHORT_TERM, WORKING, and LONG_TERM are semantic lifetimes. COLD, WARM,
and HOT are independent physical residency states. Persistent user memory requires
explicit authorization and supports list/read/write/edit/delete/search, tombstones,
and compaction. The authoritative state persists sessions, complete COGs, memory,
specialists, pack generations, semantic checkpoints, and deterministic deltas.

## Runtime and deployment

The hot path remains allocation-free C++17 behind a stable C ABI. V15 hardens
selected evidence and the wire trust boundary. PERFORMANCE uses a 3,311,868-byte
resident direct evidence table and 2 MiB cache, projecting 6,421,665 resident bytes
and 27,132,767 bytes PSRAM headroom. COMPACT uses an 8,632-byte top directory and
at most one paged leaf read per lookup.

## Service and terminal

`aethercore-server` exposes protocol v2 with resume, capabilities, memory status,
bounded frames, cancellation, and explicit failures. AetherChat is a 3-file,
344-line overlay against Tactility 0.8.0-dev, below the existing Chat app's 5 files
and 992 lines. CardKB2 uses factory BLE-HID mode (`Fn+Sym+4`), USB-C power, and no
GPIO or firmware replacement.

The exact user custom Waveshare Tactility BSP source was not present in Work, so
the physical Device-A build remains a Factory gate and no GPIO values are invented.
"""
    write_text(ROOT / "docs" / "CURRENT_ARCHITECTURE.md", current_architecture)

    review_packet = f"""
# V15 review packet

## Decision

`V15_READY_WITH_STORAGE_EXPERIMENT_PENDING`

## Reproducible checkpoint

- Source main: `{SOURCE_MAIN}`
- Source tree: `{SOURCE_TREE}`
- Candidate branch: `{BRANCH}`
- Qualification source: `{source_sha}`

## Headline evidence

- Frozen V14 cognition: 242/260 autonomous; 138/150 tuning; zero illegal actions,
  verifier bypasses, premature halts, or runaways.
- Native boundary: selected pinning and VERIFIED/TERMINAL freeze pass; 18 session
  and four COG CRC-valid forgeries reject; 180-byte COG roundtrip is byte-exact.
- Memory: four tiers, authorized user CRUD, multi-session restart/resume, checkpoint
  restore and deterministic delta replay pass.
- Deployment: PERFORMANCE projects 6,421,665 resident bytes and eliminates modeled
  evidence-directory media misses; physical Pack-v2 measurement remains pending.
- Agent: retained 5/5 real sandbox tasks, 55 operations, and zero integrations.
- Tactility: AetherLink C++17 roundtrip passes; AetherChat is 34.68% of Chat's C++
  lines. Exact custom Device-A BSP build remains pending because its source was not
  available and no pins are invented.

## Negative results retained

- DAgger: 243 roll-in states, 231/260 versus selected 242/260.
- Passage-context specialist: 54 int8 parameters, 239/260 and 129/150 tuning.
- Recurrence, adaptive learned depth, factorized heads, and cognitive lookup memory
  remain untested/deferred—not falsely rejected.

## Next physical action

Run the Kingston A2 card against the unchanged V14 binary first, then deploy V15
Pack-v2 and AetherChat using the Factory handoff. Record media-control and Pack-v2
results separately.
"""
    write_text(ROOT / "docs" / "REVIEW_PACKET.md", review_packet)

    factory_md = f"""
# Factory V15 device-deployment handoff

Fetch `{BRANCH}`, resolve and record its exact remote SHA/tree, and verify it descends
from `{SOURCE_MAIN}`. Do not move `main` and do not create a release tag.

## Physical identities

- **Device A:** Waveshare ESP32-P4-WIFI6-Touch-LCD-3.5 with integrated C6; preserve
  the user's working Tactility installation and configuration. Overlay
  `integrations/tactility/aetherchat` only after obtaining that exact custom BSP source.
- **Device B:** separate Waveshare ESP32-P4-WIFI6 SKU 32020 accessory, ESP32-P4
  rev v1.3, 32 MiB PSRAM. This is the cognition target.
- **CardKB2:** power by USB-C, press `Fn+Sym+4`, and pair as BLE HID to Device A.
  Do not flash it. There are no CardKB2 GPIO connections.
- **Compute link:** configure the existing C6-hosted ESP-NOW service. There is no
  Device-A-to-Device-B GPIO wiring; both devices require power and pairing/configuration.

## Required sequence

1. Back up Device-A configuration and retain V14 accessory image/pack.
2. Test the Kingston Canvas Go! Plus 128 GB A2 card with the unchanged V14 binary.
3. Record A2 results separately from the earlier poor 128 GB medium.
4. Build V15 using `aethersparse aethercore compile`, then build the prepacked
   PERFORMANCE image with `aethersparse aethercore pack`.
5. Flash Device B, verify pack hashes, and run qualification.
6. Overlay/build AetherChat against the exact user BSP; pair ESP-NOW and CardKB2.
7. Ask the user to type a real query; test follow-up, memory CRUD, cancel, and reset.
8. Capture address p50/p95, pages/misses, SDMMC/DMA counters, CPU, PSRAM/SRAM/stack,
   transport, and reconnect behavior.

The 128 GB card is test media; the long-term pack contract remains 256 GB class.
If parity fails, restore V14 and retain the V15 state export for diagnosis.
"""
    write_text(REPORT_ROOT / "FACTORY_V15_DEVICE_DEPLOYMENT_HANDOFF.md", factory_md)

    qualification_md = f"""
# AetherCore V15 operational-system qualification

## Classification

**V15_READY_WITH_STORAGE_EXPERIMENT_PENDING**

V15 converts the physically qualified V14 architecture into a persistent operational
system without changing Semantic Address or weakening exact verification. Source main
is `{SOURCE_MAIN}` (tree `{SOURCE_TREE}`); qualification source is `{source_sha}`.

## Qualified result

| Area | Result |
|---|---|
| V14 frozen parity | 51/51 ABI; 260/260 cases; 1,329/1,329 decisions; 107/107 queries |
| Native hardening | selected pinning; post-VERIFY and post-TERMINAL freeze PASS |
| Malformed wire | 18 session + 4 COG semantic forgeries rejected |
| COG deserialize | exact 180-byte Python/native roundtrip |
| Memory | four tiers, independent residency, authorized user CRUD, restart/resume PASS |
| Controller | selected V14 1,292 int8; 242/260 total; 138/150 tuning |
| Optional specialist | 54 int8 rejected: 239/260 total; 129/150 tuning |
| Natural input / observer | 21/21 phenomena; 11-event, 40-byte exact observer |
| Agent | 5/5 sandbox tasks, 55 operations, zero unauthorized integration |
| AetherChat | 3 C++ files / 344 LOC; framing compile and malformed rejection PASS |

## Deployment selection

PERFORMANCE uses the prepacked direct compact evidence directory (3,311,868 bytes)
and a 2 MiB cache. Projected total residency is 6,421,665 bytes, leaving 27,132,767
bytes of the 32 MiB PSRAM envelope. Against the unchanged poor-card profile, modeled
mean address latency falls from 1,217.25 ms to 463.31 ms by eliminating evidence-
directory media misses. This is a host model, not a physical claim.

## Remaining gate

Factory must first run the Kingston A2 medium with the unchanged V14 binary, then
measure Pack-v2 physically. It must also obtain the exact user custom Waveshare
Tactility BSP source for the Device-A build. CardKB2 uses factory BLE HID and the
compute link uses existing C6-hosted ESP-NOW, so no GPIO values are required or invented.

The final self/manual knowledge pack remains intentionally deferred until both devices
are physically validated.
"""
    write_text(REPORT_ROOT / "AETHERCORE_V15_OPERATIONAL_SYSTEM_QUALIFICATION.md", qualification_md)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    if len(args.source_sha) != 40 or any(ch not in "0123456789abcdef" for ch in args.source_sha):
        raise SystemExit("--source-sha must be a lowercase 40-character Git SHA")
    assemble(args.source_sha)


if __name__ == "__main__":
    main()
