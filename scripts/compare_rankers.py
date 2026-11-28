"""Run BM25, MiniLM and CodeT5+ on CodeAtlas's own source and compare them (CA-02).

    uv run python scripts/compare_rankers.py

A quick, local comparison on a few hand-written criteria whose answer is known: the
entity a developer would point to. It prints each ranker's state, time and top 3, and the
rank it gave the expected entity. These numbers are a smoke test, not the evaluation: the
real comparison uses labelled cases from the dataset.
"""

from __future__ import annotations

import sys
from pathlib import Path

from codeatlas.analyze.code import extract_entities
from codeatlas.analyze.codet5p import load_codet5p
from codeatlas.analyze.minilm import load_minilm
from codeatlas.analyze.rankers import RankerOutput, rank_bm25, rank_dense
from codeatlas.analyze.semantic_corpus import build_corpus
from codeatlas.schema import Criterion

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".codeatlas-cache" / "embeddings"

# (criterion text, entity a reviewer would expect)
CASES = [
    (
        "The same evidence must produce byte-identical JSON on every machine before hashing.",
        "codeatlas.bundle.canonical.canonical_bytes",
    ),
    (
        "Find the owners of a changed file from the CODEOWNERS rules.",
        "codeatlas.codeowners.owners_for",
    ),
    (
        "Re-evaluate a stored bundle and refuse it when the verdict hash differs.",
        "codeatlas.bundle.replay.replay",
    ),
    (
        "Extract Jira issue keys from the branch name, PR title, body and commit messages.",
        "codeatlas.analyze.keys.find_keys",
    ),
    (
        "Split the acceptance criteria text into separate criteria.",
        "codeatlas.analyze.criteria.split_criteria",
    ),
]


def main() -> int:
    sources = {
        path.relative_to(ROOT / "src").as_posix(): path.read_bytes()
        for path in sorted((ROOT / "src").rglob("*.py"))
    }
    entities = [e for p, s in sources.items() for e in extract_entities(file_path=p, source=s)]
    corpus = build_corpus(entities=entities, file_sources=sources, pinned_sha="working-tree")
    criteria = [
        Criterion(
            criterion_id=f"DEMO-{i}:v1:ac1",
            key=f"DEMO-{i}",
            version_no=1,
            text=text,
            derived_from_description=False,
        )
        for i, (text, _) in enumerate(CASES, start=1)
    ]
    print(f"corpus: {len(corpus.docs)} entities, {len(corpus.chunks())} chunks")
    outputs: list[RankerOutput] = [
        rank_bm25(corpus, criteria),
        rank_dense("minilm", corpus, criteria, lambda: load_minilm(embedding_cache_dir=CACHE)),
        rank_dense("codet5p", corpus, criteria, lambda: load_codet5p(embedding_cache_dir=CACHE)),
    ]
    for out in outputs:
        print(f"\n== {out.name}: {out.state} ({out.seconds:.1f}s) {out.model_id}")
        if out.reason:
            print(f"   reason: {out.reason}")
    hits = {o.name: 0 for o in outputs}
    rr = {o.name: 0.0 for o in outputs}
    for criterion, (_, expected) in zip(criteria, CASES, strict=True):
        print(f"\n{criterion.criterion_id}: {criterion.text}")
        print(f"   expected: {expected}")
        for out in outputs:
            ranking = out.rankings.get(criterion.criterion_id, [])
            rank = out.rank_of(criterion.criterion_id, expected)
            top = ", ".join(".".join(r.entity_id.split(".")[-2:]) for r in ranking[:3])
            print(f"   {out.name:8} rank {rank if rank else '-':>4}  top3: {top}")
            if rank is not None:
                hits[out.name] += rank <= 5
                rr[out.name] += 1 / rank
    print("\nsummary (5 demo criteria):")
    for out in outputs:
        print(f"   {out.name:8} hit@5 {hits[out.name]}/5   MRR {rr[out.name] / len(CASES):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
