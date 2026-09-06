#!/usr/bin/env python3
"""Generate self-article lead occurrences for the V15 P4 knowledge pack.

Repairs a structural corpus defect uniformly: the v12 occurrence export is a
backlink index (articles never link to themselves), so entity evidence lacked
the entity's own definitional lead. For every entity whose canonical document
exists in the corpus sqlite, this emits ONE extra occurrence whose context is
the article's definitional lead prose, cleaned at build time (deterministic
wikitext -> plaintext render). No entity, topic, or query is special-cased;
disambiguation pages ("X may mean: ...") are skipped, leaving those entities
with their existing backlink evidence unchanged.

Output records match the occurrences.jsonl.gz schema consumed by
v14_p4_pack_build.build_evidence and are appended AFTER the backlink stream,
so self-leads land at the end of each entity's blob and compete in the
on-device ranking purely on the generic scoring features.

Inputs are integrity-pinned: the sqlite sha256 is verified against the pack
series manifest before any row is read.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

MAX_RAW_BYTES = 49152         # joined raw text cap per entity (past infoboxes)
MAX_CONTEXT_BYTES = 480       # emitted context window
MIN_PROSE = 60                # first-prose threshold

DISAMBIG_MARKERS = (" may mean:", " may refer to:")


def join_spans(spans: list[tuple[int, int, str]]) -> str:
    """Reconstruct document text from (raw_start, raw_end, raw_text) chunks.
    Chunks are sliding windows and overlap; the true offsets make the merge
    exact without heuristics."""
    spans.sort()
    out: list[str] = []
    end = -1
    for raw_start, raw_end, raw_text in spans:
        if not out:
            out.append(raw_text)
            end = raw_end
            continue
        skip = end - raw_start
        if skip < 0:
            out.append("\n")
            out.append(raw_text)
        elif skip < len(raw_text):
            out.append(raw_text[skip:])
        else:
            continue
        end = max(end, raw_end)
    return "".join(out)


def strip_templates(text: str) -> str:
    depth = 0
    out: list[str] = []
    i = 0
    while i < len(text):
        if text.startswith("{{", i):
            depth += 1
            i += 2
            continue
        if text.startswith("}}", i) and depth:
            depth -= 1
            i += 2
            continue
        if depth == 0:
            out.append(text[i])
        i += 1
    return "".join(out)


def clean_prose(text: str) -> str:
    """Build-time render: wikitext -> plaintext (superset of the device-side
    cleaner; running the device cleaner on this output is identity)."""
    text = strip_templates(text)
    text = re.sub(r"\[\[(File|Image):[^\]]*\]\]", "", text, flags=re.I)
    text = re.sub(r"\[\[Category:[^\]]*\]\]", "", text, flags=re.I)
    text = re.sub(r"\[https?://[^\s\]]+\s+([^\]]*)\]", r"\1", text)
    text = re.sub(r"\[https?://[^\]]*\]", "", text)
    text = re.sub(r"\[\[([^\]|]*\|)?", "", text).replace("]]", "")
    text = re.sub(r"<ref[^>]*/>", "", text)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S)
    text = re.sub(r"</?[a-zA-Z][^>]*>", "", text)
    text = re.sub(r"={2,}[^=]*={2,}", " ", text)
    text = text.replace("'''", "").replace("''", "")
    text = text.replace("&ndash;", "\u2013").replace("&mdash;", "\u2014")
    text = text.replace("&amp;", "&").replace("&lt;", "<")
    text = text.replace("&gt;", ">").replace("&quot;", '"')
    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith(("{|", "|}", "|-")) or stripped.startswith(("|", "!")):
            continue
        m = re.match(r"^[*#:]+\s*", stripped)
        if m:
            stripped = stripped[m.end():]
        if stripped:
            lines.append(stripped)
    text = " ".join(lines)
    return re.sub(r"\s+", " ", text).strip()


def lead_window(prose: str, title: str) -> str | None:
    """Definitional window from the prose start (lead prose is about the
    entity by construction): sentences until >= 120 chars, capped at 480,
    word-boundary cut as fallback."""
    if len(prose) < MIN_PROSE:
        return None
    lowered = prose[:200].casefold()
    if any(marker in lowered for marker in DISAMBIG_MARKERS):
        return None
    # Chunk boundaries can start mid-word/link: drop a leading lowercase
    # fragment up to the first sentence boundary.
    if prose[0].islower():
        cut = re.search(r"[.!?]\s+(?=[A-Z0-9\"(])", prose)
        if cut is None:
            return None
        prose = prose[cut.end():]
        if len(prose) < MIN_PROSE:
            return None
    title_words = [
        w.casefold() for w in re.split(r"[\s()]+", title) if len(w) >= 4
    ]
    if title_words:
        # Drop a leading caption fragment: a short prefix without sentence
        # punctuation, ending in a word char, immediately before the first
        # standalone title word that is followed by a new sentence start.
        head = prose[:600].casefold()
        hits = [(head.find(w), w) for w in title_words if head.find(w) >= 0]
        if hits:
            pos, word = min(hits)
            prefix = prose[:pos]
            nxt = prose[pos + len(word):].lstrip()[:1]
            if (
                0 < pos <= 120
                and len(prefix.split()) >= 2
                and not any(p in prefix for p in ".!?")
                and prefix.rstrip()[-1:].isalnum()
                and nxt
                and (nxt.isupper() or nxt.isdigit())
            ):
                prose = prose[pos + len(word):].lstrip()
    if title_words and not any(w in prose[:400].casefold() for w in title_words):
        return None  # lead prose does not mention the entity early: skip
    ends: list[int] = []
    for i, ch in enumerate(prose[: MAX_CONTEXT_BYTES * 2]):
        if ch in ".!?":
            j = i + 1
            while j < len(prose) and prose[j] in " \")":
                j += 1
            if j >= len(prose) or prose[j].isupper() or prose[j].isdigit():
                ends.append(i + 1)
    end = None
    for candidate in ends:
        if candidate >= 120:
            end = candidate
            break
    if end is None and ends:
        end = ends[-1]
    if end is None or end > MAX_CONTEXT_BYTES:
        end = MAX_CONTEXT_BYTES
        while end > MIN_PROSE and end <= len(prose) and prose[end - 1] != " ":
            end -= 1
        if end > 0 and end <= len(prose) and prose[end - 1] == " ":
            end -= 1
    window = prose[:end].strip()
    return window if len(window) >= MIN_PROSE else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", type=Path, required=True)
    parser.add_argument("--entities", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sqlite-sha256", required=True)
    args = parser.parse_args()

    digest = hashlib.sha256(args.sqlite.read_bytes()).hexdigest()
    if digest != args.sqlite_sha256:
        raise SystemExit(f"sqlite integrity failure: {digest}")
    print(f"sqlite verified: {digest[:16]}...", file=sys.stderr)

    entities: list[tuple[str, str]] = []  # (entity_id, title)
    with gzip.open(args.entities, "rt", encoding="utf-8") as source:
        for line in source:
            record = json.loads(line)
            entities.append((record["entity_id"], record["title"]))
    print(f"entities: {len(entities)}", file=sys.stderr)
    entity_titles = {title for _, title in entities}
    entity_titles |= {t.casefold() for t in entity_titles}

    db = sqlite3.connect(f"file:{args.sqlite}?mode=ro", uri=True)
    doc_by_title: dict[str, str] = {}
    for doc_id, title, normalized in db.execute(
        "select document_id, title, normalized_title from documents"
    ):
        if title in entity_titles or normalized in entity_titles:
            doc_by_title[title] = doc_id
            doc_by_title.setdefault(normalized, doc_id)
    print(f"entity documents: {len(doc_by_title)}", file=sys.stderr)

    emitted = skipped_disambig = skipped_no_doc = skipped_no_prose = 0
    span_query = (
        "select raw_start, raw_end, raw_text from chunks "
        "where document_id = ? order by raw_start"
    )
    with gzip.open(args.output, "wt", encoding="utf-8") as out:
        for index, (entity_id, title) in enumerate(entities):
            if index % 20000 == 0:
                print(f"  progress: {index}/{len(entities)}", file=sys.stderr)
            doc_id = doc_by_title.get(title) or doc_by_title.get(title.casefold())
            if doc_id is None:
                skipped_no_doc += 1
                continue
            spans: list[tuple[int, int, str]] = []
            total = 0
            for raw_start, raw_end, raw_text in db.execute(span_query, (doc_id,)):
                spans.append((raw_start, raw_end, raw_text))
                total += len(raw_text)
                if total >= MAX_RAW_BYTES:
                    break
            if not spans:
                skipped_no_prose += 1
                continue
            prose = clean_prose(join_spans(spans))
            window = lead_window(prose, title)
            if window is None:
                if any(marker in prose[:200].casefold() for marker in DISAMBIG_MARKERS):
                    skipped_disambig += 1
                else:
                    skipped_no_prose += 1
                continue
            out.write(
                json.dumps(
                    {
                        "canonical_entity_id": entity_id,
                        "source_document_id": doc_id,
                        "mention": title,
                        "context": window,
                        "source_split": "fit",
                        "resolution_state": "canonical",
                    }
                )
                + "\n"
            )
            emitted += 1
    stats = {
        "entities": len(entities),
        "emitted": emitted,
        "skipped_no_document": skipped_no_doc,
        "skipped_disambiguation": skipped_disambig,
        "skipped_no_prose": skipped_no_prose,
        "output_bytes": args.output.stat().st_size,
    }
    print(json.dumps(stats, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
