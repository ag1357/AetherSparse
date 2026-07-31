"""Mandatory provenance and deterministic-realization sentinels."""

from __future__ import annotations

import hashlib

from aethersparse.models import Citation, ClaimBinding, KnowledgePacket, SourceSpan


class VerificationError(ValueError):
    """The answer must be withheld when a mandatory sentinel fails."""


def _sha256_text(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def verify_answer(
    *,
    sentence: str,
    expected_sentence: str,
    citations: tuple[Citation, ...],
    bindings: tuple[ClaimBinding, ...],
    packets: dict[str, KnowledgePacket],
    spans: dict[str, SourceSpan],
) -> None:
    """Verify direct bindings independently of the input parser."""

    if sentence != expected_sentence:
        raise VerificationError("deterministic realization differs from approved answer plan")
    if not bindings:
        raise VerificationError("answer has no direct claim bindings")
    if not citations:
        raise VerificationError("answer has no citation")

    cited_span_ids = {citation.source_span_id for citation in citations}
    for citation in citations:
        span = spans.get(citation.source_span_id)
        if span is None:
            raise VerificationError("citation references an unknown source span")
        if citation.quoted_text != span.text:
            raise VerificationError("citation text differs from immutable source span")
        if _sha256_text(citation.quoted_text) != span.text_hash:
            raise VerificationError("citation source-span hash mismatch")
        if citation.source_url != span.source_url:
            raise VerificationError("citation source URL mismatch")

    for binding in bindings:
        if sentence[binding.start : binding.end] != binding.surface:
            raise VerificationError("surface binding offsets do not match sentence")
        packet = packets.get(binding.packet_id)
        if packet is None:
            raise VerificationError("binding references an unknown packet")
        claim = next(
            (
                candidate
                for candidate in packet.atomic_claims
                if candidate.claim_unit_id == binding.claim_unit_id
            ),
            None,
        )
        if claim is None:
            raise VerificationError("binding references an unknown atomic claim")
        if not set(binding.source_span_ids).issubset(set(claim.aligned_span_ids)):
            raise VerificationError("binding is not aligned to the atomic claim evidence")
        if not set(binding.source_span_ids).issubset(cited_span_ids):
            raise VerificationError("binding evidence is not cited")

