"""Persistent V15 protocol service with explicit user-memory authority."""

from __future__ import annotations

import re
from collections.abc import Sequence

from aethersparse.memory.persistence import AuthoritativeStateStore
from aethersparse.memory.user import UserMemoryService, explicit_remember_payload

from .capabilities import OperationalSelfModel
from .protocol import (
    AssistantTextDeltaPayload,
    CapabilitiesPayload,
    ClarificationRequestPayload,
    EvidenceSummaryPayload,
    HealthPayload,
    MemoryStatusPayload,
    MessageType,
    ProtocolMessage,
    SessionOpenPayload,
    SessionResumePayload,
    UserTextPayload,
    response,
)
from .vertical import AetherCoreRequest, AetherCoreVerticalSlice

_LIST_MEMORY = re.compile(r"^\s*(?:list|show)\s+(?:my\s+)?memor(?:y|ies)\s*[?.!]?\s*$", re.I)
_RECALL_MEMORY = re.compile(
    r"^\s*(?:what\s+do\s+you\s+remember\s+about|recall)\s+(.+?)\s*[?.!]?\s*$", re.I
)
_EDIT_MEMORY = re.compile(r"^\s*edit\s+memory\s+(mem-[0-9]{8})\s+to\s+(.+?)\s*$", re.I)
_DELETE_MEMORY = re.compile(
    r"^\s*(?:delete|forget)\s+memory\s+(mem-[0-9]{8})\s*[.!]?\s*$", re.I
)


class AetherCoreOperationalService:
    """Accessory-side request handler; transport is deliberately injected outside."""

    def __init__(
        self,
        runtime: AetherCoreVerticalSlice,
        state_store: AuthoritativeStateStore,
        user_memory: UserMemoryService,
        self_model: OperationalSelfModel,
    ) -> None:
        if runtime.conversation.store is not state_store:
            raise ValueError("runtime must use the authoritative state store")
        self.runtime = runtime
        self.state_store = state_store
        self.user_memory = user_memory
        self.self_model = self_model

    def _capabilities(self, request: ProtocolMessage) -> ProtocolMessage:
        return response(
            request,
            MessageType.CAPABILITIES,
            CapabilitiesPayload(
                protocol_version="aethercore-tactility.v2",
                hardware_class=self.self_model.hardware_class,
                tools=tuple(item.value for item in self.self_model.available_tools),
                specialists=self.self_model.available_specialists,
                unavailable=self.self_model.unavailable_capabilities,
                transport=self.self_model.active_transport,
            ),
            suffix="capabilities",
        )

    def _memory_status(
        self,
        request: ProtocolMessage,
        operation: str,
        success: bool,
        memory_ids: tuple[str, ...],
        detail: str,
    ) -> ProtocolMessage:
        return response(
            request,
            MessageType.MEMORY_STATUS,
            MemoryStatusPayload(
                operation=operation,
                success=success,
                memory_ids=memory_ids,
                detail=detail,
            ),
            suffix="memory",
        )

    def _persist_memory(self) -> None:
        self.state_store.save_complete(memory=self.user_memory.manager)
        self.state_store.add_anchor("USER_MEMORY_MUTATED", self.user_memory.manager.epoch)

    def _user_memory_messages(
        self, request: ProtocolMessage, text: str
    ) -> tuple[ProtocolMessage, ...] | None:
        user_id = request.session_id
        authorization = request.request_id or request.message_id
        remembered = explicit_remember_payload(text)
        if remembered is not None:
            result = self.user_memory.write(
                user_id,
                remembered,
                authorization_id=authorization,
                source_id=f"session:{request.session_id}:{request.message_id}",
            )
            self._persist_memory()
            return (
                self._memory_status(
                    request,
                    result.operation.value,
                    result.success,
                    tuple(item.memory_id for item in result.records),
                    result.detail,
                ),
            )
        if _LIST_MEMORY.match(text):
            result = self.user_memory.list(user_id)
            detail = "; ".join(f"{item.memory_id}: {item.payload.text}" for item in result.records)
            return (
                self._memory_status(
                    request,
                    result.operation.value,
                    True,
                    tuple(item.memory_id for item in result.records),
                    detail or "no user memories",
                ),
            )
        recall = _RECALL_MEMORY.match(text)
        if recall:
            result = self.user_memory.search(user_id, recall.group(1))
            detail = "; ".join(
                f"USER_ASSERTED {item.memory_id}: {item.payload.text}" for item in result.records
            )
            return (
                self._memory_status(
                    request,
                    result.operation.value,
                    True,
                    tuple(item.memory_id for item in result.records),
                    detail or "no matching user memory",
                ),
            )
        edit = _EDIT_MEMORY.match(text)
        if edit:
            result = self.user_memory.edit(
                user_id,
                edit.group(1).casefold(),
                edit.group(2),
                authorization_id=authorization,
            )
            if result.success:
                self._persist_memory()
            return (
                self._memory_status(
                    request,
                    result.operation.value,
                    result.success,
                    tuple(item.memory_id for item in result.records),
                    result.detail,
                ),
            )
        delete = _DELETE_MEMORY.match(text)
        if delete:
            result = self.user_memory.delete(
                user_id,
                delete.group(1).casefold(),
                authorization_id=authorization,
            )
            if result.success:
                self._persist_memory()
            return (
                self._memory_status(
                    request,
                    result.operation.value,
                    result.success,
                    tuple(item.memory_id for item in result.records),
                    result.detail,
                ),
            )
        return None

    def handle(self, request: ProtocolMessage) -> Sequence[ProtocolMessage]:
        if request.protocol_version != "aethercore-tactility.v2":
            raise ValueError("unsupported protocol version")
        if request.type in {MessageType.SESSION_OPEN, MessageType.SESSION_RESUME}:
            if request.type is MessageType.SESSION_OPEN:
                assert isinstance(request.payload, SessionOpenPayload)
            else:
                assert isinstance(request.payload, SessionResumePayload)
            self.state_store.load(request.session_id)
            return (
                response(
                    request,
                    MessageType.HEALTH,
                    HealthPayload(
                        status=self.self_model.service_status,
                        runtime_version=self.self_model.build_version,
                        service_generation=self.state_store.state.service_generation,
                    ),
                    suffix="health",
                ),
                self._capabilities(request),
            )
        if request.type is MessageType.CAPABILITIES:
            return (self._capabilities(request),)
        if request.type is MessageType.HEALTH:
            return (
                response(
                    request,
                    MessageType.HEALTH,
                    HealthPayload(
                        status=self.self_model.service_status,
                        runtime_version=self.self_model.build_version,
                        service_generation=self.state_store.state.service_generation,
                    ),
                ),
            )
        if request.type is MessageType.USER_TEXT:
            assert isinstance(request.payload, UserTextPayload)
            memory_messages = self._user_memory_messages(request, request.payload.text)
            if memory_messages is not None:
                return memory_messages
            text = request.payload.text
        elif request.type is MessageType.USER_CANCEL:
            text = "cancel"
        elif request.type is MessageType.RESET:
            text = "reset"
        else:
            raise ValueError(f"message is not a legal client request: {request.type}")

        result = self.runtime.query(AetherCoreRequest(session_id=request.session_id, text=text))
        if result.disposition == "CLARIFY":
            pending = self.state_store.load(request.session_id).pending_clarification
            if pending is None:
                raise RuntimeError("clarification lost its structured state")
            return (
                response(
                    request,
                    MessageType.CLARIFICATION_REQUEST,
                    ClarificationRequestPayload(
                        question=pending.question,
                        choices=tuple(
                            f"{choice.choice_id}: {choice.label}" for choice in pending.choices
                        ),
                    ),
                ),
            )
        messages: list[ProtocolMessage] = [
            response(
                request,
                MessageType.ASSISTANT_TEXT_DELTA,
                AssistantTextDeltaPayload(text=result.text, final=True),
            )
        ]
        if result.evidence_handle_ids:
            messages.append(
                response(
                    request,
                    MessageType.EVIDENCE_SUMMARY,
                    EvidenceSummaryPayload(
                        handle_ids=result.evidence_handle_ids,
                        summary="Exact evidence handles used by the accepted plan.",
                    ),
                    suffix="evidence",
                )
            )
        return tuple(messages)
