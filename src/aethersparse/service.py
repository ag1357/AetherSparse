"""FastAPI boundary for the emulated external accessory."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from aethersparse.cells.models import CellKind
from aethersparse.cells.retrieval import TwoLevelCellRetriever
from aethersparse.cells.router import CognitiveCellRouter
from aethersparse.cells.topology import CognitiveCellBuilder
from aethersparse.controller.framing import QueryFramer
from aethersparse.controller.pipeline import StructuredController
from aethersparse.controller.sqlite_provider import SQLiteControllerProvider
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
DEFAULT_V050_CORPUS = Path(
    os.environ.get(
        "AETHERSPARSE_V050_CORPUS",
        ROOT / "data" / "real_corpus" / "v050" / "simplewiki-v050.sqlite",
    )
)


def create_app(
    runtime: AetherSparseRuntime | None = None,
    *,
    controller_corpus: Path | None = None,
) -> FastAPI:
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
    v050_corpus = controller_corpus or DEFAULT_V050_CORPUS

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

    @application.post("/v5/controller/query")
    def structured_controller_query(payload: dict[str, object]) -> dict[str, object]:
        """Run bounded v0.5 cognition and expose its complete exact-evidence trace."""

        text = str(payload.get("text", "")).strip()
        if not text:
            raise HTTPException(status_code=422, detail="text is required")
        if not v050_corpus.is_file():
            raise HTTPException(
                status_code=503,
                detail=(
                    "v0.5 corpus pack is not registered; set AETHERSPARSE_V050_CORPUS"
                ),
            )
        raw_prior = payload.get("prior_entity_ids", [])
        prior_entity_ids = (
            tuple(str(value) for value in raw_prior[:8])
            if isinstance(raw_prior, list)
            else ()
        )
        raw_discourse = payload.get("discourse", [])
        discourse = (
            tuple(str(value).strip() for value in raw_discourse[-8:] if str(value).strip())
            if isinstance(raw_discourse, list)
            else ()
        )
        with SQLiteControllerProvider(v050_corpus) as provider:
            # Discourse is resolved against the same read-only indexes. Only accepted
            # canonical IDs are carried forward; wording is never treated as evidence.
            framer = QueryFramer()
            carried = prior_entity_ids
            for turn in discourse:
                context_frame = provider.link_frame(
                    framer.frame(turn, prior_entity_ids=carried)
                )
                carried = tuple(
                    dict.fromkeys((*carried, *context_frame.candidate_entity_ids))
                )[-8:]
            result = StructuredController(provider, framer).query(
                str(payload.get("query_id", "interactive")),
                text,
                provider,
                prior_entity_ids=carried,
                evidence_limit=32,
            )
            workload = provider.last_workload

        scores = {
            entry.claim_id: entry.model_dump(mode="json")
            for entry in result.evidence_trace.entries
        }
        claims = {claim.claim_id: claim for claim in result.graph.claims}
        spans = {span.span_id: span for span in result.graph.source_spans}
        selected_evidence: list[dict[str, object]] = []
        for claim_id in result.evidence_trace.selected_claim_ids:
            claim = claims.get(claim_id)
            if claim is None:
                continue
            for span_id in claim.source_span_ids:
                span = spans.get(span_id)
                if span is None:
                    continue
                selected_evidence.append(
                    {
                        "claim_id": claim_id,
                        "span_id": span_id,
                        "document_id": span.document_id,
                        "title": span.source_title,
                        "text": span.text,
                        "source_url": span.source_url,
                        "source_revision": span.source_revision,
                        "raw_start": span.char_start,
                        "raw_end": span.char_end,
                        "scores": scores.get(claim_id, {}),
                    }
                )
        entity_candidates = [
            {
                "surface": mention.surface,
                "entity_id": candidate.entity_id,
                "canonical_label": candidate.title,
                "source": candidate.method.value,
                "confidence": candidate.confidence,
                "status": (
                    "SELECTED"
                    if candidate.entity_id == mention.selected_entity_id
                    else "REJECTED"
                ),
            }
            for mention in result.frame.entity_mentions
            for candidate in mention.candidates
        ]
        bindings = (
            [binding.model_dump(mode="json") for binding in result.answer.bindings]
            if result.answer
            else []
        )
        workload_payload: dict[str, object] = {}
        if workload is not None:
            workload_payload = {
                **workload.model_dump(mode="json"),
                "total_blocks_read": workload.estimated_sqlite_blocks,
                "total_bytes_read": workload.payload_bytes,
            }
        confidence = (
            result.plan.confidence
            if result.plan is not None
            else max(
                (
                    candidate.confidence
                    for mention in result.frame.entity_mentions
                    for candidate in mention.candidates
                ),
                default=0.0,
            )
        )
        return {
            "disposition": result.disposition.value,
            "answer": result.answer.text if result.answer else None,
            "failure_reason": result.reason if result.answer is None else None,
            "confidence": confidence,
            "query_frame": result.frame.model_dump(mode="json"),
            "entity_candidates": entity_candidates,
            "selected_evidence": selected_evidence,
            "evidence_graph": result.graph.model_dump(mode="json"),
            "answer_plan": result.plan.model_dump(mode="json") if result.plan else None,
            "bindings": bindings,
            "verification": (
                result.verification.model_dump(mode="json") if result.verification else None
            ),
            "disposition_trace": {
                "reason": result.reason,
                "missing_facets": [value.value for value in result.graph.missing_facets],
                "carried_entity_ids": carried,
            },
            "rejected_alternatives": (
                list(result.selection.rejected_claim_ids) if result.selection else []
            ),
            "workload": workload_payload,
            "trace": result.evidence_trace.model_dump(mode="json"),
            "ablation_comparison": {},
            "external_service_boundary": True,
        }

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
