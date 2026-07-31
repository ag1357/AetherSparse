"""Constrained generative-address experiment; never a corpus index."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AddressCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    cell_id: str
    score: float = Field(ge=0.0, le=1.0)


class AddressDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    accepted_cell_ids: tuple[str, ...]
    rejected_cell_ids: tuple[str, ...]
    fallback_required: bool
    reason: str


class GenerativeAddressGate:
    """Validate predicted IDs and force fallback on uncertainty or unknowns."""

    def __init__(
        self,
        registry: set[str],
        *,
        confidence_floor: float = 0.70,
        ambiguity_margin: float = 0.04,
        limit: int = 8,
    ):
        self.registry = registry
        self.confidence_floor = confidence_floor
        self.ambiguity_margin = ambiguity_margin
        self.limit = limit

    def decide(
        self,
        candidates: tuple[AddressCandidate, ...],
        *,
        unknown_entity_present: bool = False,
    ) -> AddressDecision:
        ordered = sorted(candidates, key=lambda item: (-item.score, item.cell_id))
        rejected = tuple(item.cell_id for item in ordered if item.cell_id not in self.registry)
        valid = [item for item in ordered if item.cell_id in self.registry][: self.limit]
        if unknown_entity_present:
            return AddressDecision(
                accepted_cell_ids=(),
                rejected_cell_ids=rejected,
                fallback_required=True,
                reason="UNKNOWN_ENTITY",
            )
        if not valid or valid[0].score < self.confidence_floor:
            return AddressDecision(
                accepted_cell_ids=(),
                rejected_cell_ids=rejected,
                fallback_required=True,
                reason="LOW_CONFIDENCE",
            )
        ambiguous = len(valid) > 1 and valid[0].score - valid[1].score < self.ambiguity_margin
        return AddressDecision(
            accepted_cell_ids=tuple(item.cell_id for item in valid),
            rejected_cell_ids=rejected,
            fallback_required=ambiguous,
            reason="AMBIGUOUS" if ambiguous else "VALIDATED_HINTS",
        )


def address_gate_metrics(
    decisions: tuple[tuple[AddressDecision, set[str]], ...],
) -> dict[str, float | int]:
    supported = [item for item in decisions if item[1]]
    top8_hits = sum(
        bool(set(decision.accepted_cell_ids[:8]) & gold) for decision, gold in supported
    )
    invalid_reached = sum(
        any(cell_id in decision.accepted_cell_ids for cell_id in decision.rejected_cell_ids)
        for decision, _gold in decisions
    )
    return {
        "question_count": len(decisions),
        "supported_question_count": len(supported),
        "valid_id_top8_recall": top8_hits / max(1, len(supported)),
        "fallback_rate": sum(decision.fallback_required for decision, _ in decisions)
        / max(1, len(decisions)),
        "invalid_ids_reaching_retrieval": invalid_reached,
    }
