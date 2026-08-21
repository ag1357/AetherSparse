"""Host service and Tactility protocol adapter for the V14 COG vertical slice."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException

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

ROOT = Path(__file__).resolve().parents[3]


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


def main() -> None:
    knowledge = os.environ.get("AETHERCORE_V14_KNOWLEDGE") or os.environ.get(
        "AETHERCORE_V13_KNOWLEDGE"
    )
    if not knowledge:
        raise SystemExit("AETHERCORE_V14_KNOWLEDGE must name a deployed grounded record file")
    policy = Path(
        os.environ.get(
            "AETHERCORE_V14_POLICY",
            ROOT / "reports" / "droid" / "v14" / "controller-selected-policy-int8.json",
        )
    )
    sessions = Path(os.environ.get("AETHERCORE_V14_SESSIONS", "runtime/v14-sessions"))
    runtime = load_runtime(Path(knowledge), policy, sessions)
    uvicorn.run(create_vertical_app(runtime), host="0.0.0.0", port=8082)


if __name__ == "__main__":
    main()
