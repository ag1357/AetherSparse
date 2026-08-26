"""Persistent V15 AetherCore service and transport-independent Tactility adapter."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException

from aethersparse.agent.capabilities import host_capability_model
from aethersparse.agent.operational import AetherCoreOperationalService
from aethersparse.agent.protocol import (
    AssistantTextDeltaPayload,
    ClarificationRequestPayload,
    EvidenceSummaryPayload,
    HealthPayload,
    MessageType,
    ProtocolMessage,
    UserTextPayload,
    response,
)
from aethersparse.agent.session import JsonSessionStore
from aethersparse.agent.vertical import (
    AetherCoreRequest,
    AetherCoreResponse,
    AetherCoreVerticalSlice,
    GroundedKnowledgeRecord,
    load_selected_policy_json,
)
from aethersparse.memory.persistence import AuthoritativeStateStore
from aethersparse.memory.user import UserMemoryService

ROOT = Path(__file__).resolve().parents[3]


def create_operational_app(service: AetherCoreOperationalService) -> FastAPI:
    application = FastAPI(
        title="AetherCore V15 operational accessory service",
        version="15.0",
        description="Persistent grounded cognition and transport-independent terminal protocol.",
    )

    @application.get("/v15/health")
    def health() -> dict[str, object]:
        return {
            "status": service.self_model.service_status,
            "role": "aethercore_accessory",
            "source_identity": service.self_model.source_identity,
            "runtime_abi": service.self_model.runtime_abi,
            "memory_schema": service.self_model.memory_schema,
            "service_generation": service.state_store.state.service_generation,
        }

    @application.get("/v15/capabilities")
    def capabilities() -> dict[str, object]:
        return service.self_model.model_dump(mode="json")

    @application.post("/v15/message", response_model=list[ProtocolMessage])
    def message(request: ProtocolMessage) -> list[ProtocolMessage]:
        try:
            return list(service.handle(request))
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    return application


def create_vertical_app(runtime: AetherCoreVerticalSlice) -> FastAPI:
    application = FastAPI(
        title="AetherCore V14 accessory service",
        version="14.0",
        description="Grounded conversational runtime for the physically separate accessory.",
    )

    @application.get("/v14/health")
    @application.get("/v13/health", include_in_schema=False)
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "role": "aethercore_accessory",
            "policy_parameters": runtime.policy.parameter_count,
            "semantic_address_surfaces": runtime.address_index.surface_count,
        }

    @application.post("/v14/query", response_model=AetherCoreResponse)
    @application.post("/v13/query", response_model=AetherCoreResponse, include_in_schema=False)
    def query(request: AetherCoreRequest) -> AetherCoreResponse:
        try:
            return runtime.query(request)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    return application


def tactility_handler(
    runtime: AetherCoreVerticalSlice,
    request: ProtocolMessage,
) -> Sequence[ProtocolMessage]:
    """Translate the frozen terminal protocol without moving cognition to it."""

    if request.type in {MessageType.SESSION_OPEN, MessageType.HEALTH}:
        return (
            response(
                request,
                MessageType.HEALTH,
                HealthPayload(status="ok", runtime_version="aethercore-v14"),
            ),
        )
    if request.type is MessageType.USER_TEXT:
        assert isinstance(request.payload, UserTextPayload)
        text = request.payload.text
    elif request.type is MessageType.USER_CANCEL:
        text = "cancel"
    elif request.type is MessageType.RESET:
        text = "reset"
    else:
        raise ValueError(f"terminal request type is not accepted by the accessory: {request.type}")
    result = runtime.query(AetherCoreRequest(session_id=request.session_id, text=text))
    if result.disposition == "CLARIFY":
        session = runtime.conversation.store.load(request.session_id)
        pending = session.pending_clarification
        if pending is None:
            raise RuntimeError("clarification response lost its structured choices")
        return (
            response(
                request,
                MessageType.CLARIFICATION_REQUEST,
                ClarificationRequestPayload(
                    question=pending.question,
                    choices=tuple(f"{item.choice_id}: {item.label}" for item in pending.choices),
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


def load_runtime(
    knowledge_path: Path,
    policy_report_path: Path,
    session_path: Path,
) -> AetherCoreVerticalSlice:
    knowledge_value = json.loads(knowledge_path.read_text(encoding="utf-8"))
    if not isinstance(knowledge_value, list):
        raise ValueError("deployed knowledge file must be a JSON list")
    records = tuple(GroundedKnowledgeRecord.model_validate(item) for item in knowledge_value)
    policy = load_selected_policy_json(policy_report_path.read_bytes())
    return AetherCoreVerticalSlice(records, policy, JsonSessionStore(session_path))


def load_operational_service(
    knowledge_path: Path,
    policy_report_path: Path,
    state_path: Path,
    *,
    source_identity: str,
) -> AetherCoreOperationalService:
    knowledge_value = json.loads(knowledge_path.read_text(encoding="utf-8"))
    if not isinstance(knowledge_value, list):
        raise ValueError("deployed knowledge file must be a JSON list")
    records = tuple(GroundedKnowledgeRecord.model_validate(item) for item in knowledge_value)
    policy = load_selected_policy_json(policy_report_path.read_bytes())
    state_store = AuthoritativeStateStore(state_path)
    runtime = AetherCoreVerticalSlice(records, policy, state_store)
    memory = state_store.restore_memory()
    return AetherCoreOperationalService(
        runtime,
        state_store,
        UserMemoryService(memory),
        host_capability_model(source_identity),
    )


def main() -> None:
    knowledge = os.environ.get("AETHERCORE_V15_KNOWLEDGE") or os.environ.get(
        "AETHERCORE_V14_KNOWLEDGE"
    ) or os.environ.get(
        "AETHERCORE_V13_KNOWLEDGE"
    )
    if not knowledge:
        raise SystemExit("AETHERCORE_V15_KNOWLEDGE must name a deployed grounded record file")
    policy = Path(
        os.environ.get(
            "AETHERCORE_V15_POLICY",
            ROOT / "reports" / "droid" / "v14" / "controller-selected-policy-int8.json",
        )
    )
    state = Path(os.environ.get("AETHERCORE_V15_STATE", "runtime/v15-operational-state.json"))
    source_identity = os.environ.get("AETHERCORE_V15_SOURCE_IDENTITY", "UNSET_WORKTREE")
    service = load_operational_service(
        Path(knowledge), policy, state, source_identity=source_identity
    )
    port = int(os.environ.get("PORT", "8082"))
    if not 1 <= port <= 65_535:
        raise SystemExit("PORT must be in the range 1..65535")
    uvicorn.run(create_operational_app(service), host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
