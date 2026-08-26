"""Deterministic authoritative tier manager and user-memory mutation boundary."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from .models import (
    DeletionState,
    MemoryAuthority,
    MemoryJournalEntry,
    MemoryManagerState,
    MemoryPayload,
    MemoryProvenance,
    MemoryRecord,
    MemoryType,
    MemoryWatermarks,
    PhysicalResidency,
    SemanticTier,
)


class MemoryAuthorizationError(PermissionError):
    pass


_NEXT_TIER = {
    SemanticTier.EPHEMERAL: SemanticTier.SHORT_TERM,
    SemanticTier.SHORT_TERM: SemanticTier.WORKING,
    SemanticTier.WORKING: SemanticTier.LONG_TERM,
}


class MemoryTierManager:
    """Owns logical lifetime; cache/resource code independently owns residency."""

    def __init__(self, state: MemoryManagerState | None = None) -> None:
        state = state or MemoryManagerState()
        self.epoch = state.epoch
        self.next_id = state.next_id
        self.watermarks = state.watermarks
        self._records = {record.memory_id: record for record in state.records}
        self._journal = list(state.journal_tail)
        self._authorizations: set[str] = set()

    @staticmethod
    def _hash(payload: MemoryPayload) -> str:
        canonical = json.dumps(
            payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
        return f"sha256:{hashlib.sha256(canonical).hexdigest()}"

    def _tick(self) -> int:
        self.epoch += 1
        self.reclaim_expired()
        return self.epoch

    def _entry(
        self,
        operation: str,
        memory_id: str,
        *,
        from_tier: SemanticTier | None = None,
        to_tier: SemanticTier | None = None,
        reason: str = "",
    ) -> None:
        self._journal.append(
            MemoryJournalEntry(
                epoch=self.epoch,
                operation=operation,
                memory_id=memory_id,
                from_tier=from_tier,
                to_tier=to_tier,
                reason=reason,
            )
        )
        self._journal = self._journal[-128:]

    def authorize_user_mutation(self, authorization_id: str) -> None:
        if not authorization_id or len(authorization_id) > 128:
            raise ValueError("invalid user-memory authorization")
        self._authorizations.add(authorization_id)

    def _consume_authorization(self, authorization_id: str) -> None:
        if authorization_id not in self._authorizations:
            raise MemoryAuthorizationError("explicit user authorization is required")
        self._authorizations.remove(authorization_id)

    def create(
        self,
        *,
        memory_type: MemoryType,
        semantic_tier: SemanticTier,
        payload: MemoryPayload,
        provenance: MemoryProvenance,
        residency: PhysicalResidency = PhysicalResidency.COLD,
        source_evidence_handle: str | None = None,
        ttl_epochs: int | None = None,
        confidence_milli: int = 1000,
        salience_milli: int = 0,
        novelty_milli: int = 0,
        cog_bindings: tuple[str, ...] = (),
        session_scope: str | None = None,
        user_scope: str | None = None,
        pinned: bool = False,
        verification_bound: bool = False,
        authorization_id: str | None = None,
    ) -> MemoryRecord:
        if memory_type is MemoryType.USER_MEMORY:
            if authorization_id is None:
                raise MemoryAuthorizationError("explicit user authorization is required")
            self._consume_authorization(authorization_id)
        epoch = self._tick()
        identifier = f"mem-{self.next_id:08d}"
        self.next_id += 1
        record = MemoryRecord(
            memory_id=identifier,
            memory_type=memory_type,
            semantic_tier=semantic_tier,
            residency=residency,
            provenance=provenance,
            payload=payload,
            source_evidence_handle=source_evidence_handle,
            created_epoch=epoch,
            modified_epoch=epoch,
            last_access_epoch=epoch,
            confidence_milli=confidence_milli,
            salience_milli=salience_milli,
            novelty_milli=novelty_milli,
            expires_epoch=epoch + ttl_epochs if ttl_epochs is not None else None,
            content_hash=self._hash(payload),
            cog_bindings=cog_bindings,
            session_scope=session_scope,
            user_scope=user_scope,
            pinned=pinned,
            verification_bound=verification_bound,
        )
        self._records[identifier] = record
        self._entry("CREATE", identifier, to_tier=semantic_tier)
        self._enforce_bound(semantic_tier)
        return record

    def get(self, memory_id: str, *, include_deleted: bool = False) -> MemoryRecord:
        epoch = self._tick()
        record = self._records[memory_id]
        if record.deletion_state is DeletionState.TOMBSTONED and not include_deleted:
            raise KeyError(memory_id)
        updated = record.model_copy(
            update={"last_access_epoch": epoch, "access_count": record.access_count + 1}
        )
        self._records[memory_id] = updated
        return updated

    def records(self, *, include_deleted: bool = False) -> tuple[MemoryRecord, ...]:
        return tuple(
            record
            for record in sorted(self._records.values(), key=lambda item: item.memory_id)
            if include_deleted or record.deletion_state is DeletionState.ACTIVE
        )

    def promote(self, memory_id: str, *, reason: str) -> MemoryRecord:
        record = self._records[memory_id]
        target = _NEXT_TIER.get(record.semantic_tier)
        if target is None:
            return record
        # Promotion never changes authority. Inference remains inference.
        if target is SemanticTier.LONG_TERM and record.memory_type not in {
            MemoryType.EXTERNAL_KNOWLEDGE,
            MemoryType.USER_MEMORY,
            MemoryType.LEARNED_SPECIALIST_STATE,
            MemoryType.PROJECT_KNOWLEDGE,
        }:
            raise ValueError("ordinary working state cannot be promoted into long-term authority")
        epoch = self._tick()
        updated = record.model_copy(
            update={"semantic_tier": target, "modified_epoch": epoch}
        )
        self._records[memory_id] = updated
        self._entry(
            "PROMOTE", memory_id, from_tier=record.semantic_tier, to_tier=target, reason=reason
        )
        self._enforce_bound(target)
        return updated

    @staticmethod
    def promotion_score(record: MemoryRecord) -> int:
        """Combine salience, reuse, novelty, provenance, and confidence deterministically."""

        authority_bonus = {
            MemoryAuthority.EXTERNAL_GROUNDED: 200,
            MemoryAuthority.USER_ASSERTED: 200,
            MemoryAuthority.OBSERVATION: 100,
            MemoryAuthority.PROJECT_APPROVED: 200,
            MemoryAuthority.SYSTEM: 100,
            MemoryAuthority.LEARNED_STATE: 50,
            MemoryAuthority.INFERENCE: 0,
        }[record.provenance.authority]
        reuse = min(record.access_count, 20) * 50
        return (
            record.salience_milli
            + record.novelty_milli
            + record.confidence_milli
            + reuse
            + authority_bonus
        )

    def promotion_candidates(self, *, minimum_score: int) -> tuple[MemoryRecord, ...]:
        candidates = (
            record
            for record in self._records.values()
            if record.deletion_state is DeletionState.ACTIVE
            and record.semantic_tier is not SemanticTier.LONG_TERM
            and self.promotion_score(record) >= minimum_score
        )
        return tuple(
            sorted(
                candidates,
                key=lambda item: (-self.promotion_score(item), item.memory_id),
            )
        )

    def demote(self, memory_id: str, target: SemanticTier, *, reason: str) -> MemoryRecord:
        record = self._records[memory_id]
        order = list(SemanticTier)
        if order.index(target) >= order.index(record.semantic_tier):
            raise ValueError("demotion target must have a shorter semantic lifetime")
        if record.pinned or record.verification_bound:
            raise ValueError("pinned or verification-bound memory cannot be demoted")
        epoch = self._tick()
        updated = record.model_copy(
            update={"semantic_tier": target, "modified_epoch": epoch}
        )
        self._records[memory_id] = updated
        self._entry(
            "DEMOTE", memory_id, from_tier=record.semantic_tier, to_tier=target, reason=reason
        )
        self._enforce_bound(target)
        return updated

    def set_residency(self, memory_id: str, residency: PhysicalResidency) -> MemoryRecord:
        record = self._records[memory_id]
        updated = record.model_copy(update={"residency": residency})
        self._records[memory_id] = updated
        self._entry("RESIDENCY", memory_id, reason=residency.value)
        return updated

    def bind_selected_evidence(
        self,
        *,
        payload: MemoryPayload,
        provenance: MemoryProvenance,
        evidence_handle: str,
        cog_bindings: tuple[str, ...],
        session_scope: str,
    ) -> MemoryRecord:
        return self.create(
            memory_type=MemoryType.SELECTED_EVIDENCE,
            semantic_tier=SemanticTier.WORKING,
            residency=PhysicalResidency.HOT,
            payload=payload,
            provenance=provenance,
            source_evidence_handle=evidence_handle,
            cog_bindings=cog_bindings,
            session_scope=session_scope,
            pinned=True,
        )

    def mark_verification_bound(self, memory_id: str) -> MemoryRecord:
        record = self._records[memory_id]
        if record.memory_type is not MemoryType.SELECTED_EVIDENCE:
            raise ValueError("only selected evidence can become verification-bound")
        epoch = self._tick()
        updated = record.model_copy(
            update={"pinned": True, "verification_bound": True, "modified_epoch": epoch}
        )
        self._records[memory_id] = updated
        self._entry("VERIFY_PIN", memory_id)
        return updated

    def reclaim_expired(self) -> int:
        expired = [
            identifier
            for identifier, record in self._records.items()
            if record.expires_epoch is not None
            and record.expires_epoch <= self.epoch
            and not record.pinned
            and record.semantic_tier in {SemanticTier.EPHEMERAL, SemanticTier.SHORT_TERM}
        ]
        for identifier in expired:
            del self._records[identifier]
            self._entry("EXPIRE", identifier)
        return len(expired)

    def advance(self, epochs: int = 1) -> int:
        if epochs < 0:
            raise ValueError("epochs cannot be negative")
        self.epoch += epochs
        return self.reclaim_expired()

    def _enforce_bound(self, tier: SemanticTier) -> None:
        limit = {
            SemanticTier.EPHEMERAL: self.watermarks.ephemeral_limit,
            SemanticTier.SHORT_TERM: self.watermarks.short_term_limit,
            SemanticTier.WORKING: self.watermarks.working_limit,
            SemanticTier.LONG_TERM: self.watermarks.long_term_limit,
        }[tier]
        active = [
            record
            for record in self._records.values()
            if record.semantic_tier is tier and record.deletion_state is DeletionState.ACTIVE
        ]
        while len(active) > limit:
            victims = [record for record in active if not record.pinned]
            if not victims:
                raise MemoryError(f"{tier} bound exhausted by pinned records")
            victim = min(
                victims,
                key=lambda item: (
                    item.last_access_epoch,
                    item.salience_milli,
                    item.created_epoch,
                    item.memory_id,
                ),
            )
            del self._records[victim.memory_id]
            self._entry("EVICT", victim.memory_id, from_tier=tier, reason="BOUND")
            active.remove(victim)

    def search_user(self, user_scope: str, query: str) -> tuple[MemoryRecord, ...]:
        terms = tuple(term for term in query.casefold().split() if term)
        matches = [
            record
            for record in self._records.values()
            if record.memory_type is MemoryType.USER_MEMORY
            and record.user_scope == user_scope
            and record.deletion_state is DeletionState.ACTIVE
            and all(term in record.payload.text.casefold() for term in terms)
        ]
        return tuple(sorted(matches, key=lambda item: (-item.salience_milli, item.memory_id)))

    def edit_user(
        self,
        memory_id: str,
        payload: MemoryPayload,
        *,
        authorization_id: str,
    ) -> MemoryRecord:
        self._consume_authorization(authorization_id)
        record = self._records[memory_id]
        if record.memory_type is not MemoryType.USER_MEMORY:
            raise ValueError("only user memory may be edited through this API")
        if record.deletion_state is DeletionState.TOMBSTONED:
            raise KeyError(memory_id)
        epoch = self._tick()
        updated = record.model_copy(
            update={
                "payload": payload,
                "content_hash": self._hash(payload),
                "modified_epoch": epoch,
            }
        )
        self._records[memory_id] = updated
        self._entry("EDIT", memory_id)
        return updated

    def delete_user(self, memory_id: str, *, authorization_id: str) -> MemoryRecord:
        self._consume_authorization(authorization_id)
        record = self._records[memory_id]
        if record.memory_type is not MemoryType.USER_MEMORY:
            raise ValueError("immutable external knowledge cannot be deleted through user memory")
        epoch = self._tick()
        updated = record.model_copy(
            update={"deletion_state": DeletionState.TOMBSTONED, "modified_epoch": epoch}
        )
        self._records[memory_id] = updated
        self._entry("TOMBSTONE", memory_id)
        return updated

    def compact_tombstones(self, *, before_epoch: int) -> int:
        victims = [
            identifier
            for identifier, record in self._records.items()
            if record.deletion_state is DeletionState.TOMBSTONED
            and record.modified_epoch <= before_epoch
        ]
        for identifier in victims:
            del self._records[identifier]
            self._entry("COMPACT", identifier)
        return len(victims)

    def export_state(self) -> MemoryManagerState:
        # Ephemeral scratch is intentionally excluded from authoritative persistence.
        records = tuple(
            record
            for record in sorted(self._records.values(), key=lambda item: item.memory_id)
            if record.semantic_tier is not SemanticTier.EPHEMERAL
        )
        return MemoryManagerState(
            epoch=self.epoch,
            next_id=self.next_id,
            records=records,
            watermarks=self.watermarks,
            journal_tail=tuple(self._journal),
        )

    @classmethod
    def with_limits(
        cls,
        *,
        ephemeral: int = 128,
        short_term: int = 64,
        working: int = 128,
        long_term: int = 4096,
    ) -> MemoryTierManager:
        return cls(
            MemoryManagerState(
                watermarks=MemoryWatermarks(
                    ephemeral_limit=ephemeral,
                    short_term_limit=short_term,
                    working_limit=working,
                    long_term_limit=long_term,
                )
            )
        )

    def import_records(self, records: Iterable[MemoryRecord]) -> None:
        for record in records:
            if record.memory_id in self._records:
                raise ValueError("duplicate imported memory handle")
            self._records[record.memory_id] = record
