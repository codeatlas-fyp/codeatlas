"""Phase 1 evaluation: run CodeAtlas on recorded and generated SBX issues, write the report.

Every number in docs/report/phase1-evaluation.md comes from this script; nothing is typed.

1. Recorded demo issues SBX-1..7 (tests/fixtures/jira): versions and case results.
2. Generated issues (ground truth from tools/scenario_generator): collected live from Jira
   (read-only), evaluated with two policies (generator account has / lacks priority authority),
   compared with the oracle: version exact matches, per-case accuracy, false PASS and false FAIL.
3. Determinism (two separate processes give the same verdict_hash), tamper test (exit 5).
4. Branch coverage, mutation score (docs/report/data/mutation-score.json), property examples.

Limitation stated in the report: the GitHub collector is Yusra's (step 9), so the PR's commit
times for C1 and C6 are taken from the ground truth as explicit test input.

Usage: uv run python scripts/evaluate_phase1.py [--truth docs/report/data/ground-truth-*.json]
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codeatlas.bundle.store import BundleStore, load_bundle  # noqa: E402
from codeatlas.collect.jira.adf import adf_to_text  # noqa: E402
from codeatlas.collect.jira.client import JiraClient  # noqa: E402
from codeatlas.collect.jira.recorder import FixtureJiraSource  # noqa: E402
from codeatlas.config import ChangeFile, Settings, load_policy  # noqa: E402
from codeatlas.logging import EventLog  # noqa: E402
from codeatlas.pipeline import (  # noqa: E402
    CollectRequest,
    JiraSource,
    collect_and_evaluate,
    now_utc,
)
from codeatlas.schema import AuthorityPolicy, IdentityMap, Policy, Verdict  # noqa: E402

DATA = ROOT / "docs" / "report" / "data"
REPORT = ROOT / "docs" / "report" / "phase1-evaluation.md"
STORE = ROOT / ".recordings" / "evaluation-store"  # live bundles hold real account ids
CASES = ("C1", "C3", "C6", "C8")
COVERAGE_TARGETS = {
    "schema/": "src/codeatlas/schema/",
    "bundle/": "src/codeatlas/bundle/",
    "evaluate/": "src/codeatlas/evaluate/",
    "analyze/lifecycle.py": "src/codeatlas/analyze/lifecycle.py",
    "collect/jira/": "src/codeatlas/collect/jira/",
}


@dataclass
class CaseTally:
    correct: int = 0
    total: int = 0
    false_pass: int = 0  # expected VIOLATED, got SATISFIED
    false_fail: int = 0  # expected SATISFIED, got VIOLATED
    other: int = 0  # INSUFFICIENT_EVIDENCE or NOT_APPLICABLE where a judgement was expected

    def add(self, want: str, got: str) -> None:
        self.total += 1
        if want == got:
            self.correct += 1
        elif want == "VIOLATED" and got == "SATISFIED":
            self.false_pass += 1
        elif want == "SATISFIED" and got == "VIOLATED":
            self.false_fail += 1
        else:
            self.other += 1


def change_for(key: str, commit_times: list[str], number: int) -> ChangeFile:
    return ChangeFile.model_validate(
        {
            "repo": "codeatlas-fyp/codeatlas-sandbox",
            "pr_number": number,
            "base_sha": "0" * 40,
            "head_sha": "1" * 40,
            "merge_base_sha": "0" * 40,
            "work_items": [{"key": key, "found_in": "pr_title"}],
            "commits": [
                {
                    "sha": f"{number:020d}{i:020d}",
                    "author_email": "user-02@example.test",
                    "author_login": "user-02",
                    "committed_at": at,
                    "message": f"{key}: implement",
                }
                for i, at in enumerate(commit_times)
            ],
        }
    )


def evaluate_one(
    source: JiraSource, policy: Policy, change: ChangeFile, store: BundleStore
) -> tuple[str, Verdict]:
    request = CollectRequest(
        change=change,
        change_label="evaluate_phase1",
        policy=policy,
        identity_map=IdentityMap(people=[]),
        run_id=uuid.uuid4().hex,
        collected_at=now_utc(),
    )
    with open(STORE / "pipeline.log", "a", encoding="utf-8") as log_file:
        return collect_and_evaluate(
            request, source, store, EventLog(request.run_id, stream=log_file)
        )


def outcome(verdict: Verdict, case: str) -> str:
    return next(r.outcome for r in verdict.results if r.case == case)


# --- 1. recorded demo issues ---


def recorded_section(policy: Policy) -> list[str]:
    source = FixtureJiraSource(ROOT / "tests" / "fixtures" / "jira")
    store = BundleStore(Path(tempfile.mkdtemp()))
    lines = [
        "| Issue | Labels | History entries | Versions | Verdict | C1 | C3 | C6 | C8 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for n in range(1, 8):
        key = f"SBX-{n}"
        issue = source.fetch_issue(key)
        bundle_hash, verdict = evaluate_one(
            source, policy, change_for(key, ["2026-10-07T12:00:00Z"], n), store
        )
        bundle = load_bundle(store.folder(bundle_hash))
        labels = ", ".join(issue["fields"].get("labels") or [])
        cells = " | ".join(outcome(verdict, c) for c in CASES)
        lines.append(
            f"| {key} | {labels} | {len(source.fetch_changelog(key))} | {len(bundle.versions)} "
            f"| {verdict.value} | {cells} |"
        )
    return lines


# --- 2. generated issues ---


def generated_section(truth_path: Path | None, base_policy: Policy) -> tuple[list[str], list[Path]]:
    if truth_path is None:
        return ["**Not run.** No ground-truth file was given (`--truth`)."], []
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    settings = Settings()
    base_url, email, token = settings.jira()
    client = JiraClient(base_url, email, token)
    # The generator's own account id (read-only GET /myself); used only in memory, never stored.
    me = str(client._get("/myself", {})["accountId"])
    with_authority = base_policy.model_copy(
        update={
            "approval": base_policy.approval.model_copy(update={"approver_account_ids": [me]}),
            "priority_authority": AuthorityPolicy(account_ids=[me]),
        }
    )
    without_authority = with_authority.model_copy(
        update={"priority_authority": AuthorityPolicy(account_ids=["nobody"])}
    )
    STORE.mkdir(parents=True, exist_ok=True)
    store = BundleStore(STORE)
    tallies = {name: CaseTally() for name in ("C1", "C3", "C6", "C8 with authority", "C8 without")}
    version_matches = 0
    folders: list[Path] = []
    for number, record in enumerate(truth["issues"], start=1):
        key, want = record["key"], record["expected"]
        times = {a["kind"]: a["at"] for a in record["actions"] if a["kind"].endswith("_commit")}
        change = change_for(key, [times["first_commit"], times["last_commit"]], 1000 + number)
        hash_a, verdict_a = evaluate_one(client, with_authority, change, store)
        _, verdict_b = evaluate_one(client, without_authority, change, store)
        folders.append(store.folder(hash_a))
        bundle = load_bundle(store.folder(hash_a))
        got_versions = [v.description for v in bundle.versions]
        want_versions = [adf_to_text(_adf(t)) for t in want["versions"]]
        version_matches += got_versions == want_versions
        tallies["C1"].add(want["c1"], outcome(verdict_a, "C1"))
        tallies["C3"].add(want["c3"], outcome(verdict_a, "C3"))
        tallies["C6"].add(want["c6"], outcome(verdict_a, "C6"))
        tallies["C8 with authority"].add(want["c8_with_authority"], outcome(verdict_a, "C8"))
        tallies["C8 without"].add(want["c8_without_authority"], outcome(verdict_b, "C8"))
    client.close()
    total = len(truth["issues"])
    shown = truth_path.resolve().relative_to(ROOT).as_posix()
    lines = [
        f"Ground truth: `{shown}` (seed {truth['seed']}, "
        f"{total} issues labelled `{truth['label']}`).",
        "",
        f"**Version reconstruction, exact match:** {version_matches} / {total} issues.",
        "",
        "| Case | Correct | Accuracy | False PASS | False FAIL | Other |",
        "|---|---|---|---|---|---|",
    ]
    for name, tally in tallies.items():
        accuracy = f"{tally.correct / tally.total:.1%}" if tally.total else "n/a"
        lines.append(
            f"| {name} | {tally.correct} / {tally.total} | {accuracy} | {tally.false_pass} "
            f"| {tally.false_fail} | {tally.other} |"
        )
    lines += [
        "",
        "False PASS: the oracle expected VIOLATED and CodeAtlas said SATISFIED. False FAIL: the "
        "reverse. Other: INSUFFICIENT_EVIDENCE or NOT_APPLICABLE where a judgement was expected.",
    ]
    return lines, folders


def _adf(text: str) -> dict[str, Any]:
    return {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": line}]}
            for line in text.split("\n")
        ],
    }


# --- 3. determinism and tampering ---


def cli(*args: str) -> subprocess.CompletedProcess[str]:
    code = "from codeatlas.cli import app; app()"
    return subprocess.run(
        [sys.executable, "-c", code, *args], cwd=ROOT, capture_output=True, text=True, check=False
    )


def determinism_section(folders: list[Path]) -> list[str]:
    if not folders:
        sample = next(p for p in (ROOT / "tests" / "fixtures" / "bundles").iterdir() if p.is_dir())
        folders = [sample]
    work = Path(tempfile.mkdtemp())
    same = 0
    for folder in folders:
        copy = work / folder.name
        shutil.copytree(folder, copy, dirs_exist_ok=True)
        hashes = []
        for _ in range(2):
            cli("evaluate", "--bundle", str(copy))
            hashes.append(json.loads((copy / "verdict.json").read_text())["verdict_hash"])
        same += hashes[0] == hashes[1]
    tampered = work / "tampered" / folders[0].name
    shutil.copytree(folders[0], tampered)
    raw = (tampered / "bundle.json").read_bytes()
    (tampered / "bundle.json").write_bytes(raw[:-2] + bytes([raw[-2] ^ 1]) + raw[-1:])
    replay = cli("replay", str(tampered))
    return [
        f"**Determinism:** {same} / {len(folders)} bundles gave the same `verdict_hash` when "
        "evaluated twice in separate processes.",
        "",
        f"**Tamper test:** one byte of a stored bundle flipped; `codeatlas replay` exit code "
        f"{replay.returncode} (expected 5).",
    ]


# --- 4. coverage, mutation, property examples ---------------------------------------------------


def coverage_section() -> list[str]:
    out = Path(tempfile.mkdtemp()) / "coverage.json"
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--cov=codeatlas",
         "--cov-branch", f"--cov-report=json:{out}"],
        cwd=ROOT, capture_output=True, check=False,
    )  # fmt: skip
    files = json.loads(out.read_text())["files"]
    lines = ["| Package | Statements | Branches | Branch coverage |", "|---|---|---|---|"]
    for name, prefix in COVERAGE_TARGETS.items():
        chosen = [s for path, s in files.items() if path.replace("\\", "/").startswith(prefix)]
        statements = sum(s["summary"]["num_statements"] for s in chosen)
        covered = sum(s["summary"]["covered_lines"] for s in chosen)
        branches = sum(s["summary"]["num_branches"] for s in chosen)
        partial = sum(s["summary"]["covered_branches"] for s in chosen)
        percent = (covered + partial) / (statements + branches) if statements + branches else 0
        lines.append(f"| `{name}` | {statements} | {branches} | {percent:.1%} |")
    return lines


def mutation_section() -> list[str]:
    path = DATA / "mutation-score.json"
    if not path.is_file():
        return ["**Not run.** `docs/report/data/mutation-score.json` is missing."]
    data = json.loads(path.read_text())
    survivors = [r for r in data["results"] if not r["killed"]]
    lines = [
        f"Tool: {data['tool']}.",
        "",
        f"**Mutation score:** {data['killed']} / {data['mutants']} mutants killed "
        f"({data['score']:.1%}) in `analyze/lifecycle.py` and `evaluate/rules/`.",
        "",
        "Surviving mutants:",
        "",
    ]
    lines += [f"- `{r['file']}` {r['mutation']}" for r in survivors] or ["- none"]
    return lines


def property_section() -> list[str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/property",
         "--hypothesis-show-statistics"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )  # fmt: skip
    counts = [int(n) for n in re.findall(r"- (\d+) passing,", result.stdout)]
    tests = re.findall(r"::(test_\w+):", result.stdout)
    return [
        f"**Property-based tests:** {len(set(tests))} properties, {sum(counts)} generated examples "
        f"passed in total (pytest exit code {result.returncode})."
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--truth", type=Path)
    parser.add_argument("--policy", type=Path, default=ROOT / "config" / "policy.example.yaml")
    args = parser.parse_args()
    STORE.mkdir(parents=True, exist_ok=True)
    policy, _ = load_policy(args.policy)
    generated, folders = generated_section(args.truth, policy)
    when = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    sections = [
        "# Phase 1 evaluation",
        "",
        f"Generated by `scripts/evaluate_phase1.py` on {when} at commit `{commit}`. Every number "
        "below is computed by the script; none is typed by hand.",
        "",
        "## Limitation",
        "",
        "The GitHub collector and git adapter are Yusra's (step 9) and do not exist yet. The PR's "
        "commit times, which C1 and C6 depend on, are therefore **explicit test input**: for "
        "generated issues they come from the ground truth's commit markers; for the recorded demo "
        "issues a single commit at 2026-10-07T12:00:00Z is assumed.",
        "",
        "## 1. Recorded demo issues (SBX-1..7)",
        "",
        *recorded_section(policy),
        "",
        "## 2. Generated issues against ground truth",
        "",
        *generated,
        "",
        "## 3. Determinism and integrity",
        "",
        *determinism_section(folders[:10]),
        "",
        "## 4. Test strength",
        "",
        *coverage_section(),
        "",
        *mutation_section(),
        "",
        *property_section(),
        "",
    ]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(sections), encoding="utf-8", newline="\n")
    print(REPORT)


if __name__ == "__main__":
    main()
