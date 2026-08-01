"""Reproduce R1 through isolated author, adjudicator, evaluator, and audit jobs."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--work-directory", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    script_directory = Path(__file__).resolve().parent
    args.work_directory.mkdir(parents=True, exist_ok=True)
    args.output_directory.mkdir(parents=True, exist_ok=True)
    author_paths: list[Path] = []
    for author in ("alpha", "beta", "gamma"):
        output = args.work_directory / f"author-{author}.json"
        _run(
            [
                sys.executable,
                str(script_directory / "author_shard.py"),
                "--corpus",
                str(args.corpus),
                "--author",
                author,
                "--output",
                str(output),
            ]
        )
        author_paths.append(output)

    benchmark = args.output_directory / "INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json"
    manifest = args.output_directory / "INDEPENDENT_NATURAL_QUERY_SET_V050_R1.manifest.json"
    source_map = args.output_directory / "INDEPENDENT_NATURAL_QUERY_SET_V050_R1.source-map.json"
    adjudication_command = [
        sys.executable,
        str(script_directory / "adjudicate_and_freeze.py"),
        "--corpus",
        str(args.corpus),
    ]
    for author_path in author_paths:
        adjudication_command.extend(["--draft", str(author_path)])
    adjudication_command.extend(
        [
            "--benchmark-output",
            str(benchmark),
            "--manifest-output",
            str(manifest),
            "--source-map-output",
            str(source_map),
        ]
    )
    _run(adjudication_command)

    blind_input = (
        args.output_directory / "INDEPENDENT_NATURAL_QUERY_SET_V050_R1.blind-input.json"
    )
    _run(
        [
            sys.executable,
            str(script_directory / "build_blind_evaluation_input.py"),
            "--benchmark",
            str(benchmark),
            "--output",
            str(blind_input),
        ]
    )
    _run(
        [
            sys.executable,
            str(script_directory / "provenance_audit.py"),
            "--corpus",
            str(args.corpus),
            "--benchmark",
            str(benchmark),
            "--manifest",
            str(manifest),
            "--blind-input",
            str(blind_input),
            "--output",
            str(
                args.output_directory
                / "INDEPENDENT_NATURAL_QUERY_SET_V050_R1.provenance-audit.json"
            ),
        ]
    )


if __name__ == "__main__":
    main()
