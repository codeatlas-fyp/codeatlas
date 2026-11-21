"""Manual smoke check for the CodeT5+ adapter (CA-02). Downloads the pinned model once.

    uv run python scripts/smoke_codet5p.py

Prints the model id, the weights file hash, the vector size and norms, and the cosine of a
criterion against a matching and a non-matching function. The matching one should score
higher. Not part of pytest, because tests never touch the network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from codeatlas.analyze.codet5p import EmbedderUnavailable, load_codet5p

CRITERION = "When a member returns a book, the loan is closed and a late fee is charged."
MATCH = (
    "def return_book(loan_id: str) -> None:\n"
    "    loan = find_loan(loan_id)\n"
    "    loan.closed = True\n"
    "    charge_late_fee(loan)\n"
)
OTHER = "def parse_config(path: str) -> dict:\n    return yaml.safe_load(open(path))\n"


def main() -> int:
    try:
        embedder = load_codet5p(embedding_cache_dir=Path(".codeatlas-cache") / "embeddings")
    except EmbedderUnavailable as exc:
        print(f"UNAVAILABLE: {exc}")
        return 1
    vecs = embedder.embed([CRITERION, MATCH, OTHER, "x = 1\n" * 400])
    print("model_id   ", embedder.model_id)
    print("weights    ", embedder.weights)
    print("load mode  ", embedder.load_mode)
    if embedder.load_note:
        print("load note  ", embedder.load_note)
    print("shape      ", vecs.shape)
    print("norms      ", np.round(np.linalg.norm(vecs, axis=1), 4).tolist())
    print("cos match  ", round(float(vecs[0] @ vecs[1]), 4))
    print("cos other  ", round(float(vecs[0] @ vecs[2]), 4))
    print("split texts", embedder.stats.split_texts, "windows", embedder.stats.windows)
    ok = vecs.shape[1] == 256 and float(vecs[0] @ vecs[1]) > float(vecs[0] @ vecs[2])
    print("RESULT     ", "OK" if ok else "CHECK")
    _show_remote_forward()
    return 0 if ok else 2


def _show_remote_forward() -> None:
    """Print the remote code's embedding line, to check the native loader copies it."""
    from huggingface_hub import snapshot_download

    from codeatlas.analyze.model_registry import spec

    codet5p = spec("codet5p")
    snapshot = Path(
        snapshot_download(codet5p.repo_id, revision=codet5p.revision, local_files_only=True)
    )
    for path in sorted(snapshot.glob("modeling_*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if "normalize" in line or "self.proj" in line or "T5Stack(" in line:
                print("remote code", path.name, "|", line.strip())


if __name__ == "__main__":
    sys.exit(main())
