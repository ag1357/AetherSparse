import pytest

from aethersparse.cells.models import ExactEvidenceNode
from aethersparse.cells.working import build_working_state


def _node(index: int) -> ExactEvidenceNode:
    return ExactEvidenceNode(
        claim_id=f"claim:{index}",
        entity_id=f"entity:{index}",
        relation_id="relation:example",
        source_span_id=f"span:{index}",
        polarity=1,
    )


def test_dual_state_preserves_exact_nodes_and_is_bounded() -> None:
    nodes = (_node(1), _node(2))
    state = build_working_state(nodes, ("temporal_fit",), max_nodes=2)
    assert state.exact_nodes == nodes
    assert len(bytes.fromhex(state.associative_signature_hex)) == 128
    assert state.exact_graph_is_authoritative
    with pytest.raises(ValueError, match="bounded working-state"):
        build_working_state((*nodes, _node(3)), (), max_nodes=2)
