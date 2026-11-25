# Semantic runs on the frozen dataset cases (CA-02, FYP-1 sprint)

This is how CA-02 runs on the frozen cases of `codeatlas-eval`. It covers tasks 9 and 21 of the dataset plan (Final, 21 Nov 2026). All commands run from the CodeAtlas folder.

## Before the labels are frozen: embed the code (task 9)

```powershell
uv run python scripts/precompute_embeddings.py --repos-dir D:\FYP\codeatlas-eval\eval\repos --case D:\FYP\codeatlas-eval\eval\cases\AF-01 --case D:\FYP\codeatlas-eval\eval\cases\AR-01
```

The script reads only the code at each case's `head_at_merge`, under its `analysis_root`, and embeds every chunk with MiniLM and CodeT5+ into `.codeatlas-cache/`. No criterion, issue text or label is read, so running it early reveals nothing about the labels or about CodeAtlas's answers.

## After gate 3: run every case, check it, score it (task 21)

```powershell
uv run python scripts/run_semantic_cases.py --repos-dir D:\FYP\codeatlas-eval\eval\repos --cases-dir D:\FYP\codeatlas-eval\eval\cases --out runs\sprint --labels semantic_labels.csv
```

For each case the runner does three things:

1. It runs BM25, MiniLM and CodeT5+ on `packet/criteria.csv`, word for word.
2. It writes `runs/sprint/<case_id>/`. The run id is the case id and the repo is the repository name.
3. It checks that every model ranked exactly `packet/entities.csv` and writes `universe_check.json`.

If a case fails that check, it is listed and never scored, and the exit code is 3.

`--labels` is a CSV of the adjudicated semantic labels with the columns `criterion_id, criterion_text, entity_id`. A criterion has one row per relevant entity, and an empty `entity_id` means no match. The `entity_id` can be the `label_ref` form, for example `airflow/models/dag.py::DAG.clear`.

## What the results contain

| Output | Content |
|---|---|
| `cases_summary.md` / `.json` | Per case: repository, analysis root, entities, criteria, entity-list check, gate counts |
| `evaluation.md` / `.json` | Primary measures: MRR and recall at 1, 5 and 10, each with a 95% bootstrap interval that resamples whole cases (2000 rounds, seed 20261114). Exploratory measures: recall at 20 and 50, and the median first-relevant rank. Precision, recall and F1 at 1, 5 and 10. Hit rates with Wilson intervals. Per-repository tables. Gate outcomes, with no-match criteria counted separately |
| `recall_curve.csv` / `.svg` | Recall at k for each ranker |

A bootstrap interval needs at least 2 cases. With fewer, the table says so instead of showing a range.
