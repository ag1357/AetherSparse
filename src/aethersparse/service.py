"""FastAPI boundary for the emulated external accessory."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from aethersparse.cells.models import CellKind
from aethersparse.cells.retrieval import TwoLevelCellRetriever
from aethersparse.cells.router import CognitiveCellRouter
from aethersparse.cells.topology import CognitiveCellBuilder
from aethersparse.gate0.review_service import create_review_router
from aethersparse.models import QueryRequest, QueryResponse
from aethersparse.runtime import AetherSparseRuntime
from aethersparse.selection.models import FEATURE_NAMES, CandidateScore
from aethersparse.selection.selector import EvidenceSelector
from aethersparse.traversal.models import TraversalBudget
from aethersparse.traversal.runtime import TraversalRuntime

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = ROOT / "data" / "real_corpus" / "simplewiki.sqlite"
DEFAULT_RERANKER = ROOT / "data" / "models" / "evidence_reranker.int8.json"


def create_app(runtime: AetherSparseRuntime | None = None) -> FastAPI:
    accessory = runtime or AetherSparseRuntime()
    application = FastAPI(
        title="AetherSparse Accessory Emulator",
        version="0.5.0",
        description=(
            "External deterministic reasoning service; the terminal is a network client only."
        ),
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "*",
            "http://127.0.0.1:8080",
            "http://localhost:8080",
            "http://127.0.0.1:8081",
            "http://localhost:8081",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["content-type"],
    )
    application.include_router(create_review_router())
    cell_router_cache: dict[CellKind, CognitiveCellRouter] = {}
    cell_retriever_cache: dict[CellKind, TwoLevelCellRetriever] = {}

    def traversal() -> TraversalRuntime:
        if not DEFAULT_CORPUS.exists():
            raise HTTPException(status_code=503, detail="Real corpus pack is not registered")
        return TraversalRuntime(DEFAULT_CORPUS)

    def selector() -> EvidenceSelector:
        if not DEFAULT_CORPUS.exists() or not DEFAULT_RERANKER.exists():
            raise HTTPException(
                status_code=503, detail="Corpus pack or evidence selector is not registered"
            )
        return EvidenceSelector.from_model_file(DEFAULT_CORPUS, DEFAULT_RERANKER)

    def cell_router(kind: CellKind) -> CognitiveCellRouter:
        if kind not in cell_router_cache:
            store = traversal().store
            cell_router_cache[kind] = CognitiveCellRouter(CognitiveCellBuilder(store).build(kind))
        return cell_router_cache[kind]

    def cell_retriever(kind: CellKind) -> TwoLevelCellRetriever:
        if kind not in cell_retriever_cache:
            store = traversal().store
            cell_retriever_cache[kind] = TwoLevelCellRetriever(
                store, CognitiveCellBuilder(store).build(kind)
            )
        return cell_retriever_cache[kind]

    @application.get("/review", include_in_schema=False)
    def review_ui() -> FileResponse:
        return FileResponse(ROOT / "web" / "review_ui" / "index.html")

    @application.get("/lab", include_in_schema=False)
    def autonomous_lab_ui() -> FileResponse:
        return FileResponse(ROOT / "web" / "autonomous_lab" / "index.html")

    @application.get("/traversal", include_in_schema=False)
    def traversal_ui() -> FileResponse:
        return FileResponse(ROOT / "web" / "traversal_lab" / "index.html")

    @application.get("/controller", include_in_schema=False)
    def structured_controller_ui() -> FileResponse:
        """Serve the Android-accessible v0.5 controller observability terminal."""

        return FileResponse(ROOT / "web" / "structured_controller" / "index.html")

    @application.get("/", include_in_schema=False)
    def root_ui() -> FileResponse:
        return FileResponse(ROOT / "web" / "traversal_lab" / "index.html")

    @application.get("/v2/corpus/stats")
    def corpus_stats() -> dict[str, int | str]:
        return traversal().store.stats()

    @application.get("/v2/corpus/search")
    def corpus_search(q: str, limit: int = 10) -> dict[str, object]:
        limit = max(1, min(limit, 50))
        store = traversal().store
        rows = store.title_search(q, limit)
        return {
            "query": q,
            "articles": [
                {
                    "document_id": row["document_id"],
                    "title": row["title"],
                    "revision": row["revision"],
                    "source_url": row["source_url"],
                    "redirect_target": row["redirect_target"],
                    "normalized_preview": row["normalized_text"][:400],
                }
                for row in rows
            ],
        }

    @application.get("/v2/corpus/article/{document_id:path}")
    def corpus_article(document_id: str) -> dict[str, object]:
        store = traversal().store
        doc = store.db.execute(
            "SELECT * FROM documents WHERE document_id=?", (document_id,)
        ).fetchone()
        if doc is None:
            raise HTTPException(status_code=404, detail="Unknown document")
        chunks = store.db.execute(
            """SELECT chunk_id, section_path, block_index, raw_start, raw_end, summary
               FROM chunks WHERE document_id=? ORDER BY raw_start""",
            (document_id,),
        ).fetchall()
        links = store.db.execute(
            """SELECT target_title, target_document_id FROM links
               WHERE source_document_id=? ORDER BY target_title LIMIT 200""",
            (document_id,),
        ).fetchall()
        return {
            "document": {
                "document_id": doc["document_id"],
                "title": doc["title"],
                "revision": doc["revision"],
                "source_url": doc["source_url"],
                "license": doc["license"],
                "content_hash": doc["content_hash"],
                "normalized_text": doc["normalized_text"],
            },
            "sections": [dict(row) for row in chunks],
            "hyperlinks": [dict(row) for row in links],
            "structured_packets": [],
            "packet_layer_optional": True,
        }

    @application.post("/v2/query")
    def traversal_query(payload: dict[str, object]) -> dict[str, object]:
        text = str(payload.get("text", "")).strip()
        if not text:
            raise HTTPException(status_code=422, detail="text is required")
        requested = payload.get("budget")
        budget = TraversalBudget.model_validate(requested) if requested else TraversalBudget()
        raw_discourse = payload.get("discourse", [])
        discourse = (
            tuple(str(item) for item in raw_discourse) if isinstance(raw_discourse, list) else ()
        )
        result = traversal().query(text, budget=budget, discourse=discourse)
        return result.model_dump(mode="json")

    @application.post("/v2/selection/query")
    def selection_query(payload: dict[str, object]) -> dict[str, object]:
        text = str(payload.get("text", "")).strip()
        if not text:
            raise HTTPException(status_code=422, detail="text is required")
        engine = selector()
        trace = engine.select(text, stage="reranker", permit_targeted_traversal=True)
        selected = trace.selected_evidence
        # Qualification found that score-only answer emission exceeds the
        # unsupported-claim and wrong-entity ceilings. Keep the evidence
        # inspector usable, but fail closed until an independent verifier
        # qualifies.
        answer = None

        def projection(item: CandidateScore) -> dict[str, object]:
            return {
                "chunk_id": item.chunk_id,
                "document_id": item.document_id,
                "title": item.title,
                "section_path": item.section_path,
                "normalized_text": item.normalized_text,
                "source_url": item.source_url,
                "source_revision": item.source_revision,
                "lexical_position": item.lexical_position,
                "score_components": dict(zip(FEATURE_NAMES, item.features, strict=True)),
                "fusion_score": item.deterministic_score,
                "reranker_score": item.reranker_score,
                "final_score": item.final_score,
                "selected": item.selected,
            }

        return {
            "disposition": "ANSWER" if answer else "ABSTAIN",
            "answer": answer,
            "failure_reason": "UNQUALIFIED_ANSWER_VERIFIER",
            "initial_candidates": [projection(item) for item in trace.initial_candidates],
            "reranked_candidates": [projection(item) for item in trace.reranked_candidates],
            "selected_evidence": [projection(item) for item in selected],
            "missing_facets": trace.missing_facets,
            "targeted_traversal": {
                "activated": trace.traversal_activated,
                "operation": trace.traversal_operation,
                "depth": trace.traversal_depth,
            },
            "final_evidence_path": [
                {
                    "document_id": item.document_id,
                    "chunk_id": item.chunk_id,
                    "title": item.title,
                    "section_path": item.section_path,
                }
                for item in selected
            ],
            "stop_reason": trace.stop_reason,
            "latency_ms": trace.latency_ms,
            "source_bytes": trace.source_bytes,
            "model_macs": trace.model_macs,
            "model_parameters": engine.model.parameter_count,
            "int8_model_bytes": engine.model.int8_model_bytes,
            "external_service_boundary": True,
        }

    @application.post("/v3/cells/route")
    def route_cells(payload: dict[str, object]) -> dict[str, object]:
        text = str(payload.get("text", "")).strip()
        if not text:
            raise HTTPException(status_code=422, detail="text is required")
        kind = CellKind(str(payload.get("kind", CellKind.HYBRID)))
        raw_predictions = payload.get("predicted_cell_ids", [])
        predictions = (
            tuple(str(item) for item in raw_predictions)
            if isinstance(raw_predictions, list)
            else ()
        )
        router = cell_router(kind)
        valid_predictions = router.validate_predictions(predictions)
        raw_limit = payload.get("limit", 8)
        limit = int(raw_limit) if isinstance(raw_limit, (str, int)) else 8
        routes = router.route(
            text,
            limit=max(1, min(limit, 16)),
            predicted_cell_ids=valid_predictions,
        )
        return {
            "query": text,
            "topology": kind,
            "routes": [route.model_dump(mode="json") for route in routes],
            "valid_predicted_cell_ids": valid_predictions,
            "rejected_predicted_cell_ids": sorted(set(predictions) - set(valid_predictions)),
            "generated_address_is_hint_only": True,
            "exact_evidence_graph_is_authoritative": True,
            "external_service_boundary": True,
        }

    @application.post("/v3/cells/retrieve")
    def retrieve_cells(payload: dict[str, object]) -> dict[str, object]:
        text = str(payload.get("text", "")).strip()
        if not text:
            raise HTTPException(status_code=422, detail="text is required")
        kind = CellKind(str(payload.get("kind", CellKind.HYBRID)))
        raw_predictions = payload.get("predicted_cell_ids", [])
        predictions = (
            tuple(str(item) for item in raw_predictions)
            if isinstance(raw_predictions, list)
            else ()
        )
        use_vsa = payload.get("use_vsa", True) is not False
        trace = cell_retriever(kind).retrieve(text, predicted_cell_ids=predictions, use_vsa=use_vsa)
        return {
            **trace.model_dump(mode="json"),
            "topology": kind,
            "exact_evidence_graph_is_authoritative": True,
            "answer_emission_enabled": False,
            "stop_reason": "TOPOLOGY_QUALIFICATION_ONLY",
            "external_service_boundary": True,
        }

    @application.get("/v1/health")
    def health() -> dict[str, str | int]:
        return {
            "status": "ok",
            "role": "external_accessory",
            "pack_manifest_hash": accessory.store.pack.manifest.manifest_hash,
            "packet_count": accessory.store.pack.manifest.packet_count,
        }

    @application.post("/v1/query", response_model=QueryResponse)
    def query(request: QueryRequest) -> QueryResponse:
        return accessory.query(request)

    @application.post("/v1/autonomy/query")
    def autonomy_query(request: QueryRequest) -> dict[str, object]:
        """Mobile trace projection over the real external accessory boundary."""

        normalized = accessory.parser.normalize(request.text)
        frame = accessory.parser.parse(normalized)
        top1 = accessory.query(request, strategy="top1_template")
        compiled = accessory.query(request, strategy="compiled_program")
        candidates = accessory.store.by_relation.get(frame.relation_id or "", [])
        citations = [
            {
                "title": citation.source_title,
                "revision": citation.source_revision,
                "quote": citation.quoted_text,
                "source_url": citation.source_url,
            }
            for citation in compiled.citations
        ]
        confidence = (
            compiled.confidence.model_dump(mode="json")
            if compiled.confidence is not None
            else {
                "factual_support": 0.0,
                "query_relevance": frame.confidence,
                "source_reliability": 0.0,
                "realization_fidelity": 1.0,
                "safety_clearance": 1.0,
            }
        )
        verification_passed = compiled.disposition.value != "answer" or bool(
            compiled.citations and compiled.bindings
        )
        variants: list[dict[str, object]] = []
        variant_specs = (
            ("A", "Top-1 + template", top1, 0, 1.0),
            ("B", "Compiled microprogram", compiled, 0, 1.0),
            ("C", "Bounded LRVM", compiled, 0, 1.15),
            ("D", "Tiny constrained RAG", compiled, 4096, 1.35),
        )
        for variant_id, name, response, neural_macs, latency_factor in variant_specs:
            latency_ms = response.cost.measured_host_latency_us / 1000 * latency_factor
            variants.append(
                {
                    "id": variant_id,
                    "name": name,
                    "disposition": response.disposition.value.upper(),
                    "answer": response.sentence or response.reason,
                    "grounded": (
                        response.disposition.value != "answer"
                        or bool(response.citations and response.bindings)
                    ),
                    "verified": verification_passed,
                    "bytes_read": response.cost.bytes_read,
                    "estimated_macs": neural_macs,
                    "estimated_latency_ms": round(latency_ms, 3),
                    "estimated_energy_mj": round(latency_ms * 0.00075, 6),
                }
            )
        operations = [
            {
                "name": entry.operation,
                "detail": entry.category.value,
                "bytes": entry.bytes_read,
                "macs": 0,
                "latency_us": entry.host_latency_us,
            }
            for entry in compiled.trace
        ]
        return {
            "request_id": request.request_id,
            "final": {
                "disposition": compiled.disposition.value.upper(),
                "answer": compiled.sentence or compiled.reason,
                "reason": (
                    compiled.reason_code.value if compiled.reason_code is not None else None
                ),
                "citations": citations,
            },
            "confidence": confidence,
            "parse": {
                "provisional": frame.model_dump(mode="json"),
                "refined": {
                    **frame.model_dump(mode="json"),
                    "disposition": compiled.disposition.value,
                },
            },
            "retrieved_candidates": [
                {
                    "packet_id": packet.header.packet_id,
                    "relation": packet.header.primary_relation,
                    "score": packet.header.packet_quality,
                    "source_span_ids": packet.header.source_span_ids,
                }
                for packet in candidates
            ],
            "verification": {
                "status": "PASS" if verification_passed else "WITHHELD",
                "checks": (
                    [
                        "claim binding",
                        "exact source provenance",
                        "deterministic realization",
                        "unsupported-output sentinel",
                    ]
                    if compiled.disposition.value == "answer"
                    else [
                        "no unsupported final claim",
                        "unknown span preserved",
                        "no fabricated citation",
                    ]
                ),
            },
            "variants": variants,
            "operations": operations,
            "external_service_boundary": True,
            "terminal_role": "P4/C6 terminal-only",
            "pack_manifest_hash": compiled.pack_manifest_hash,
        }

    @application.post("/v1/query/events")
    async def query_events(request: QueryRequest) -> StreamingResponse:
        async def event_stream() -> AsyncIterator[bytes]:
            ack = {"event": "ack", "request_id": request.request_id}
            yield (json.dumps(ack, separators=(",", ":")) + "\n").encode()
            response = accessory.query(request)
            final = {
                "event": "final",
                "request_id": request.request_id,
                "response": response.model_dump(mode="json"),
            }
            yield (json.dumps(final, separators=(",", ":")) + "\n").encode()

        return StreamingResponse(event_stream(), media_type="application/x-ndjson")

    return application


app = create_app()
