"""Dual exact and associative working memory with bounded lifetime."""

from __future__ import annotations

from aethersparse.cells.models import DualWorkingState, ExactEvidenceNode
from aethersparse.cells.vsa import atom, bind, bundle, permute


def evidence_signature(nodes: tuple[ExactEvidenceNode, ...]) -> int:
    bound_nodes: list[int] = []
    for node in nodes:
        subject = atom(node.entity_id)
        relation = permute(atom(node.relation_id), 17)
        source = permute(atom(node.source_span_id), 31)
        bound_nodes.append(bind(bind(subject, relation), source))
    return bundle(bound_nodes)


def build_working_state(
    nodes: tuple[ExactEvidenceNode, ...],
    unresolved_facets: tuple[str, ...],
    *,
    max_nodes: int = 64,
) -> DualWorkingState:
    if len(nodes) > max_nodes:
        raise ValueError("exact evidence graph exceeds bounded working-state budget")
    signature = evidence_signature(nodes)
    return DualWorkingState(
        exact_nodes=nodes,
        associative_signature_hex=signature.to_bytes(128).hex(),
        unresolved_facets=unresolved_facets,
    )
