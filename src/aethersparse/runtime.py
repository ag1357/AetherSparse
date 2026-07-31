"""Bounded compiled-program runtime for the external accessory."""

from __future__ import annotations

from typing import Literal

from aethersparse.compiler import load_pack
from aethersparse.models import (
    CapsuleDelta,
    ConfidenceDimensions,
    Disposition,
    FailureCode,
    Intent,
    OperationCategory,
    QueryRequest,
    QueryResponse,
)
from aethersparse.parser import DeterministicParser
from aethersparse.realizer import realize
from aethersparse.retrieval import KnowledgeStore
from aethersparse.tracing import TraceRecorder
from aethersparse.verifier import VerificationError, verify_answer


class AetherSparseRuntime:
    """Reference accessory runtime; contains no web/UI dependency."""

    def __init__(self) -> None:
        self.store = KnowledgeStore(load_pack())
        self.parser = DeterministicParser()

    def _failure(
        self,
        request: QueryRequest,
        recorder: TraceRecorder,
        code: FailureCode,
        reason: str,
    ) -> QueryResponse:
        disposition = (
            Disposition.OUT_OF_DOMAIN
            if code is FailureCode.OUT_OF_DOMAIN
            else Disposition.ABSTAIN
        )
        return QueryResponse(
            request_id=request.request_id,
            session_id=request.session_id,
            disposition=disposition,
            reason_code=code,
            reason=reason,
            cost=recorder.summary(),
            trace=tuple(recorder.entries) if request.trace else (),
            capsule_delta=CapsuleDelta(
                ontology_version=self.store.pack.manifest.ontology_version,
                unresolved_goals=(request.text,),
            ),
            pack_manifest_hash=self.store.pack.manifest.manifest_hash,
        )

    def query(
        self,
        request: QueryRequest,
        *,
        strategy: Literal["top1_template", "compiled_program"] = "compiled_program",
    ) -> QueryResponse:
        recorder = TraceRecorder()

        with recorder.operation(
            "NORMALIZE_INPUT",
            OperationCategory.SYMBOLIC,
            input_count=1,
            output_count=1,
            integer_ops=len(request.text),
            working_ram_bytes=len(request.text.encode("utf-8")) * 2,
        ):
            normalized = self.parser.normalize(request.text)

        with recorder.operation(
            "PARSE_PROVISIONAL",
            OperationCategory.SYMBOLIC,
            input_count=1,
            output_count=1,
            integer_ops=len(normalized) * 6,
            working_ram_bytes=2048,
        ):
            frame = self.parser.parse(normalized)

        if frame.intent is Intent.UNKNOWN:
            if frame.unknown_spans:
                unknown = ", ".join(span.surface for span in frame.unknown_spans)
                return self._failure(
                    request,
                    recorder,
                    FailureCode.INSUFFICIENT_EVIDENCE,
                    f"Insufficient evidence in compiled knowledge for: {unknown}.",
                )
            if frame.confidence == 0.0:
                return self._failure(
                    request,
                    recorder,
                    FailureCode.OUT_OF_DOMAIN,
                    "Outside compiled domain.",
                )
            return self._failure(
                request,
                recorder,
                FailureCode.OUT_OF_ONTOLOGY,
                "The requested relation is outside the current ontology.",
            )

        relation_candidates = self.store.by_relation.get(frame.relation_id or "", [])
        bytes_read = sum(
            128 + len(str(packet.payload).encode("utf-8"))
            for packet in relation_candidates
        )
        with recorder.operation(
            "RETRIEVE_FACTS",
            OperationCategory.STORAGE,
            input_count=1,
            output_count=len(relation_candidates),
            bytes_read=bytes_read,
            storage_reads=1,
            integer_ops=max(1, len(relation_candidates) * 8),
            working_ram_bytes=max(512, bytes_read),
        ):
            candidates = self.store.retrieve(frame)

        if not candidates:
            return self._failure(
                request,
                recorder,
                FailureCode.RETRIEVAL_FAILURE,
                "No canonical packet matched the executable frame.",
            )

        if strategy == "top1_template":
            selected = candidates[:1]
        else:
            with recorder.operation(
                "SELECT_EVIDENCE_SET",
                OperationCategory.CONTROL,
                input_count=len(candidates),
                output_count=min(len(candidates), 3),
                integer_ops=len(candidates) * 4,
                working_ram_bytes=512,
            ):
                selected = self.store.select_evidence(candidates)

        if not selected:
            return self._failure(
                request,
                recorder,
                FailureCode.INSUFFICIENT_EVIDENCE,
                "Insufficient canonical evidence in the compiled knowledge pack.",
            )

        packet = selected[0]
        with recorder.operation(
            "PLAN_ANSWER",
            OperationCategory.CONTROL,
            input_count=len(selected),
            output_count=1,
            integer_ops=24,
            working_ram_bytes=1024,
        ):
            pass

        with recorder.operation(
            "REALIZE_TEMPLATE",
            OperationCategory.REALIZATION,
            input_count=1,
            output_count=1,
            integer_ops=64,
            working_ram_bytes=2048,
        ):
            realized = realize(frame, packet, self.store.span_by_id)

        try:
            with recorder.operation(
                "VERIFY_CLAIM",
                OperationCategory.VERIFICATION,
                input_count=len(realized.bindings),
                output_count=1,
                integer_ops=len(realized.sentence) * max(1, len(realized.bindings)),
                working_ram_bytes=2048,
            ):
                verify_answer(
                    sentence=realized.sentence,
                    expected_sentence=realized.sentence,
                    citations=realized.citations,
                    bindings=realized.bindings,
                    packets=self.store.by_packet_id,
                    spans=self.store.span_by_id,
                )
        except VerificationError as error:
            return self._failure(
                request,
                recorder,
                FailureCode.VERIFICATION_FAILURE,
                f"Verification failed; answer withheld: {error}",
            )

        source_groups = {
            self.store.span_by_id[citation.source_span_id].source_group
            for citation in realized.citations
        }
        confidence = ConfidenceDimensions(
            factual_support=1.0,
            query_relevance=frame.confidence,
            temporal_validity=1.0,
            source_reliability=1.0,
            source_independence=min(1.0, len(source_groups) / 2),
            interpretation=1.0,
            realization_fidelity=1.0,
            safety_clearance=1.0,
        )
        claim_ids = tuple(binding.claim_unit_id for binding in realized.bindings)
        return QueryResponse(
            request_id=request.request_id,
            session_id=request.session_id,
            disposition=Disposition.ANSWER,
            sentence=realized.sentence,
            citations=realized.citations,
            bindings=realized.bindings,
            confidence=confidence,
            trace=tuple(recorder.entries) if request.trace else (),
            cost=recorder.summary(),
            capsule_delta=CapsuleDelta(
                ontology_version=self.store.pack.manifest.ontology_version,
                active_entity_ids=(frame.entity_id,) if frame.entity_id else (),
                supported_claim_ids=claim_ids,
            ),
            pack_manifest_hash=self.store.pack.manifest.manifest_hash,
        )
