"""Profile v0.5 flat SQLite/binary packs and emit a frozen edge report."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from aethersparse.v050.profiling import (
    FrozenHardwareCriteria,
    ProfileQuery,
    build_edge_qualification_report,
    profile_binary_pack,
    profile_sqlite_pack,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Measure only the winning flat structured workload. Packs are opened "
            "read-only and are never materialized in memory."
        )
    )
    parser.add_argument(
        "--sqlite-pack",
        action="append",
        default=[],
        metavar="PROFILE_ID=PATH",
        help="10k/50k real-corpus SQLite pack; repeat for progressive scales",
    )
    parser.add_argument(
        "--binary-pack",
        action="append",
        default=[],
        metavar="PROFILE_ID=PATH",
        help="10k/50k flat structured binary pack; repeat for progressive scales",
    )
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--criteria", type=Path)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _named_path(value: str) -> tuple[str, Path]:
    name, separator, raw_path = value.partition("=")
    if not separator or not name or not raw_path:
        raise ValueError(f"expected PROFILE_ID=PATH, received {value!r}")
    return name, Path(raw_path)


def _load_queries(path: Path) -> tuple[ProfileQuery, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_queries = payload.get("queries") if isinstance(payload, dict) else payload
    if not isinstance(raw_queries, list):
        raise ValueError("query JSON must be an array or an object containing a queries array")
    queries = tuple(ProfileQuery.model_validate(item) for item in raw_queries)
    if not queries:
        raise ValueError("query JSON contains no queries")
    return queries


def _criteria(
    path: Path | None, *, default_profile_id: str
) -> tuple[FrozenHardwareCriteria, str]:
    if path is None:
        criteria = FrozenHardwareCriteria(
            criteria_id="UNQUALIFIED_DEFAULT_FAIL_CLOSED",
            decision_profile_id=default_profile_id,
        )
        payload = criteria.model_dump_json().encode("utf-8")
        return criteria, f"sha256:{hashlib.sha256(payload).hexdigest()}"
    payload = path.read_bytes()
    return (
        FrozenHardwareCriteria.model_validate_json(payload),
        f"sha256:{hashlib.sha256(payload).hexdigest()}",
    )


def main() -> None:
    args = _arguments()
    named_sqlite = tuple(_named_path(item) for item in args.sqlite_pack)
    named_binary = tuple(_named_path(item) for item in args.binary_pack)
    if not named_sqlite and not named_binary:
        raise SystemExit("at least one --sqlite-pack or --binary-pack is required")
    if args.repetitions < 1:
        raise SystemExit("--repetitions must be positive")
    all_ids = [name for name, _path in (*named_sqlite, *named_binary)]
    if len(all_ids) != len(set(all_ids)):
        raise SystemExit("profile IDs must be unique")
    queries = _load_queries(args.queries)
    profiles = [
        profile_sqlite_pack(
            path,
            queries,
            profile_id=profile_id,
            repetitions=args.repetitions,
        )
        for profile_id, path in named_sqlite
    ]
    profiles.extend(
        profile_binary_pack(
            path,
            queries,
            profile_id=profile_id,
            repetitions=args.repetitions,
        )
        for profile_id, path in named_binary
    )
    criteria, criteria_sha256 = _criteria(
        args.criteria,
        default_profile_id=profiles[-1].profile_id,
    )
    report = build_edge_qualification_report(
        profiles,
        criteria,
        criteria_sha256=criteria_sha256,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
