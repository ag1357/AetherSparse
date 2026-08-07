"""Typed value-candidate schema for the answer controller (Mission 4 Phase 0B).

All extractors emit this one shared structure.  Selection scores on entity,
relation, scope, attribution, and facet compatibility — lexical proximity may
be a feature, never the mechanism.
"""

from __future__ import annotations

from pydantic import Field

from aethersparse.selection.models import FrozenModel


class ValueCandidate(FrozenModel):
    """One enumerated candidate value with full provenance and bindings."""

    source_span_id: str
    provenance: str
    """document id + revision + byte offsets, e.g. 'mw:123:rev@120:240'."""
    raw_surface: str
    """Verbatim text the candidate was extracted from."""
    canonical_value: str
    """Deterministic normalized form (dates, quantities, entity ids)."""
    value_type: str
    """date | quantity | entity | quote | name | ... """
    subject_entity: str | None = None
    """Candidate subject binding (canonical entity id)."""
    relation: str | None = None
    """Candidate relation/attribute binding."""
    time_scope: str | None = None
    """Temporal scope of the assertion, when bound."""
    unit: str | None = None
    """Unit for quantities."""
    attribution: str | None = None
    """Speaker/source for quotations."""
    section_context: str = ""
    """Heading path of the source span."""
    local_predicate_features: tuple[str, ...] = Field(default_factory=tuple)
    """Verb, negation, modality, hedging markers observed at the span."""
