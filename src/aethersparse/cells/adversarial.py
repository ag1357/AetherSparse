"""Deterministic adversarial mutations and exact claim verification."""

from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class MutationKind(StrEnum):
    ENTITY_SWAP = "entity_swap"
    DATE_SWAP = "date_swap"
    QUANTITY_SWAP = "quantity_swap"
    NEGATION = "negation"
    RELATION_REVERSAL = "relation_reversal"
    ATTRIBUTION_SWAP = "attribution_swap"
    UNSUPPORTED_ADDITION = "unsupported_addition"


class BoundClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    claim_id: str
    subject_id: str
    relation_id: str
    object_value: str
    source_span_id: str
    source_text: str
    polarity: int = 1
    attribution_id: str | None = None


class MutatedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mutation_id: str
    kind: MutationKind
    original: BoundClaim
    candidate: BoundClaim


class AdversarialMutator:
    """Creates reproducible wrong claims without using the verifier."""

    @staticmethod
    def _id(claim: BoundClaim, kind: MutationKind) -> str:
        return "mutation:" + hashlib.sha256(f"{claim.claim_id}:{kind}".encode()).hexdigest()[:20]

    @staticmethod
    def _different(value: str, replacement: str) -> str:
        return replacement if value != replacement else replacement + ":alt"

    def mutate(self, claim: BoundClaim) -> tuple[MutatedClaim, ...]:
        replacements = {
            MutationKind.ENTITY_SWAP: claim.model_copy(
                update={"subject_id": claim.subject_id + ":wrong"}
            ),
            MutationKind.DATE_SWAP: claim.model_copy(
                update={"object_value": self._different(claim.object_value, "2099")}
            ),
            MutationKind.QUANTITY_SWAP: claim.model_copy(
                update={"object_value": self._different(claim.object_value, "999 wrong-units")}
            ),
            MutationKind.NEGATION: claim.model_copy(
                update={"polarity": -1 if claim.polarity >= 0 else 1}
            ),
            MutationKind.RELATION_REVERSAL: claim.model_copy(
                update={"relation_id": claim.relation_id + ":reversed"}
            ),
            MutationKind.ATTRIBUTION_SWAP: claim.model_copy(
                update={
                    "attribution_id": self._different(
                        claim.attribution_id or "", "entity:wrong-speaker"
                    )
                }
            ),
            MutationKind.UNSUPPORTED_ADDITION: claim.model_copy(
                update={"object_value": claim.object_value + " unsupported"}
            ),
        }
        return tuple(
            MutatedClaim(
                mutation_id=self._id(claim, kind),
                kind=kind,
                original=claim,
                candidate=candidate,
            )
            for kind, candidate in replacements.items()
        )


class ExactClaimVerifier:
    """The exact ledger remains authoritative; learned probes may only add vetoes."""

    def verify(self, candidate: BoundClaim, canonical: BoundClaim) -> tuple[bool, tuple[str, ...]]:
        failures: list[str] = []
        for field in (
            "subject_id",
            "relation_id",
            "object_value",
            "source_span_id",
            "polarity",
            "attribution_id",
        ):
            if getattr(candidate, field) != getattr(canonical, field):
                failures.append(field)
        if canonical.source_text not in candidate.source_text:
            failures.append("source_text")
        return not failures, tuple(failures)


def mutation_rejection_report(claims: tuple[BoundClaim, ...]) -> dict[str, object]:
    mutator = AdversarialMutator()
    verifier = ExactClaimVerifier()
    counts = {kind.value: {"tested": 0, "rejected": 0} for kind in MutationKind}
    for claim in claims:
        for mutation in mutator.mutate(claim):
            passed, _failures = verifier.verify(mutation.candidate, claim)
            counts[mutation.kind.value]["tested"] += 1
            counts[mutation.kind.value]["rejected"] += int(not passed)
    tested = sum(int(value["tested"]) for value in counts.values())
    rejected = sum(int(value["rejected"]) for value in counts.values())
    return {
        "claim_count": len(claims),
        "mutation_count": tested,
        "rejection_rate": rejected / max(1, tested),
        "by_kind": counts,
        "learned_verifier_present": False,
        "exact_verifier_authoritative": True,
    }
