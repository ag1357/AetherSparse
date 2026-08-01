"""Freeze or audit independently authored v0.5 natural-query cases."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aethersparse.controller.evaluation import (
    AuditSourceDocument,
    FrozenBenchmark,
    NaturalQueryCase,
    RoleIdentity,
    audit_benchmark,
    freeze_benchmark,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, help="independently authored/adjudicated case JSON")
    parser.add_argument("--roles", type=Path, help="isolated role identity JSON")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit", type=Path, help="audit an existing frozen benchmark")
    parser.add_argument("--sources", type=Path, help="JSON mapping document ID to immutable text")
    parser.add_argument("--allow-compact-fixture", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _args()
    if args.audit:
        if not args.sources:
            raise SystemExit("--sources is required for provenance audit")
        benchmark = FrozenBenchmark.model_validate_json(args.audit.read_text(encoding="utf-8"))
        sources = json.loads(args.sources.read_text(encoding="utf-8"))
        source_documents = {
            str(key): AuditSourceDocument.model_validate(value)
            if isinstance(value, dict)
            else str(value)
            for key, value in sources.items()
        }
        report = audit_benchmark(
            benchmark,
            source_documents,
            require_full=not args.allow_compact_fixture,
        )
        args.output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return
    if not args.cases or not args.roles:
        raise SystemExit("--cases and --roles are required to freeze a benchmark")
    cases_payload = json.loads(args.cases.read_text(encoding="utf-8"))
    roles = json.loads(args.roles.read_text(encoding="utf-8"))
    benchmark = freeze_benchmark(
        tuple(NaturalQueryCase.model_validate(item) for item in cases_payload),
        author_roles=tuple(RoleIdentity.model_validate(item) for item in roles["authors"]),
        adjudicator_role=RoleIdentity.model_validate(roles["adjudicator"]),
        evaluator_role=RoleIdentity.model_validate(roles["evaluator"]),
        auditor_role=RoleIdentity.model_validate(roles["auditor"]),
        require_full=not args.allow_compact_fixture,
    )
    args.output.write_text(benchmark.model_dump_json(indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
