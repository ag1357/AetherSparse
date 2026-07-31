"""Comparative topology metrics for the Cognitive Cell gate."""

from __future__ import annotations

import statistics
from collections.abc import Iterable

from aethersparse.cells.models import CellKind, CognitiveCell
from aethersparse.cells.router import CognitiveCellRouter
from aethersparse.cells.topology import CognitiveCellBuilder


def topology_metrics(cells: list[CognitiveCell], document_count: int) -> dict[str, float | int]:
    memberships = sum(len(cell.document_ids) for cell in cells)
    sizes = [len(cell.document_ids) for cell in cells] or [0]
    return {
        "cell_count": len(cells),
        "average_cell_size": statistics.fmean(sizes),
        "maximum_cell_size": max(sizes),
        "overlap_factor": memberships / max(1, document_count),
        "total_declared_source_bytes": sum(cell.source_bytes for cell in cells),
    }


def evaluate_routes(
    cells: list[CognitiveCell],
    questions: Iterable[dict[str, object]],
    *,
    use_vsa: bool = True,
) -> dict[str, float | int]:
    router = CognitiveCellRouter(cells)
    totals = hits1 = hits4 = hits8 = 0
    bytes_at8: list[int] = []
    candidate_counts: list[int] = []
    cell_by_id = {cell.cell_id: cell for cell in cells}
    for question in questions:
        raw_path = question.get("gold_document_path", ())
        gold_documents = (
            {str(item) for item in raw_path} if isinstance(raw_path, (list, tuple)) else set()
        )
        if not gold_documents:
            continue
        query = str(question["query"])
        candidate_counts.append(len(router.candidate_ids(query, use_vsa=use_vsa)))
        routes = router.route(query, limit=8, use_vsa=use_vsa)
        hit_positions = [
            index
            for index, route in enumerate(routes)
            if gold_documents & set(cell_by_id[route.cell_id].document_ids)
        ]
        totals += 1
        hits1 += int(bool(hit_positions and min(hit_positions) < 1))
        hits4 += int(bool(hit_positions and min(hit_positions) < 4))
        hits8 += int(bool(hit_positions and min(hit_positions) < 8))
        bytes_at8.append(sum(cell_by_id[route.cell_id].source_bytes for route in routes))
    return {
        "question_count": totals,
        "cell_recall_at_1": hits1 / max(1, totals),
        "cell_recall_at_4": hits4 / max(1, totals),
        "cell_recall_at_8": hits8 / max(1, totals),
        "mean_declared_cell_bytes_at_8": statistics.fmean(bytes_at8) if bytes_at8 else 0.0,
        "mean_routed_cell_candidates": (
            statistics.fmean(candidate_counts) if candidate_counts else 0.0
        ),
        "maximum_routed_cell_candidates": max(candidate_counts, default=0),
    }


def compare_topologies(
    builder: CognitiveCellBuilder, questions: Iterable[dict[str, object]]
) -> dict[str, object]:
    frozen_questions = list(questions)
    document_count = int(builder.store.stats()["documents"])
    results: dict[str, object] = {}
    for kind in CellKind:
        cells = builder.build(kind)
        results[kind.value] = {
            **topology_metrics(cells, document_count),
            "with_vsa": evaluate_routes(cells, frozen_questions, use_vsa=True),
            "without_vsa": evaluate_routes(cells, frozen_questions, use_vsa=False),
        }
    return {
        "classification": "COGNITIVE_CELL_TOPOLOGY_QUALIFICATION",
        "baseline_preserved": "REAL_CORPUS_ARCHITECTURE_FAILED",
        "topologies": results,
        "decision": "NOT_QUALIFIED_WITHOUT_FROZEN_REAL_CORPUS_RUN",
    }
