#!/usr/bin/env python3
"""Emit the compact V14 native/5C/specialist resource qualification lane report."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

DIRECTORY_BYTES = 1_735_620
EVIDENCE_PAGE_BUFFERS_BYTES = 2 * 4096
COG_BYTES = 48
FIVE_C_STATE_BYTES = 64
FIVE_C_CONSTRAINT_COUNT = 9
FIVE_C_CONSTRAINT_BYTES = 32
PROGRESS_BYTES = 48
SPECIALIST_DESCRIPTOR_CAP = 32
SPECIALIST_DESCRIPTOR_BYTES = 72
SPECIALIST_SUMMARY_BYTES = 16
SESSION_BYTES = 872


def native_build_measure(repository: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temporary_name:
        temporary = Path(temporary_name)
        library = temporary / "libaethercore_runtime.so"
        object_file = temporary / "aethercore_runtime.o"
        compile_common = [
            "g++",
            "-I",
            str(repository / "native/aethercore_runtime/include"),
            "-O2",
            "-std=c++17",
            "-fno-exceptions",
            "-fno-rtti",
        ]
        subprocess.run(
            [
                *compile_common,
                "-fPIC",
                "-shared",
                str(repository / "native/aethercore_runtime/src/aethercore_runtime.cpp"),
                "-o",
                str(library),
            ],
            check=True,
        )
        subprocess.run(
            [
                *compile_common,
                "-fstack-usage",
                "-c",
                str(repository / "native/aethercore_runtime/src/aethercore_runtime.cpp"),
                "-o",
                str(object_file),
            ],
            check=True,
        )
        text_bytes, data_bytes, bss_bytes = (
            int(value)
            for value in subprocess.check_output(["size", str(library)], text=True)
            .splitlines()[1]
            .split()[:3]
        )
        stack_rows = []
        for line in object_file.with_suffix(".su").read_text(encoding="utf-8").splitlines():
            fields = line.rsplit("\t", 2)
            if len(fields) == 3 and fields[1].isdigit():
                stack_rows.append(
                    (int(fields[1]), fields[0].replace(f"{repository}/", ""))
                )
        stack_rows.sort(reverse=True)
        return {
            "host_shared_object_file_bytes": library.stat().st_size,
            "elf_load_text_bytes": text_bytes,
            "elf_load_data_bytes": data_bytes,
            "elf_bss_bytes": bss_bytes,
            "elf_load_total_bytes": text_bytes + data_bytes + bss_bytes,
            "host_compiler_max_static_stack_bytes": stack_rows[0][0],
            "host_compiler_max_static_stack_function": stack_rows[0][1],
            "policy_select_static_stack_bytes": next(
                size for size, name in stack_rows if "ac_policy_select_i8_v2" in name
            ),
            "cog_serialize_static_stack_bytes": next(
                size for size, name in stack_rows if "ac_cog_runtime_serialize_v1" in name
            ),
            "evidence_class": "measured host GCC build; not ESP32-P4 binary or stack watermark",
        }


def resident_projection(
    feature_count: int, action_count: int, bias_bytes: int
) -> list[dict[str, int]]:
    policy_bytes = feature_count * action_count + bias_bytes
    fixed = {
        "resident_address_directories": DIRECTORY_BYTES,
        "compact_cog": COG_BYTES,
        "five_c_state": FIVE_C_STATE_BYTES,
        "five_c_constraints": FIVE_C_CONSTRAINT_COUNT * FIVE_C_CONSTRAINT_BYTES,
        "progress": PROGRESS_BYTES,
        "specialist_descriptor_capacity": (
            SPECIALIST_DESCRIPTOR_CAP * SPECIALIST_DESCRIPTOR_BYTES
        ),
        "specialist_summary": SPECIALIST_SUMMARY_BYTES,
        "session_including_candidate_workspace": SESSION_BYTES,
        "int8_policy_weights_and_optional_int32_bias": policy_bytes,
        "active_evidence_page_buffers": EVIDENCE_PAGE_BUFFERS_BYTES,
    }
    base = sum(fixed.values())
    rows = []
    for cache_bytes in (256 * 1024, 1024 * 1024, 2 * 1024 * 1024):
        total = base + cache_bytes
        rows.append(
            {
                "cache_bytes": cache_bytes,
                "combined_resident_bytes": total,
                "headroom_in_4mib_psram_bytes": 4 * 1024 * 1024 - total,
                "headroom_in_8mib_psram_bytes": 8 * 1024 * 1024 - total,
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--feature-count", type=int, default=38)
    parser.add_argument("--action-count", type=int, default=34)
    parser.add_argument("--bias-bytes", type=int, default=0)
    parser.add_argument(
        "--policy-artifact",
        type=Path,
        default=Path("reports/droid/v14/controller-selected-policy-int8.json"),
    )
    parser.add_argument(
        "--binding-status",
        default="EXACT_SELECTED_INT8_ARTIFACT_BOUND_ARGUMENT_AWARE",
    )
    parser.add_argument(
        "--factory-gate",
        choices=("PENDING_INTEGRATED_READY_GATE", "READY_FOR_FACTORY_P4"),
        default="PENDING_INTEGRATED_READY_GATE",
    )
    arguments = parser.parse_args()
    if not 0 < arguments.feature_count <= 64 or not 0 < arguments.action_count <= 64:
        raise SystemExit("native policy dimensions must be within 1..64")
    if arguments.bias_bytes not in (0, arguments.action_count * 4):
        raise SystemExit("bias bytes must be zero or one int32 per action")
    repository = Path(__file__).resolve().parents[2]
    policy_path = (
        arguments.policy_artifact
        if arguments.policy_artifact.is_absolute()
        else repository / arguments.policy_artifact
    )
    selected_policy = json.loads(policy_path.read_text(encoding="utf-8"))
    selected_weights = selected_policy.get("weights_int8")
    if (
        not isinstance(selected_weights, list)
        or len(selected_weights) != arguments.action_count
        or any(
            not isinstance(row, list) or len(row) != arguments.feature_count
            for row in selected_weights
        )
    ):
        raise SystemExit("selected policy artifact shape does not match native binding")
    canonical_policy = json.dumps(
        selected_policy, separators=(",", ":"), sort_keys=True
    ).encode()
    policy_sha256 = hashlib.sha256(canonical_policy).hexdigest()
    macs = arguments.feature_count * arguments.action_count
    primitive_ops = macs * 2 + arguments.action_count + arguments.action_count - 1
    page_reads = {
        "256kib": 11.85,
        "1mib": 0.19,
        "2mib": 0.19,
    }
    report = {
        "schema_version": "aethersparse.v14-native-5c-specialist-runtime.v1",
        "scope": "worker-C native/5C/specialist qualification; not integrated V14 gate",
        "five_c": {
            "root_constraint_classes": 9,
            "constraint_state_bytes": FIVE_C_STATE_BYTES,
            "constraint_descriptor_bytes": FIVE_C_CONSTRAINT_BYTES,
            "default_constraint_bytes": FIVE_C_CONSTRAINT_COUNT * FIVE_C_CONSTRAINT_BYTES,
            "root_is_const_only_in_c_abi": True,
            "verifier_bypass_denied": True,
            "evidence_rewrite_denied": True,
            "controller_root_rewrite_or_prune_denied": True,
            "self_generated_activation_requires": [
                "external authorization",
                "signed update",
                "sandbox",
                "tests",
                "rollback",
            ],
            "contextual_policy_can_override_root": False,
        },
        "specialists": {
            "descriptor_bytes": SPECIALIST_DESCRIPTOR_BYTES,
            "descriptor_capacity_projection": SPECIALIST_DESCRIPTOR_CAP,
            "activation_states": ["COLD", "WARM", "HOT"],
            "shared_parameter_family_and_instance_calibration": True,
            "hard_limit_enforcement": "deterministic after learned residual",
        },
        "native": {
            "abi": "aethercore.runtime.c-abi.v1 additive V14 contracts",
            "compact_cog_bytes": COG_BYTES,
            "compact_cog_python_mapping": (
                "exact 19-u16 aethersparse.cognitive.CompactCOGView order; "
                "three reserved u16 slots"
            ),
            "progress_bytes": PROGRESS_BYTES,
            "cognitive_snapshot_wire_bytes": 180,
            "int8_policy": {
                "binding_status": arguments.binding_status,
                "selected_policy_artifact": str(policy_path.relative_to(repository)),
                "selected_policy_sha256": policy_sha256,
                "argument_aware_candidate_scoring": True,
                "feature_count": arguments.feature_count,
                "action_count": arguments.action_count,
                "weight_bytes": macs,
                "bias_bytes": arguments.bias_bytes,
                "model_bytes": macs + arguments.bias_bytes,
                "macs_per_decision": macs,
                "primitive_integer_ops_per_decision": primitive_ops,
                "legal_mask_bits": 64,
                "numeric_tolerance": 0,
                "frozen_parity_fixture": (
                    "34x19 generic fixture plus exact selected 34x38 artifact"
                ),
            },
            "build": native_build_measure(repository),
        },
        "resident_projection": {
            "components_excluding_cache": {
                "resident_address_directories": DIRECTORY_BYTES,
                "compact_cog": COG_BYTES,
                "five_c_state_and_constraints": (
                    FIVE_C_STATE_BYTES + FIVE_C_CONSTRAINT_COUNT * FIVE_C_CONSTRAINT_BYTES
                ),
                "progress": PROGRESS_BYTES,
                "specialist_metadata": (
                    SPECIALIST_DESCRIPTOR_CAP * SPECIALIST_DESCRIPTOR_BYTES
                    + SPECIALIST_SUMMARY_BYTES
                ),
                "session_including_candidate_workspace": SESSION_BYTES,
                "int8_policy_weights_and_optional_int32_bias": (
                    macs + arguments.bias_bytes
                ),
                "active_evidence_page_buffers": EVIDENCE_PAGE_BUFFERS_BYTES,
            },
            "cache_rows": resident_projection(
                arguments.feature_count, arguments.action_count, arguments.bias_bytes
            ),
            "warning": (
                "headroom is a layout projection, not an ESP-IDF linker map or "
                "physical PSRAM reading"
            ),
        },
        "paged_address": {
            "page_bytes": 4096,
            "resident_directory_bytes": DIRECTORY_BYTES,
            "cold_index_bytes": 32_284_672,
            "proxy_page_reads_per_query": page_reads,
            "proxy_candidate_completeness": 1.0,
            "two_mib_derivation": (
                "same 0.19 compulsory misses/query as 1 MiB because the 778,240-byte "
                "proxy cold index already fits; not a physical 397k trace"
            ),
        },
        "p4_analytical": {
            "evidence_class": "analytical cycle lower bound; no hardware performance claim",
            "policy": {
                f"{mhz}mhz": {
                    "primitive_integer_ops": primitive_ops,
                    "one_op_per_cycle_lower_bound_us": primitive_ops / mhz,
                }
                for mhz in (200, 300, 400)
            },
            "v13_address_latency_ms_p50_p95": {
                "200mhz": [116.0533825, 236.6056065],
                "300mhz": [63.65246333333334, 129.94380825],
                "400mhz": [37.45200375, 76.670070125],
            },
        },
        "factory_handoff": {
            "schema": "config/deployment/aethercore-v14-factory-p4-handoff.schema.json",
            "handoff": "reports/droid/v14/factory-p4-handoff.json",
            "target": "second/accessory ESP32-P4; never the Tactility display appliance",
            "temporary_medium": "128 GB microSD",
            "long_term_class": "256 GB removable storage",
            "gate": arguments.factory_gate,
        },
        "limitations": [
            "ESP-IDF toolchain and physical accessory P4 were not available",
            "stack values are host compiler static estimates, not target high-water measurements",
            "address page readings are the retained V13 proxy, not physical storage I/O",
        ],
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
