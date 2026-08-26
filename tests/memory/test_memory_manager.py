from __future__ import annotations

from pathlib import Path

import pytest

from aethersparse.agent.contracts import EntityHypothesis
from aethersparse.agent.conversation import ConversationEngine
from aethersparse.cognitive.models import CognitiveObligationGraph
from aethersparse.memory.manager import MemoryAuthorizationError, MemoryTierManager
from aethersparse.memory.models import (
    DeletionState,
    MemoryAuthority,
    MemoryPayload,
    MemoryProvenance,
    MemoryType,
    PhysicalResidency,
    SemanticTier,
)
from aethersparse.memory.persistence import AuthoritativeStateStore
from aethersparse.memory.user import UserMemoryService


def _provenance(authority: MemoryAuthority = MemoryAuthority.INFERENCE) -> MemoryProvenance:
    return MemoryProvenance(authority=authority, source_id="test")


def test_ephemeral_expiry_short_term_eviction_and_orthogonal_residency() -> None:
    manager = MemoryTierManager.with_limits(ephemeral=2, short_term=2)
    ephemeral = manager.create(
        memory_type=MemoryType.SCRATCH,
        semantic_tier=SemanticTier.EPHEMERAL,
        residency=PhysicalResidency.HOT,
        payload=MemoryPayload(text="temporary score"),
        provenance=_provenance(),
        ttl_epochs=2,
    )
    assert ephemeral.semantic_tier is SemanticTier.EPHEMERAL
    assert ephemeral.residency is PhysicalResidency.HOT
    manager.advance(2)
    with pytest.raises(KeyError):
        manager.get(ephemeral.memory_id)

    created = []
    for index in range(3):
        created.append(
            manager.create(
                memory_type=MemoryType.CONVERSATION_TURN,
                semantic_tier=SemanticTier.SHORT_TERM,
                payload=MemoryPayload(text=f"turn {index}"),
                provenance=MemoryProvenance(
                    authority=MemoryAuthority.USER_ASSERTED, source_id=f"turn-{index}"
                ),
                session_scope="s1",
            )
        )
    remaining = {record.memory_id for record in manager.records()}
    assert created[0].memory_id not in remaining
    assert {created[1].memory_id, created[2].memory_id}.issubset(remaining)


def test_working_selected_evidence_is_pinned_and_promotion_preserves_inference() -> None:
    manager = MemoryTierManager()
    evidence = manager.bind_selected_evidence(
        payload=MemoryPayload(
            text="temperature is not below 82 C",
            negated=True,
            quantity="82",
            unit="C",
            uncertainty_milli=20,
            perspective="sensor joint_4",
        ),
        provenance=MemoryProvenance(
            authority=MemoryAuthority.OBSERVATION,
            source_id="sensor:joint_4",
            evidence_handle="event:77",
        ),
        evidence_handle="event:77",
        cog_bindings=("evidence:thermal",),
        session_scope="s1",
    )
    verified = manager.mark_verification_bound(evidence.memory_id)
    assert verified.pinned and verified.verification_bound
    with pytest.raises(ValueError, match="pinned"):
        manager.demote(evidence.memory_id, SemanticTier.SHORT_TERM, reason="pressure")

    inference = manager.create(
        memory_type=MemoryType.HYPOTHESIS,
        semantic_tier=SemanticTier.EPHEMERAL,
        payload=MemoryPayload(text="thermal fault likely", uncertainty_milli=400),
        provenance=_provenance(),
        salience_milli=200,
        novelty_milli=200,
    )
    promoted = manager.promote(inference.memory_id, reason="reuse")
    assert promoted.provenance.authority is MemoryAuthority.INFERENCE
    assert promoted.semantic_tier is SemanticTier.SHORT_TERM
    assert promoted.payload.uncertainty_milli == 400
    promoted = manager.promote(inference.memory_id, reason="active obligation")
    assert promoted.semantic_tier is SemanticTier.WORKING
    with pytest.raises(ValueError, match="long-term authority"):
        manager.promote(inference.memory_id, reason="not authority")
    assert manager.promotion_score(promoted) == 1400
    assert promoted in manager.promotion_candidates(minimum_score=1400)


def test_user_memory_crud_tombstone_compaction_and_authority_separation() -> None:
    manager = MemoryTierManager()
    service = UserMemoryService(manager)
    with pytest.raises(MemoryAuthorizationError):
        manager.create(
            memory_type=MemoryType.USER_MEMORY,
            semantic_tier=SemanticTier.LONG_TERM,
            payload=MemoryPayload(text="unauthorized"),
            provenance=MemoryProvenance(
                authority=MemoryAuthority.USER_ASSERTED, source_id="conversation"
            ),
            user_scope="allan",
        )
    written = service.write(
        "allan",
        "my preferred keyboard mode is BLE HID",
        authorization_id="remember-1",
        source_id="turn-1",
    ).records[0]
    assert service.search("allan", "keyboard BLE").records == (written,)

    edited = service.edit(
        "allan",
        written.memory_id,
        "my preferred keyboard mode is BLE HID host",
        authorization_id="edit-1",
    ).records[0]
    assert edited.created_epoch == written.created_epoch
    assert edited.modified_epoch > written.modified_epoch
    assert service.search("allan", "keyboard host").records == (edited,)

    external = manager.create(
        memory_type=MemoryType.EXTERNAL_KNOWLEDGE,
        semantic_tier=SemanticTier.LONG_TERM,
        payload=MemoryPayload(text="CardKB2 supports several firmware modes"),
        provenance=MemoryProvenance(
            authority=MemoryAuthority.EXTERNAL_GROUNDED,
            source_id="manual",
            evidence_handle="manual:cardkb2",
        ),
        source_evidence_handle="manual:cardkb2",
    )
    with pytest.raises(ValueError, match="immutable external"):
        manager.authorize_user_mutation("bad-delete")
        manager.delete_user(external.memory_id, authorization_id="bad-delete")

    tombstone = service.delete(
        "allan", edited.memory_id, authorization_id="delete-1"
    ).records[0]
    assert tombstone.deletion_state is DeletionState.TOMBSTONED
    assert service.search("allan", "keyboard").records == ()
    assert external in manager.records()
    assert manager.compact_tombstones(before_epoch=manager.epoch) == 1
    remaining_ids = {item.memory_id for item in manager.records(include_deleted=True)}
    assert edited.memory_id not in remaining_ids


def test_authoritative_restart_restores_sessions_cog_and_long_term_but_not_scratch(
    tmp_path: Path,
) -> None:
    path = tmp_path / "operational-state.json"
    store = AuthoritativeStateStore(path)
    conversation = ConversationEngine(store)
    hypothesis = EntityHypothesis(
        entity_id="entity:turing", label="Alan Turing", confidence=0.99, surface="Turing"
    )
    conversation.accept("session-a", "Who was Turing?", candidates=(hypothesis,))
    conversation.accept("session-b", "Hello")

    manager = MemoryTierManager()
    manager.create(
        memory_type=MemoryType.SCRATCH,
        semantic_tier=SemanticTier.EPHEMERAL,
        payload=MemoryPayload(text="uncommitted score"),
        provenance=_provenance(),
    )
    service = UserMemoryService(manager)
    memory = service.write(
        "allan", "project storage is removable", authorization_id="remember", source_id="turn-1"
    ).records[0]
    cog = CognitiveObligationGraph(cog_id="cog-session-a")
    store.save_complete(memory=manager, cogs=(cog,))
    anchor = store.add_anchor("USER_MEMORY_MUTATED", manager.epoch)
    conversation.accept("session-a", "continue task")

    restarted = AuthoritativeStateStore(path)
    restored = restarted.restore_memory()
    assert restarted.load("session-a").previously_resolved_entities[-1].entity_id == "entity:turing"
    assert restarted.load("session-b").current_query == "Hello"
    assert restarted.state.cogs == (cog,)
    assert restored.get(memory.memory_id).payload.text == "project storage is removable"
    assert all(record.semantic_tier is not SemanticTier.EPHEMERAL for record in restored.records())
    replayed = restarted.restore_anchor_and_replay(anchor.anchor_id)
    assert replayed.sessions == restarted.state.sessions
    assert restarted.load("session-a").current_query == "continue task"

    restarted.reset("session-a")
    assert restarted.load("session-a").current_query is None
    # Session reset intentionally does not delete long-term user memory.
    restored_text = restarted.restore_memory().get(memory.memory_id).payload.text
    assert restored_text == "project storage is removable"


def test_conversation_context_is_bounded_and_summarized() -> None:
    from aethersparse.agent.session import InMemorySessionStore

    engine = ConversationEngine(InMemorySessionStore())
    for index in range(20):
        state, _ = engine.accept("bounded", f"utterance number {index}")
    assert len(state.recent_utterances) == 12
    assert 0 < len(state.conversation_summary) <= 8
