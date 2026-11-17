"""semantic-run-v1 export: one semantic run written out for evaluation (CA-02).

A run directory holds two files:

- `semantic-run-v1.json`: the whole run. That covers the summary frozen into the bundle,
  the model ids and weight hashes, the corpus facts (pinned SHA, hash, chunker, skipped
  entities), the criteria word for word, and each ranker's state, time and top-N with
  scores and the chunk that won. It also holds the fused candidates with their gate.
- `predictions.csv`: one row per (criterion, ranker, rank), with the entity's file and
  line span. It is the file Saleha's evaluation harness reads next to her labels. The
  "fused" ranker rows are the RRF candidates, which are what the bundle shows.

Raw scores and spans live only here, never in the bundle, so the bundle hash does not
depend on float noise from a model.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from codeatlas.analyze.semantic_run import SCHEMA_ID, SemanticRunResult
from codeatlas.schema import Criterion

EXPORT_VERSION = "semantic-export@1"
JSON_NAME = "semantic-run-v1.json"
CSV_NAME = "predictions.csv"
CSV_COLUMNS = (
    "run_id",
    "repo",
    "pinned_sha",
    "criterion_id",
    "criterion_version",
    "criterion_text",
    "ranker",
    "rank",
    "entity_id",
    "file",
    "start_line",
    "end_line",
    "score",
    "gate",
    "model_id",
)


def export_document(
    result: SemanticRunResult,
    criteria: list[Criterion],
    *,
    run_id: str,
    repo: str,
    top_n: int = 10,
) -> dict[str, Any]:
    corpus = result.corpus
    return {
        "schema_id": SCHEMA_ID,
        "export_version": EXPORT_VERSION,
        "run_id": run_id,
        "repo": repo,
        # pydantic writes +inf ("never accept") as null in JSON
        "summary": json.loads(result.summary.model_dump_json()),
        "model_ids": result.model_ids,
        "model_file_hashes": dict(sorted(result.model_file_hashes.items())),
        "corpus": {
            "pinned_sha": corpus.pinned_sha,
            "corpus_hash": corpus.corpus_hash,
            "chunker_version": corpus.chunker_version,
            "max_lines": corpus.max_lines,
            "max_chars": corpus.max_chars,
            "entities": len(corpus.docs),
            "chunks": len(corpus.chunks()),
            "skipped": [{"entity_id": e, "reason": r} for e, r in corpus.skipped],
        },
        "criteria": [
            {
                "criterion_id": c.criterion_id,
                "key": c.key,
                "version_no": c.version_no,
                "text": c.text,
            }
            for c in criteria
        ],
        "rankers": [
            {
                "name": o.name,
                "model_id": o.model_id,
                "state": o.state,
                "reason": o.reason,
                "seconds": round(o.seconds, 3),
                "rankings": {
                    cid: [
                        {
                            "rank": r.rank,
                            "entity_id": r.entity_id,
                            "score": round(r.score, 6),
                            "chunk_id": r.best_chunk_id,
                        }
                        for r in ranking[:top_n]
                    ]
                    for cid, ranking in sorted(o.rankings.items())
                },
            }
            for o in result.outputs
        ],
        "candidates": [c.model_dump(mode="json") for c in result.candidates],
    }


def prediction_rows(
    result: SemanticRunResult,
    criteria: list[Criterion],
    *,
    run_id: str,
    repo: str,
    top_n: int = 10,
) -> list[dict[str, str]]:
    docs = {d.entity_id: d for d in result.corpus.docs}
    chunks = {c.chunk_id: c for c in result.corpus.chunks()}
    gates = {g.criterion_id: g.gate for g in result.gates}

    def span(entity_id: str, chunk_id: str | None) -> tuple[str, str]:
        chunk = chunks.get(chunk_id) if chunk_id else None
        if chunk is not None and chunk.spans:
            return str(chunk.spans[0][0]), str(chunk.spans[-1][1])
        doc = docs.get(entity_id)
        return (str(doc.start_line), str(doc.end_line)) if doc else ("", "")

    def row(
        c: Criterion,
        ranker: str,
        rank: int,
        entity_id: str,
        score: float,
        model_id: str,
        chunk_id: str | None,
    ) -> dict[str, str]:
        start, end = span(entity_id, chunk_id)
        doc = docs.get(entity_id)
        return {
            "run_id": run_id,
            "repo": repo,
            "pinned_sha": result.corpus.pinned_sha,
            "criterion_id": c.criterion_id,
            "criterion_version": str(c.version_no),
            "criterion_text": c.text,
            "ranker": ranker,
            "rank": str(rank),
            "entity_id": entity_id,
            "file": doc.file if doc else "",
            "start_line": start,
            "end_line": end,
            "score": f"{score:.6f}",
            "gate": gates.get(c.criterion_id, ""),
            "model_id": model_id,
        }

    rows: list[dict[str, str]] = []
    for c in criteria:
        for o in result.outputs:
            for r in o.rankings.get(c.criterion_id, [])[:top_n]:
                rows.append(
                    row(c, o.name, r.rank, r.entity_id, r.score, o.model_id, r.best_chunk_id)
                )
        for cand in result.candidates:
            if cand.criterion_id == c.criterion_id:
                rows.append(
                    row(
                        c,
                        "fused",
                        cand.fused_rank,
                        cand.entity_id,
                        cand.fused_score,
                        cand.model_id,
                        None,
                    )
                )
    return rows


def predictions_csv(rows: list[dict[str, str]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def write_export(
    out_dir: Path,
    result: SemanticRunResult,
    criteria: list[Criterion],
    *,
    run_id: str,
    repo: str,
    top_n: int = 10,
) -> tuple[Path, Path]:
    """Write `<out_dir>/semantic-run-v1.json` and `<out_dir>/predictions.csv`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = export_document(result, criteria, run_id=run_id, repo=repo, top_n=top_n)
    rows = prediction_rows(result, criteria, run_id=run_id, repo=repo, top_n=top_n)
    json_path, csv_path = out_dir / JSON_NAME, out_dir / CSV_NAME
    json_path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    csv_path.write_text(predictions_csv(rows), encoding="utf-8", newline="")
    return json_path, csv_path


__all__ = [
    "CSV_COLUMNS",
    "CSV_NAME",
    "EXPORT_VERSION",
    "JSON_NAME",
    "export_document",
    "prediction_rows",
    "predictions_csv",
    "write_export",
]
