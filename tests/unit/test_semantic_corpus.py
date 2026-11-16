"""Retrieval corpus (CA-02): bodies, spans, lossless deterministic chunking."""

from codeatlas.analyze.code import extract_entities
from codeatlas.analyze.semantic_corpus import CHUNKER_VERSION, Chunk, build_corpus
from codeatlas.schema import CodeEntity

LOANS = b'''"""Loans."""


def return_book(loan_id: str) -> None:
    """Close the loan."""
    loan = find(loan_id)
    loan.closed = True


class Ledger:
    """Money owed."""

    rate = 2

    def charge(self, days: int) -> int:
        return days * self.rate

    def waive(self) -> None:
        pass
'''


def _corpus(source: bytes = LOANS, **kwargs: int):  # type: ignore[no-untyped-def]
    entities = extract_entities(file_path="library/loans.py", source=source)
    return build_corpus(
        entities=entities,
        file_sources={"library/loans.py": source},
        pinned_sha="a" * 40,
        **kwargs,
    )


def _doc(corpus, entity_id: str):  # type: ignore[no-untyped-def]
    return next(d for d in corpus.docs if d.entity_id == entity_id)


def _rebuild(chunks: tuple[Chunk, ...]) -> str:
    """Join chunk bodies back; a chunk that continues the previous line joins with no newline."""
    out = ""
    last_line = None
    for chunk in chunks:
        if not chunk.body:
            continue
        if last_line is not None:
            out += "" if chunk.spans[0][0] == last_line else "\n"
        out += chunk.body
        last_line = chunk.spans[-1][1]
    return out


def test_function_document_has_body_header_and_exact_span() -> None:
    doc = _doc(_corpus(), "library.loans.return_book")
    (chunk,) = doc.chunks
    assert "loan.closed = True" in chunk.text
    assert chunk.text.startswith("library.loans.return_book\ndef return_book")
    assert "Close the loan." in doc.header
    assert chunk.spans == ((4, 7),)
    assert chunk.chunk_id == "library.loans.return_book#0"


def test_modules_are_not_in_the_corpus() -> None:
    assert all(d.kind != "module" for d in _corpus().docs)


def test_class_body_leaves_out_its_methods() -> None:
    doc = _doc(_corpus(), "library.loans.Ledger")
    (chunk,) = doc.chunks
    assert "rate = 2" in chunk.body
    assert "days * self.rate" not in chunk.body
    assert chunk.spans == ((10, 13),)  # trailing blank line 14 dropped
    method = _doc(_corpus(), "library.loans.Ledger.charge")
    assert "days * self.rate" in method.chunks[0].body


def test_long_body_is_split_and_every_line_is_kept_once() -> None:
    body = "".join(f"    x{i} = {i}\n" for i in range(95))
    source = b"def big():\n" + body.encode()
    doc = _doc(_corpus(source, max_lines=40), "library.loans.big")
    assert len(doc.chunks) == 3
    covered = [no for c in doc.chunks for lo, hi in c.spans for no in range(lo, hi + 1)]
    assert covered == list(range(1, 97))
    assert _rebuild(doc.chunks) == "def big():\n" + body.rstrip("\n")
    assert all(c.text.startswith(doc.header) for c in doc.chunks)


def test_line_longer_than_max_chars_is_cut_without_loss() -> None:
    long_value = "z" * 250
    source = f"def wide():\n    s = '{long_value}'\n".encode()
    doc = _doc(_corpus(source, max_chars=100), "library.loans.wide")
    assert len(doc.chunks) > 2
    assert all(len(c.body) <= 100 for c in doc.chunks)
    assert _rebuild(doc.chunks) == f"def wide():\n    s = '{long_value}'"


def test_missing_file_text_is_reported_not_dropped() -> None:
    entity = CodeEntity(
        entity_id="gone.f",
        kind="function",
        file="gone.py",
        start_line=1,
        end_line=2,
        signature=None,
        docstring=None,
    )
    corpus = build_corpus(entities=[entity], file_sources={}, pinned_sha="a" * 40)
    assert corpus.docs == ()
    assert corpus.skipped == (("gone.f", "file text not available at the pinned commit"),)


def test_crlf_and_form_feed_do_not_shift_line_numbers() -> None:
    source = b"x = 1\r\n\x0c\r\ndef f():\r\n    return 1\r\n"
    doc = _doc(_corpus(source), "library.loans.f")
    assert doc.chunks[0].spans == ((3, 4),)
    assert "\r" not in doc.chunks[0].body


def test_corpus_is_deterministic_and_hash_tracks_content() -> None:
    first, second = _corpus(), _corpus()
    assert first == second
    assert first.chunker_version == CHUNKER_VERSION
    changed = _corpus(LOANS.replace(b"days * self.rate", b"days * 3"))
    assert changed.corpus_hash != first.corpus_hash
    other_limits = _corpus(max_lines=5)
    assert other_limits.corpus_hash != first.corpus_hash
