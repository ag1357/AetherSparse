"""Bounded records whose semantic lifetime is independent of physical residency."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SemanticTier(StrEnum):
    EPHEMERAL = "EPHEMERAL"
    SHORT_TERM = "SHORT_TERM"
    WORKING = "WORKING"
    LONG_TERM = "LONG_TERM"


class PhysicalResidency(StrEnum):
    COLD = "COLD"
    WARM = "WARM"
    HOT = "HOT"


class MemoryAuthority(StrEnum):
    EXTERNAL_GROUNDED = "EXTERNAL_GROUNDED"
    USER_ASSERTED = "USER_ASSERTED"
    OBSERVATION = "OBSERVATION"
    INFERENCE = "INFERENCE"
    SYSTEM = "SYSTEM"
    PROJECT_APPROVED = "PROJECT_APPROVED"
    LEARNED_STATE = "LEARNED_STATE"


class MemoryType(StrEnum):
    SCRATCH = "SCRATCH"
    CANDIDATE_FEATURE = "CANDIDATE_FEATURE"
    UNCOMMITTED_TOOL_RESULT = "UNCOMMITTED_TOOL_RESULT"
    HYPOTHESIS = "HYPOTHESIS"
    CONVERSATION_TURN = "CONVERSATION_TURN"
    ACTIVE_REFERENCE = "ACTIVE_REFERENCE"
    TASK_STATE = "TASK_STATE"
    COG_ITEM = "COG_ITEM"
    SELECTED_EVIDENCE = "SELECTED_EVIDENCE"
    EXTERNAL_KNOWLEDGE = "EXTERNAL_KNOWLEDGE"
    USER_MEMORY = "USER_MEMORY"
    LEARNED_SPECIALIST_STATE = "LEARNED_SPECIALIST_STATE"
    PROJECT_KNOWLEDGE = "PROJECT_KNOWLEDGE"


class DeletionState(StrEnum):
    ACTIVE = "ACTIVE"
    TOMBSTONED = "TOMBSTONED"


class MemoryPayload(FrozenModel):
    """Semantic content keeps qualifiers that must survive promotion."""

    text: str = Field(min_length=1, max_length=4096)
    negated: bool = False
    quantity: str | None = Field(default=None, max_length=128)
    unit: str | None = Field(default=None, max_length=64)
    uncertainty_milli: int = Field(default=0, ge=0, le=1000)
    perspective: str | None = Field(default=None, max_length=256)


class MemoryProvenance(FrozenModel):
    authority: MemoryAuthority
    source_id: str = Field(min_length=1, max_length=256)
    evidence_handle: str | None = Field(default=None, max_length=256)
    derivation_ids: tuple[str, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def evidence_for_external_fact(self) -> MemoryProvenance:
        if self.authority is MemoryAuthority.EXTERNAL_GROUNDED and not self.evidence_handle:
            raise ValueError("external grounded memory requires an evidence handle")
        return self


class MemoryRecord(FrozenModel):
    memory_id: str = Field(min_length=1, max_length=96)
    memory_type: MemoryType
    semantic_tier: SemanticTier
    residency: PhysicalResidency
    provenance: MemoryProvenance
    payload: MemoryPayload
    source_evidence_handle: str | None = Field(default=None, max_length=256)
    created_epoch: int = Field(ge=0)
    modified_epoch: int = Field(ge=0)
    last_access_epoch: int = Field(ge=0)
    access_count: int = Field(default=0, ge=0)
    confidence_milli: int = Field(default=1000, ge=0, le=1000)
    salience_milli: int = Field(default=0, ge=0, le=1000)
    novelty_milli: int = Field(default=0, ge=0, le=1000)
    expires_epoch: int | None = Field(default=None, ge=0)
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    cog_bindings: tuple[str, ...] = Field(default=(), max_length=16)
    session_scope: str | None = Field(default=None, max_length=128)
    user_scope: str | None = Field(default=None, max_length=128)
    deletion_state: DeletionState = DeletionState.ACTIVE
    pinned: bool = False
    verification_bound: bool = False

    @model_validator(mode="after")
    def enforce_authority_and_lifetime(self) -> MemoryRecord:
        if self.modified_epoch < self.created_epoch:
            raise ValueError("modified epoch precedes creation")
        if self.last_access_epoch < self.created_epoch:
            raise ValueError("last access precedes creation")
        if self.expires_epoch is not None and self.expires_epoch < self.created_epoch:
            raise ValueError("expiry precedes creation")
        if self.memory_type is MemoryType.USER_MEMORY:
            if self.semantic_tier is not SemanticTier.LONG_TERM:
                raise ValueError("user memory is long-term")
            if self.provenance.authority is not MemoryAuthority.USER_ASSERTED:
                raise ValueError("user memory requires USER_ASSERTED provenance")
            if not self.user_scope:
                raise ValueError("user memory requires a user scope")
        if (
            self.memory_type is MemoryType.SELECTED_EVIDENCE
            and (self.semantic_tier is not SemanticTier.WORKING or not self.pinned)
        ):
            raise ValueError("selected evidence must be pinned working memory")
        if self.verification_bound and not self.pinned:
            raise ValueError("verification-bound memory must be pinned")
        return self


class MemoryWatermarks(FrozenModel):
    ephemeral_limit: int = Field(default=128, ge=1, le=4096)
    short_term_limit: int = Field(default=64, ge=1, le=4096)
    working_limit: int = Field(default=128, ge=1, le=4096)
    long_term_limit: int = Field(default=4096, ge=1, le=1_000_000)


class MemoryJournalEntry(FrozenModel):
    epoch: int = Field(ge=0)
    operation: str = Field(min_length=1, max_length=32)
    memory_id: str = Field(min_length=1, max_length=96)
    from_tier: SemanticTier | None = None
    to_tier: SemanticTier | None = None
    reason: str = Field(default="", max_length=192)


class MemoryManagerState(FrozenModel):
    schema_version: str = "aethercore.memory.v1"
    epoch: int = Field(default=0, ge=0)
    next_id: int = Field(default=1, ge=1)
    records: tuple[MemoryRecord, ...] = ()
    watermarks: MemoryWatermarks = Field(default_factory=MemoryWatermarks)
    journal_tail: tuple[MemoryJournalEntry, ...] = Field(default=(), max_length=128)

    @model_validator(mode="after")
    def unique_handles(self) -> MemoryManagerState:
        identifiers = [record.memory_id for record in self.records]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("duplicate memory handle")
        return self
