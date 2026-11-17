"""Retrieval corpus for semantic ranking (CA-02): one document per entity, body included.

Every ranker (BM25, MiniLM, CodeT5+) reads the same corpus, so a difference in their results
comes from the ranker, never from different input text.

What one entity document holds:

- a header: the qualified name, the signature and the docstring;
- the body: the entity's own source lines at the pinned commit. A class keeps its own lines
  (class statement, attributes, decorators) but not the lines of its methods or nested
  classes, which are documents of their own, so no line is counted twice;
- exact spans: the 1-based, inclusive line ranges each chunk covers.

Chunking is deterministic and loses nothing. The body is cut into chunks of at most
`max_lines` lines and `max_chars` characters, every chunk repeats the header, and a single
line longer than `max_chars` is cut into pieces that join back into the original line.
Joining the chunk bodies in order gives the whole body back (tested), so no text is dropped
without a trace. Only trailing blank lines are left out.

Entities whose file text is missing are not dropped silently: they are listed in
`Corpus.skipped` with a reason.

Pure: no file I/O, no model, deterministic output order.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from codeatlas.schema import CodeEntity

CHUNKER_VERSION = "chunker@1"
ELIGIBLE_KINDS = ("function", "method", "class")
MAX_LINES = 40
MAX_CHARS = 1600

Span = tuple[int, int]


@dataclass(frozen=True)
class Chunk:
    """One piece of an entity's body, with the header repeated in front."""

    chunk_id: str  # "<entity_id>#<index>"
    entity_id: str
    file: str
    index: int
    spans: tuple[Span, ...]  # line ranges of the body text in this chunk; () = header only
    body: str
    text: str  # header + body: what the rankers read


@dataclass(frozen=True)
class EntityDoc:
    entity_id: str
    kind: str
    file: str
    start_line: int
    end_line: int
    header: str
    chunks: tuple[Chunk, ...]


@dataclass(frozen=True)
class Corpus:
    pinned_sha: str
    chunker_version: str
    max_lines: int
    max_chars: int
    docs: tuple[EntityDoc, ...]
    skipped: tuple[tuple[str, str], ...]  # (entity_id, reason)
    corpus_hash: str

    def chunks(self) -> list[Chunk]:
        return [chunk for doc in self.docs for chunk in doc.chunks]


def build_corpus(
    *,
    entities: list[CodeEntity],
    file_sources: dict[str, bytes],
    pinned_sha: str,
    max_lines: int = MAX_LINES,
    max_chars: int = MAX_CHARS,
) -> Corpus:
    """Build the corpus from every eligible entity whose file text is in `file_sources`."""
    if max_lines < 1 or max_chars < 1:
        raise ValueError("max_lines and max_chars must be at least 1")
    eligible = sorted(
        (e for e in entities if e.kind in ELIGIBLE_KINDS),
        key=lambda e: (e.file, e.start_line, e.entity_id),
    )
    lines_by_file: dict[str, list[str]] = {}
    docs: list[EntityDoc] = []
    skipped: list[tuple[str, str]] = []
    for entity in eligible:
        source = file_sources.get(entity.file)
        if source is None:
            skipped.append((entity.entity_id, "file text not available at the pinned commit"))
            continue
        if entity.file not in lines_by_file:
            lines_by_file[entity.file] = _lines(source)
        lines = lines_by_file[entity.file]
        if entity.start_line < 1 or entity.end_line > len(lines):
            skipped.append((entity.entity_id, "entity lines fall outside the file"))
            continue
        excluded = _child_ranges(entity, eligible)
        body_lines = [
            (no, lines[no - 1])
            for no in range(entity.start_line, entity.end_line + 1)
            if not any(lo <= no <= hi for lo, hi in excluded)
        ]
        while body_lines and not body_lines[-1][1].strip():
            body_lines.pop()
        header = _header(entity)
        docs.append(
            EntityDoc(
                entity_id=entity.entity_id,
                kind=entity.kind,
                file=entity.file,
                start_line=entity.start_line,
                end_line=entity.end_line,
                header=header,
                chunks=tuple(_chunks(entity, header, body_lines, max_lines, max_chars)),
            )
        )
    return Corpus(
        pinned_sha=pinned_sha,
        chunker_version=CHUNKER_VERSION,
        max_lines=max_lines,
        max_chars=max_chars,
        docs=tuple(docs),
        skipped=tuple(skipped),
        corpus_hash=_corpus_hash(pinned_sha, max_lines, max_chars, docs),
    )


def _lines(source: bytes) -> list[str]:
    """Split on "\\n" only, so line numbers match tree-sitter (str.splitlines also splits on
    form feeds and other separators, which would shift every later line)."""
    text = source.decode("utf-8", errors="replace")
    return [line.rstrip("\r") for line in text.split("\n")]


def _child_ranges(entity: CodeEntity, eligible: list[CodeEntity]) -> list[Span]:
    """Line ranges of a class's methods and nested classes, which are their own documents."""
    if entity.kind != "class":
        return []
    prefix = entity.entity_id + "."
    return [
        (child.start_line, child.end_line)
        for child in eligible
        if child.file == entity.file and child.entity_id.startswith(prefix)
    ]


def _header(entity: CodeEntity) -> str:
    parts = [entity.entity_id]
    if entity.signature:
        parts.append(entity.signature)
    if entity.docstring:
        parts.append(entity.docstring)
    return "\n".join(parts)


def _chunks(
    entity: CodeEntity,
    header: str,
    body_lines: list[tuple[int, str]],
    max_lines: int,
    max_chars: int,
) -> list[Chunk]:
    """Greedy, in order: start a new chunk when the next piece would break either limit."""
    pieces: list[tuple[int, str]] = []
    for no, line in body_lines:
        if len(line) <= max_chars:
            pieces.append((no, line))
        else:
            pieces.extend((no, line[i : i + max_chars]) for i in range(0, len(line), max_chars))

    groups: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    size = 0
    for no, piece in pieces:
        if current and (len(current) >= max_lines or size + len(piece) > max_chars):
            groups.append(current)
            current, size = [], 0
        current.append((no, piece))
        size += len(piece)
    if current:
        groups.append(current)
    if not groups:
        groups = [[]]  # a class made only of methods still gets a header-only chunk

    out: list[Chunk] = []
    for index, group in enumerate(groups):
        body = _join(group)
        out.append(
            Chunk(
                chunk_id=f"{entity.entity_id}#{index}",
                entity_id=entity.entity_id,
                file=entity.file,
                index=index,
                spans=_spans([no for no, _ in group]),
                body=body,
                text=f"{header}\n{body}" if body else header,
            )
        )
    return out


def _join(group: list[tuple[int, str]]) -> str:
    """Pieces of one long line join with no separator; separate lines join with a newline."""
    out: list[str] = []
    previous: int | None = None
    for no, piece in group:
        if previous is not None:
            out.append("" if no == previous else "\n")
        out.append(piece)
        previous = no
    return "".join(out)


def _spans(numbers: list[int]) -> tuple[Span, ...]:
    """[3, 4, 5, 9, 9, 10] -> ((3, 5), (9, 10))."""
    spans: list[Span] = []
    for no in numbers:
        if spans and no <= spans[-1][1] + 1:
            spans[-1] = (spans[-1][0], max(no, spans[-1][1]))
        else:
            spans.append((no, no))
    return tuple(spans)


def _corpus_hash(pinned_sha: str, max_lines: int, max_chars: int, docs: list[EntityDoc]) -> str:
    payload = {
        "pinned_sha": pinned_sha,
        "chunker": [CHUNKER_VERSION, max_lines, max_chars],
        "chunks": [
            [c.chunk_id, c.file, [list(s) for s in c.spans], c.text]
            for doc in docs
            for c in doc.chunks
        ],
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
