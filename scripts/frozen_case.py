"""Read what a semantic run needs from a frozen dataset case (codeatlas-eval eval/cases/<ID>/).

A frozen case folder holds case.yaml (written by freeze_case.py from a fixed template) and,
after prepare_packets.py, packet/criteria.csv and packet/entities.csv. A semantic run needs
only five facts from it: the case id, the repository, the head commit at the merge, the
analysis root and the Jira keys. The run never reads the labels, the sources/ evidence or the
live APIs.

A case whose case.yaml says `jira_keys: [none]` (the no_key category, e.g. SP-03) has no
requirement, so prepare_packets.py builds no packet for it and there is nothing to rank: the
runner records it as "no criteria" instead of failing. A case with keys still needs a packet.

The repository's clone is <repos-dir>/<name>, where <name> is the last part of case.yaml's
`repo` (apache/airflow -> airflow), the layout codeatlas-eval uses for eval/repos/.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

CASE_FILE = "case.yaml"
PACKET = "packet"


@dataclass(frozen=True)
class FrozenCase:
    folder: Path
    case_id: str
    repo: str  # e.g. apache/airflow
    head_at_merge: str
    analysis_root: str  # "" for the whole repository
    jira_keys: tuple[str, ...] | None = None  # None when case.yaml has no jira_keys line

    @property
    def no_key(self) -> bool:
        """True only when case.yaml states that the PR names no Jira key."""
        return self.jira_keys == ()

    @property
    def repo_name(self) -> str:
        return self.repo.rstrip("/").rsplit("/", 1)[-1]

    @property
    def criteria_csv(self) -> Path:
        return self.folder / PACKET / "criteria.csv"

    @property
    def entities_csv(self) -> Path:
        return self.folder / PACKET / "entities.csv"

    def clone(self, repos_dir: Path) -> Path:
        return repos_dir / self.repo_name

    def target_spec(self, repos_dir: Path) -> str:
        return f"{self.clone(repos_dir)}@{self.head_at_merge}"


def case_field(text: str, name: str) -> str:
    """A top-level scalar of case.yaml; the file comes from a fixed template, so a line match
    is enough (the same reader prepare_packets.py uses). Comments and quotes are dropped."""
    match = re.search(rf"^{re.escape(name)}:[ \t]*(.*?)[ \t]*(?:#.*)?$", text, re.MULTILINE)
    return match.group(1).strip().strip('"').strip("'") if match else ""


def parse_keys(value: str) -> tuple[str, ...]:
    """`[ARROW-1, ARROW-2]` -> ("ARROW-1", "ARROW-2"); `[none]`, `[]` and `none` -> ()."""
    items = (part.strip().strip('"').strip("'") for part in value.strip().strip("[]").split(","))
    return tuple(k for k in items if k and k.lower() != "none")


def read_case(folder: Path) -> FrozenCase:
    path = folder / CASE_FILE
    if not path.is_file():
        raise ValueError(f"{folder} has no {CASE_FILE}: not a frozen case folder")
    text = path.read_text(encoding="utf-8")
    case = FrozenCase(
        folder=folder,
        case_id=case_field(text, "case_id") or folder.name,
        repo=case_field(text, "repo"),
        head_at_merge=case_field(text, "head_at_merge"),
        analysis_root=case_field(text, "analysis_root").strip("/"),
        jira_keys=(
            parse_keys(case_field(text, "jira_keys"))
            if re.search(r"^jira_keys:", text, re.MULTILINE)
            else None
        ),
    )
    if not case.repo or not re.fullmatch(r"[0-9a-f]{40}", case.head_at_merge):
        raise ValueError(f"{path}: needs repo and a full 40-character head_at_merge")
    return case


__all__ = ["CASE_FILE", "PACKET", "FrozenCase", "case_field", "parse_keys", "read_case"]
