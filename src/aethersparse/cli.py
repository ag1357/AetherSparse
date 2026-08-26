"""Command-line entrypoints for reproducible compilation, evaluation, and service."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
import uvicorn
from pydantic import BaseModel

from aethersparse.autonomy.qualification import run_qualification
from aethersparse.autonomy.silver import compile_real_source_silver
from aethersparse.benchmark import run_benchmark
from aethersparse.cells.corpus_gate import FrozenCorpusError, qualify_frozen_scales
from aethersparse.cells.models import CellKind
from aethersparse.cells.pack import CognitiveCellPack
from aethersparse.cells.qualification import compare_topologies
from aethersparse.cells.smoke import canonical_smoke_bytes
from aethersparse.cells.topology import CognitiveCellBuilder
from aethersparse.compiler import COMPILED_FILE, compile_pack
from aethersparse.edge_runtime.deployment_v15 import EvidenceLayout
from aethersparse.edge_runtime.production import (
    compile_native_runtime,
    compile_pack_v2_evidence,
    qualify_production_candidate,
)
from aethersparse.evaluation import run_evaluation
from aethersparse.gate0.pipeline import (
    DEFAULT_DATA_ROOT,
    DEFAULT_REPORT_ROOT,
    bootstrap_gate0,
    build_candidate_and_validation_sets,
    build_query_candidates,
    freeze_rules,
    generate_gate0_report,
    generate_sealed_query_report,
    ingest_source_seed,
    materialize_reviewed_gold,
)
from aethersparse.models import (
    CompiledPack,
    QueryRequest,
    QueryResponse,
    SourceSpan,
)
from aethersparse.selection.qualification import evaluate_selection, train_reranker
from aethersparse.traversal.corpus import CorpusStore
from aethersparse.traversal.evaluation import author_questions, evaluate_retrieval

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
gate0_app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help="Gate 0 compiler qualification workflows.",
)
app.add_typer(gate0_app, name="gate0")
autonomy_app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help="Autonomous architecture qualification workflows.",
)
app.add_typer(autonomy_app, name="autonomy")
corpus_app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help="Real-corpus traversal qualification workflows.",
)
app.add_typer(corpus_app, name="corpus")
selection_app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help="Compact evidence-selection qualification workflows.",
)
app.add_typer(selection_app, name="selection")
cells_app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help="Cognitive-cell topology qualification workflows.",
)
app.add_typer(cells_app, name="cells")
controller_app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help="Certified AetherCore replay and micro-operation qualification workflows.",
)
app.add_typer(controller_app, name="controller")
aethercore_app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help="Production-facing AetherCore compile, pack, qualify, and service operations.",
)
app.add_typer(aethercore_app, name="aethercore")


@aethercore_app.command("compile")
def aethercore_compile(
    output: Annotated[Path, typer.Option(help="Native shared-library output.")] = Path(
        "runtime/v15/libaethercore_runtime.so"
    ),
) -> None:
    """Compile the portable allocation-free C++17 runtime."""

    typer.echo(json.dumps(compile_native_runtime(output), indent=2, sort_keys=True))


@aethercore_app.command("pack")
def aethercore_pack(
    evidence_region: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Option(dir_okay=False)],
    entity_capacity: Annotated[int, typer.Option(min=1)],
    layout: Annotated[EvidenceLayout, typer.Option()] = EvidenceLayout.DIRECT_COMPACT_RESIDENT,
) -> None:
    """Prepack a V14 evidence directory into the selected V15 lookup image."""

    typer.echo(
        json.dumps(
            compile_pack_v2_evidence(
                evidence_region,
                output,
                entity_capacity=entity_capacity,
                layout=layout,
            ),
            indent=2,
            sort_keys=True,
        )
    )


@aethercore_app.command("qualify")
def aethercore_qualify(
    report_root: Annotated[Path, typer.Option(exists=True, file_okay=False)] = Path(
        "reports/droid/v15"
    ),
) -> None:
    """Validate the selected V15 production gates without re-running historical sweeps."""

    typer.echo(
        json.dumps(qualify_production_candidate(report_root), indent=2, sort_keys=True)
    )


@aethercore_app.command("service")
def aethercore_service() -> None:
    """Run the operational AetherCore accessory service."""

    from aethersparse.agent.server import main

    main()


@controller_app.command("export-replay")
def controller_export_replay(
    traces: Annotated[
        list[Path],
        typer.Option("--trace", help="Retained v09 controller trajectory JSONL; repeatable."),
    ],
    output: Annotated[Path, typer.Option(help="Replay bundle output directory.")],
    corpus_tier: Annotated[
        str,
        typer.Option(help="Frozen corpus tier represented by these traces (10k/25k/100k/397k)."),
    ],
) -> None:
    """Export a compact deterministic replay bundle without corpus retrieval."""
    from aethersparse.controller.replay import export_replay_bundle

    manifest = export_replay_bundle(traces, output, corpus_tier=corpus_tier)
    typer.echo(json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True))


@controller_app.command("verify-replay")
def controller_verify_replay(
    bundle: Annotated[Path, typer.Option(help="Replay bundle directory.")],
) -> None:
    """Verify hashes, counts, schemas, and protected-partition markings."""
    from aethersparse.controller.replay import verify_replay_bundle

    manifest = verify_replay_bundle(bundle)
    typer.echo(json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True))


@controller_app.command("qualify-reachability")
def controller_qualify_reachability(
    bundle: Annotated[Path, typer.Option(help="Verified controller replay bundle.")],
    benchmark: Annotated[Path, typer.Option(help="Frozen benchmark used only under split rules.")],
    output: Annotated[Path, typer.Option(help="Qualification report JSON.")],
    max_depth: Annotated[int, typer.Option(min=1, max=64)] = 12,
    max_expansions: Annotated[int, typer.Option(min=1)] = 5000,
    beam_width: Annotated[int, typer.Option(min=1, max=4096)] = 64,
) -> None:
    """Run best-first and beam reachability search with protected split handling."""
    from aethersparse.controller.reachability import qualify_reachability

    report = qualify_reachability(
        bundle,
        benchmark,
        max_depth=max_depth,
        max_expansions=max_expansions,
        beam_width=beam_width,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    typer.echo(json.dumps(report, indent=2, sort_keys=True))


@controller_app.command("classify-failures")
def controller_classify_failures(
    inputs: Annotated[
        list[str],
        typer.Option(
            "--input",
            help="Legacy taxonomy per-case file as TIER=PATH; repeatable.",
        ),
    ],
    output: Annotated[Path, typer.Option(help="Mission 5 taxonomy report JSON.")],
    reachability_report: Annotated[
        Path | None,
        typer.Option(help="Optional completed reachability report used to mark reachable cases."),
    ] = None,
) -> None:
    """Normalize D_CONTROLLER_FAILED records into the Mission 5 taxonomy."""
    from aethersparse.controller.failure_taxonomy import (
        aggregate_failures,
        failure_record_from_legacy,
    )

    reachable: set[tuple[str, str]] = set()
    if reachability_report is not None:
        reachability = json.loads(reachability_report.read_text(encoding="utf-8"))
        reachable = {
            (str(item["corpus_tier"]), str(item["case_id"]))
            for item in reachability.get("per_case", ())
            if item.get("reachable")
        }
    records: list[Any] = []
    for item in inputs:
        try:
            tier, raw_path = item.split("=", 1)
        except ValueError as error:
            raise typer.BadParameter("failure input must be TIER=PATH") from error
        payload = json.loads(Path(raw_path).read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise typer.BadParameter(f"failure input is not a list: {raw_path}")
        records.extend(
            failure_record_from_legacy(
                case,
                corpus_tier=tier,
                reachable=(tier, str(case.get("case_id", ""))) in reachable,
            )
            for case in payload
            if isinstance(case, dict)
        )
    report = aggregate_failures(tuple(records))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    typer.echo(json.dumps(report, indent=2, sort_keys=True))


@cells_app.command("qualify-frozen")
def cells_qualify_frozen(
    corpus_root: Annotated[Path, typer.Option()] = Path("data/real_corpus"),
    manifest: Annotated[Path, typer.Option()] = Path("data/real_corpus/manifest.json"),
    output: Annotated[Path, typer.Option()] = Path(
        "reports/COGNITIVE_CELL_SCALING_QUALIFICATION.json"
    ),
    max_documents: Annotated[int, typer.Option(min=8, max=4096)] = 256,
) -> None:
    try:
        report = qualify_frozen_scales(
            manifest,
            corpus_root,
            max_documents=max_documents,
            progress=lambda scale: typer.echo(f"qualifying frozen {scale} pack...", err=True),
        )
    except FrozenCorpusError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    typer.echo(json.dumps(report, indent=2, sort_keys=True))


@cells_app.command("smoke")
def cells_smoke(
    output: Annotated[Path, typer.Option()] = Path("reports/COGNITIVE_CELL_SMOKE.json"),
    check: Annotated[
        bool, typer.Option(help="Fail if output differs from the checked artifact.")
    ] = False,
) -> None:
    """Reproduce or verify the tiny cognitive-cell contract artifact."""
    generated = canonical_smoke_bytes()
    if check:
        if not output.is_file() or output.read_bytes() != generated:
            typer.echo(f"deterministic smoke mismatch: {output}", err=True)
            raise typer.Exit(code=2)
        typer.echo(f"deterministic smoke verified: {output}")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(generated)
    typer.echo(f"deterministic smoke written: {output}")


@cells_app.command("qualify")
def cells_qualify(
    corpus: Annotated[Path, typer.Option()] = Path("data/real_corpus/simplewiki-10k.sqlite"),
    questions: Annotated[Path, typer.Option()] = Path("data/real_corpus/questions.json"),
    output: Annotated[Path, typer.Option()] = Path("reports/COGNITIVE_CELL_QUALIFICATION.json"),
    max_documents: Annotated[int, typer.Option(min=8, max=4096)] = 256,
) -> None:
    payload = json.loads(questions.read_text(encoding="utf-8"))
    report = compare_topologies(
        CognitiveCellBuilder(CorpusStore(corpus), max_documents=max_documents),
        payload["questions"],
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    typer.echo(json.dumps(report, indent=2, sort_keys=True))


@cells_app.command("build")
def cells_build(
    corpus: Annotated[Path, typer.Option()] = Path("data/real_corpus/simplewiki-10k.sqlite"),
    kind: Annotated[CellKind, typer.Option()] = CellKind.HYBRID,
    output: Annotated[Path, typer.Option()] = Path("data/cells/cells.json"),
    max_documents: Annotated[int, typer.Option(min=8, max=4096)] = 256,
) -> None:
    cells = CognitiveCellBuilder(CorpusStore(corpus), max_documents=max_documents).build(kind)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps([cell.model_dump(mode="json") for cell in cells], indent=2) + "\n",
        encoding="utf-8",
    )
    typer.echo(json.dumps({"kind": kind, "cell_count": len(cells), "output": str(output)}))


@cells_app.command("pack")
def cells_pack(
    corpus: Annotated[Path, typer.Option()] = Path("data/real_corpus/simplewiki-10k.sqlite"),
    kind: Annotated[CellKind, typer.Option()] = CellKind.HYBRID,
    output: Annotated[Path, typer.Option()] = Path("data/cells/hybrid-pack"),
    source_manifest_hash: Annotated[str, typer.Option()] = "sha256:unregistered",
    max_documents: Annotated[int, typer.Option(min=8, max=4096)] = 256,
) -> None:
    cells = CognitiveCellBuilder(CorpusStore(corpus), max_documents=max_documents).build(kind)
    pack = CognitiveCellPack.compile(
        cells, topology=kind, source_manifest_hash=source_manifest_hash
    )
    stats = pack.write(output)
    if not pack.verify():
        raise typer.Exit(code=2)
    typer.echo(json.dumps(stats, indent=2, sort_keys=True))


@selection_app.command("train")
def selection_train(
    corpus: Annotated[Path, typer.Option()] = Path("data/real_corpus/simplewiki-1k.sqlite"),
    questions: Annotated[Path, typer.Option()] = Path("data/real_corpus/scaling_questions.json"),
    output_model: Annotated[Path, typer.Option()] = Path("data/models/evidence_reranker.int8.json"),
    output_manifest: Annotated[Path, typer.Option()] = Path(
        "data/models/evidence_reranker.training.json"
    ),
) -> None:
    result = train_reranker(corpus, questions, output_model, output_manifest)
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@selection_app.command("evaluate")
def selection_evaluate(
    corpus: Annotated[Path, typer.Option()] = Path("data/real_corpus/simplewiki-10k.sqlite"),
    questions: Annotated[Path, typer.Option()] = Path("data/real_corpus/questions.json"),
    model: Annotated[Path, typer.Option()] = Path("data/models/evidence_reranker.int8.json"),
    output: Annotated[Path, typer.Option()] = Path("reports/EVIDENCE_SELECTION_10K.json"),
    limit: Annotated[int | None, typer.Option(min=1)] = None,
) -> None:
    result = evaluate_selection(corpus, questions, model, output, limit=limit)
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@corpus_app.command("ingest-mediawiki")
def corpus_ingest_mediawiki(
    dump: Annotated[Path, typer.Option(help="MediaWiki pages-articles XML or XML.bz2.")],
    output: Annotated[Path, typer.Option(help="Immutable SQLite corpus pack.")] = Path(
        "data/real_corpus/simplewiki.sqlite"
    ),
    limit: Annotated[int | None, typer.Option(min=1)] = None,
    chunk_chars: Annotated[int, typer.Option(min=200, max=4096)] = 480,
    fold_redirects: Annotated[
        bool, typer.Option(help="Resolve redirect source titles to their targets.")
    ] = True,
) -> None:
    store = CorpusStore(output)
    result = store.ingest_mediawiki(
        dump, limit=limit, chunk_chars=chunk_chars, fold_redirects=fold_redirects
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


@corpus_app.command("stats")
def corpus_stats(
    corpus: Annotated[Path, typer.Option()] = Path("data/real_corpus/simplewiki.sqlite"),
) -> None:
    typer.echo(json.dumps(CorpusStore(corpus).stats(), indent=2, sort_keys=True))


@corpus_app.command("author-questions")
def corpus_author_questions(
    corpus: Annotated[Path, typer.Option()] = Path("data/real_corpus/simplewiki.sqlite"),
    output: Annotated[Path, typer.Option()] = Path("data/real_corpus/questions.json"),
    count: Annotated[int, typer.Option(min=1)] = 2000,
    seed: Annotated[int, typer.Option()] = 48_271,
) -> None:
    typer.echo(json.dumps(author_questions(corpus, output, count=count, seed=seed), indent=2))


@corpus_app.command("evaluate")
def corpus_evaluate(
    corpus: Annotated[Path, typer.Option()] = Path("data/real_corpus/simplewiki.sqlite"),
    questions: Annotated[Path, typer.Option()] = Path("data/real_corpus/questions.json"),
    output: Annotated[Path, typer.Option()] = Path("reports/REAL_CORPUS_EVALUATION.json"),
    limit: Annotated[int | None, typer.Option(min=1)] = None,
) -> None:
    typer.echo(
        json.dumps(
            evaluate_retrieval(corpus, questions, output, limit=limit),
            indent=2,
            sort_keys=True,
        )
    )


@app.command("compile")
def compile_command(
    output: Annotated[
        Path,
        typer.Option(help="Compiled pack path."),
    ] = COMPILED_FILE,
) -> None:
    pack = compile_pack(output_file=output)
    typer.echo(json.dumps(pack.manifest.model_dump(mode="json"), indent=2, sort_keys=True))


@app.command("evaluate")
def evaluate_command(
    output: Annotated[
        Path | None,
        typer.Option(help="Optional JSON report path."),
    ] = None,
) -> None:
    report = run_evaluation()
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    typer.echo(rendered, nl=False)


@app.command("serve")
def serve_command(
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65_535)] = 8000,
) -> None:
    uvicorn.run("aethersparse.service:app", host=host, port=port, reload=False)


@app.command("benchmark")
def benchmark_command(
    iterations: Annotated[int, typer.Option(min=1, max=100_000)] = 500,
    output: Annotated[
        Path | None,
        typer.Option(help="Optional JSON report path."),
    ] = None,
) -> None:
    report = run_benchmark(iterations)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    typer.echo(rendered, nl=False)


@autonomy_app.command("qualify")
def autonomy_qualify_command(
    scale: Annotated[
        str,
        typer.Option(help="debug, intermediate, or decisive"),
    ] = "decisive",
    output_root: Annotated[
        Path,
        typer.Option(help="Generated datasets, models, and report artifacts."),
    ] = Path("data/autonomy/release"),
    report_root: Annotated[
        Path,
        typer.Option(help="Human-readable and machine-readable reports."),
    ] = Path("reports"),
) -> None:
    report = run_qualification(
        scale_name=scale,
        output_root=output_root,
        report_root=report_root,
    )
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


@autonomy_app.command("compile-silver")
def autonomy_compile_silver_command(
    gate0_root: Annotated[
        Path,
        typer.Option(help="Frozen Gate 0 real-source corpus."),
    ] = DEFAULT_DATA_ROOT,
    output_root: Annotated[
        Path,
        typer.Option(help="Autonomous real-source silver output."),
    ] = Path("data/autonomy/release/real_source_silver"),
) -> None:
    report = compile_real_source_silver(
        gate0_root=gate0_root,
        output_root=output_root,
    )
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


@app.command("export-schemas")
def export_schemas(
    output_dir: Annotated[Path, typer.Option()] = Path("specs/schemas"),
) -> None:
    models: dict[str, type[BaseModel]] = {
        "source_span": SourceSpan,
        "compiled_pack": CompiledPack,
        "service_request": QueryRequest,
        "service_response": QueryResponse,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, model in models.items():
        path = output_dir / f"{name}.schema.json"
        path.write_text(
            json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


@gate0_app.command("bootstrap")
def gate0_bootstrap_command(
    seed: Annotated[Path, typer.Option()] = Path("data/gate0/source_seed.json"),
    data_root: Annotated[Path, typer.Option()] = DEFAULT_DATA_ROOT,
    report_root: Annotated[Path, typer.Option()] = DEFAULT_REPORT_ROOT,
) -> None:
    result = bootstrap_gate0(seed, data_root, report_root)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


@gate0_app.command("ingest-sources")
def gate0_ingest_sources_command(
    seed: Annotated[Path, typer.Option()] = Path("data/gate0/source_seed.json"),
    data_root: Annotated[Path, typer.Option()] = DEFAULT_DATA_ROOT,
) -> None:
    _repository, manifest = ingest_source_seed(seed, data_root)
    typer.echo(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))


@gate0_app.command("extract-validate")
def gate0_extract_validate_command(
    data_root: Annotated[Path, typer.Option()] = DEFAULT_DATA_ROOT,
) -> None:
    result = build_candidate_and_validation_sets(data_root)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


@gate0_app.command("materialize-gold")
def gate0_materialize_gold_command(
    data_root: Annotated[Path, typer.Option()] = DEFAULT_DATA_ROOT,
) -> None:
    result = materialize_reviewed_gold(data_root)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


@gate0_app.command("freeze-rules")
def gate0_freeze_rules_command(
    data_root: Annotated[Path, typer.Option()] = DEFAULT_DATA_ROOT,
    permit_sealed: Annotated[
        bool,
        typer.Option(
            help="Permit sealed evaluation only after calibration/development review is complete."
        ),
    ] = False,
) -> None:
    result = freeze_rules(data_root, sealed_evaluation_permitted=permit_sealed)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


@gate0_app.command("report")
def gate0_report_command(
    data_root: Annotated[Path, typer.Option()] = DEFAULT_DATA_ROOT,
    report_root: Annotated[Path, typer.Option()] = DEFAULT_REPORT_ROOT,
) -> None:
    result = generate_gate0_report(data_root, report_root)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


@gate0_app.command("build-query-candidates")
def gate0_build_query_candidates_command(
    data_root: Annotated[Path, typer.Option()] = DEFAULT_DATA_ROOT,
) -> None:
    result = build_query_candidates(data_root)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


@gate0_app.command("evaluate-sealed")
def gate0_evaluate_sealed_command(
    data_root: Annotated[Path, typer.Option()] = DEFAULT_DATA_ROOT,
    report_root: Annotated[Path, typer.Option()] = DEFAULT_REPORT_ROOT,
) -> None:
    result = generate_sealed_query_report(data_root, report_root)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
