"""Explicitly authorized user-memory CRUD, separate from external knowledge."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .manager import MemoryTierManager
from .models import (
    MemoryAuthority,
    MemoryPayload,
    MemoryProvenance,
    MemoryRecord,
    MemoryType,
    PhysicalResidency,
    SemanticTier,
)


class UserMemoryOperation(StrEnum):
    LIST_USER_MEMORY = "LIST_USER_MEMORY"
    READ_USER_MEMORY = "READ_USER_MEMORY"
    WRITE_USER_MEMORY = "WRITE_USER_MEMORY"
    EDIT_USER_MEMORY = "EDIT_USER_MEMORY"
    DELETE_USER_MEMORY = "DELETE_USER_MEMORY"
    SEARCH_USER_MEMORY = "SEARCH_USER_MEMORY"


class UserMemoryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation: UserMemoryOperation
    success: bool
    records: tuple[MemoryRecord, ...] = ()
    detail: str = Field(default="", max_length=512)


class UserMemoryService:
    def __init__(self, manager: MemoryTierManager) -> None:
        self.manager = manager

    def list(self, user_id: str) -> UserMemoryResult:
        records = tuple(
            record
            for record in self.manager.records()
            if record.memory_type is MemoryType.USER_MEMORY and record.user_scope == user_id
        )
        return UserMemoryResult(
            operation=UserMemoryOperation.LIST_USER_MEMORY,
            success=True,
            records=records,
        )

    def read(self, user_id: str, memory_id: str) -> UserMemoryResult:
        try:
            record = self.manager.get(memory_id)
        except KeyError:
            return UserMemoryResult(
                operation=UserMemoryOperation.READ_USER_MEMORY,
                success=False,
                detail="memory not found",
            )
        if record.memory_type is not MemoryType.USER_MEMORY or record.user_scope != user_id:
            return UserMemoryResult(
                operation=UserMemoryOperation.READ_USER_MEMORY,
                success=False,
                detail="memory not found",
            )
        return UserMemoryResult(
            operation=UserMemoryOperation.READ_USER_MEMORY, success=True, records=(record,)
        )

    def write(
        self,
        user_id: str,
        text: str,
        *,
        authorization_id: str,
        source_id: str,
        salience_milli: int = 500,
    ) -> UserMemoryResult:
        self.manager.authorize_user_mutation(authorization_id)
        record = self.manager.create(
            memory_type=MemoryType.USER_MEMORY,
            semantic_tier=SemanticTier.LONG_TERM,
            residency=PhysicalResidency.COLD,
            payload=MemoryPayload(text=text),
            provenance=MemoryProvenance(
                authority=MemoryAuthority.USER_ASSERTED,
                source_id=source_id,
            ),
            user_scope=user_id,
            salience_milli=salience_milli,
            novelty_milli=1000,
            authorization_id=authorization_id,
        )
        return UserMemoryResult(
            operation=UserMemoryOperation.WRITE_USER_MEMORY,
            success=True,
            records=(record,),
            detail="explicit user assertion stored",
        )

    def edit(
        self,
        user_id: str,
        memory_id: str,
        text: str,
        *,
        authorization_id: str,
    ) -> UserMemoryResult:
        current = self.read(user_id, memory_id)
        if not current.success:
            return UserMemoryResult(
                operation=UserMemoryOperation.EDIT_USER_MEMORY,
                success=False,
                detail="memory not found",
            )
        self.manager.authorize_user_mutation(authorization_id)
        record = self.manager.edit_user(
            memory_id, MemoryPayload(text=text), authorization_id=authorization_id
        )
        return UserMemoryResult(
            operation=UserMemoryOperation.EDIT_USER_MEMORY,
            success=True,
            records=(record,),
        )

    def delete(
        self,
        user_id: str,
        memory_id: str,
        *,
        authorization_id: str,
    ) -> UserMemoryResult:
        current = self.read(user_id, memory_id)
        if not current.success:
            return UserMemoryResult(
                operation=UserMemoryOperation.DELETE_USER_MEMORY,
                success=False,
                detail="memory not found",
            )
        self.manager.authorize_user_mutation(authorization_id)
        tombstone = self.manager.delete_user(memory_id, authorization_id=authorization_id)
        return UserMemoryResult(
            operation=UserMemoryOperation.DELETE_USER_MEMORY,
            success=True,
            records=(tombstone,),
            detail="memory tombstoned",
        )

    def search(self, user_id: str, query: str) -> UserMemoryResult:
        return UserMemoryResult(
            operation=UserMemoryOperation.SEARCH_USER_MEMORY,
            success=True,
            records=self.manager.search_user(user_id, query),
        )


_REMEMBER = re.compile(r"^\s*remember\s+that\s+(.+?)\s*[.]?\s*$", re.I)


def explicit_remember_payload(text: str) -> str | None:
    """Recognize only an unambiguous explicit persistence request."""

    match = _REMEMBER.match(text)
    return match.group(1) if match else None
