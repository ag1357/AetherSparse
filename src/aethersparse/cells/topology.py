"""Build bounded, overlapping cognitive cells from the existing corpus planes."""

from __future__ import annotations

import hashlib
from collections import defaultdict

from aethersparse.cells.models import CellKind, CognitiveCell
from aethersparse.cells.vsa import encode_terms
from aethersparse.traversal.corpus import TOKEN_RE, CorpusStore, normalize_text


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in TOKEN_RE.findall(normalize_text(value)) if len(token) > 2}


class CognitiveCellBuilder:
    """Derive comparable topologies without changing immutable source rows."""

    def __init__(self, store: CorpusStore, *, max_documents: int = 256):
        self.store = store
        self.max_documents = max_documents

    def _cell(self, kind: CellKind, label: str, documents: set[str]) -> CognitiveCell:
        bounded = tuple(sorted(documents)[: self.max_documents])
        marks = ",".join("?" for _ in bounded)
        rows = (
            list(
                self.store.db.execute(
                    "SELECT document_id,title,normalized_text FROM documents "
                    f"WHERE document_id IN ({marks})",
                    bounded,
                )
            )
            if bounded
            else []
        )
        titles = tuple(sorted({str(row["title"]) for row in rows}))
        redirects = (
            tuple(
                sorted(
                    {
                        str(row[0])
                        for row in self.store.db.execute(
                            f"SELECT alias FROM aliases WHERE document_id IN ({marks})", bounded
                        )
                    }
                    - set(titles)
                )
            )
            if bounded
            else ()
        )
        aliases = (*titles, *redirects[: max(0, 512 - len(titles))])
        terms = _tokens(label)
        source_bytes = 0
        for row in rows:
            terms.update(_tokens(row["title"]))
            source_bytes += len(row["normalized_text"].encode())
        identity = hashlib.sha256(f"{kind}:{label}".encode()).hexdigest()[:20]
        return CognitiveCell(
            cell_id=f"cell:{kind}:{identity}",
            kind=kind,
            label=label,
            document_ids=bounded,
            entity_aliases=aliases,
            relation_terms=tuple(sorted(terms)[:512]),
            signature_hex=encode_terms(terms).to_bytes(128).hex(),
            source_bytes=source_bytes,
        )

    def category_cells(self) -> list[CognitiveCell]:
        groups: dict[str, set[str]] = defaultdict(set)
        for document_id, category in self.store.db.execute(
            "SELECT document_id,category FROM categories ORDER BY category,document_id"
        ):
            groups[normalize_text(category).casefold()].add(document_id)
        return [
            self._cell(CellKind.CATEGORY, label, docs) for label, docs in sorted(groups.items())
        ]

    def entity_community_cells(self) -> list[CognitiveCell]:
        """Deterministic bounded anchor neighborhoods; not broad query traversal."""
        groups: dict[str, set[str]] = defaultdict(set)
        rows = self.store.db.execute(
            """SELECT source_document_id,target_document_id FROM links
               WHERE target_document_id IS NOT NULL
               ORDER BY target_document_id,source_document_id"""
        )
        for source, target in rows:
            groups[target].update((source, target))
        titles = dict(self.store.db.execute("SELECT document_id,title FROM documents"))
        return [
            self._cell(CellKind.ENTITY_COMMUNITY, titles.get(anchor, anchor), docs)
            for anchor, docs in sorted(groups.items())
        ]

    def semantic_bucket_cells(self, *, prefix_bits: int = 10) -> list[CognitiveCell]:
        if not 1 <= prefix_bits <= 256:
            raise ValueError("prefix_bits must be between 1 and 256")
        groups: dict[str, set[str]] = defaultdict(set)
        for document_id, semantic_key in self.store.db.execute(
            "SELECT document_id,min(semantic_key) FROM chunks GROUP BY document_id"
        ):
            key = int(semantic_key, 16)
            total_bits = len(semantic_key) * 4
            prefix = key >> max(0, total_bits - prefix_bits)
            groups[f"{prefix_bits}:{prefix:0{(prefix_bits + 3) // 4}x}"].add(document_id)
        return [
            self._cell(CellKind.SEMANTIC_BUCKET, label, docs)
            for label, docs in sorted(groups.items())
        ]

    def hybrid_cells(self) -> list[CognitiveCell]:
        """Overlap category and anchor neighborhoods while enforcing a size cap."""
        source = [*self.category_cells(), *self.entity_community_cells()]
        cells: list[CognitiveCell] = []
        for cell in source:
            if 2 <= len(cell.document_ids) <= self.max_documents:
                identity = hashlib.sha256(f"{CellKind.HYBRID}:{cell.cell_id}".encode()).hexdigest()[
                    :20
                ]
                cells.append(
                    cell.model_copy(
                        update={
                            "cell_id": f"cell:{CellKind.HYBRID}:{identity}",
                            "kind": CellKind.HYBRID,
                        }
                    )
                )
        return cells

    def build(self, kind: CellKind) -> list[CognitiveCell]:
        return {
            CellKind.CATEGORY: self.category_cells,
            CellKind.ENTITY_COMMUNITY: self.entity_community_cells,
            CellKind.SEMANTIC_BUCKET: self.semantic_bucket_cells,
            CellKind.HYBRID: self.hybrid_cells,
        }[kind]()
