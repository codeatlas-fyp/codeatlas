"""Pull-request collection end to end (G2): GitHub + git + governance + code + criteria + semantic.

GitHub answers come from the recorded sandbox responses (tests/fixtures/github, pr-1 and pr-2).
Those recordings were sanitised, so their commit SHAs are placeholders; the test builds a small
git repo with pygit2 and points each placeholder at a real commit in it. Jira answers come from
the recorded SBX issues. The network is blocked.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pygit2
import pytest
from typer.testing import CliRunner

from codeatlas import pipeline
from codeatlas.bundle.replay import replay
from codeatlas.bundle.store import BundleStore, load_bundle
from codeatlas.cli import app
from codeatlas.collect.git.repo import LocalRepo
from codeatlas.collect.jira.recorder import FixtureJiraSource
from codeatlas.config import load_policy
from codeatlas.errors import CollectionError
from codeatlas.evaluate.links import bundle_links
from codeatlas.logging import EventLog
from codeatlas.pipeline import (
    ChangeSources,
    CollectRequest,
    PullRequestTarget,
    _split_by_entity,
    collect_and_evaluate,
    collect_bundle,
    evaluate_bundle,
)
from codeatlas.schema import CodeEntity, EvidenceBundle, IdentityEntry, IdentityMap, Verdict

ROOT = Path(__file__).parents[2]
GITHUB = ROOT / "tests" / "fixtures" / "github"
JIRA = ROOT / "tests" / "fixtures" / "jira"
POLICY = ROOT / "config" / "policy.example.yaml"
MINI = ROOT / "tests" / "fixtures" / "repos" / "mini_sandbox"
REPO = "codeatlas-fyp/codeatlas-sandbox"
AT = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)

ROUND_FEE = '''

def round_fee_to_cents(fee: Decimal) -> Decimal:
    """Show late fees to two decimal places."""
    return fee.quantize(Decimal("0.01"))
'''
LOAN_HELPER = '''

def count_open_loans(loans: list[str]) -> int:
    """How many loans are still open."""
    return len(loans)
'''


def _commit(repo: pygit2.Repository, files: dict[str, str], parents: list[Any], msg: str) -> str:
    index = pygit2.Index()
    for path, text in sorted(files.items()):
        blob = repo.create_blob(text.encode())
        index.add(pygit2.IndexEntry(path, blob, pygit2.GIT_FILEMODE_BLOB))
    tree = index.write_tree(repo)
    sig = pygit2.Signature("user-01", "user-01@example.test", 1_760_000_000, 0)
    oid = repo.create_commit(None, sig, sig, msg, tree, parents)
    return str(oid)


@pytest.fixture
def sandbox(tmp_path: Path) -> dict[str, str]:
    """A git repo whose commits stand in for the recordings' placeholder SHAs."""
    repo = pygit2.init_repository(str(tmp_path / "sandbox"), bare=False)
    fees = (MINI / "payments" / "fees.py").read_text(encoding="utf-8")
    base_files = {
        "CODEOWNERS": (GITHUB / REPO.replace("/", "__") / "pr-1" / "codeowners.txt").read_text(),
        "loans.py": (MINI / "loans.py").read_text(encoding="utf-8"),
        "payments/__init__.py": "",
        "payments/fees.py": fees,
        "docs/adr/0001-fees.md": "---\nstatus: accepted\nscope: [payments/**]\n"
        "requirement_keys: [SBX-4]\n---\n# Fees live in payments\n",
    }
    base = _commit(repo, base_files, [], "init")
    head1 = _commit(
        repo,
        {**base_files, "payments/fees.py": fees + ROUND_FEE},
        [pygit2.Oid(hex=base)],
        "feat(payments): add round_fee_to_cents helper for display (SBX-4)",
    )
    head2 = _commit(
        repo,
        {
            **base_files,
            "CODEOWNERS": base_files["CODEOWNERS"] + "/loans.py            @user-02\n",
            "loans.py": base_files["loans.py"] + LOAN_HELPER,
        },
        [pygit2.Oid(hex=base)],
        "chore(ownership): transfer loans.py ownership (SBX-5)",
    )
    return {
        "path": str(tmp_path / "sandbox"),
        "basesha0000000000000000000000000000000001": base,
        "headsha0000000000000000000000000000000001": head1,
        "basesha0000000000000000000000000000000002": base,
        "headsha0000000000000000000000000000000002": head2,
    }


class RecordedGitHub:
    """The recorded GitHub responses, with placeholder SHAs mapped to the test repo's commits."""

    def __init__(self, shas: dict[str, str]) -> None:
        self._shas = {k: v for k, v in shas.items() if k != "path"}

    def _load(self, number: int, name: str) -> Any:
        text = (GITHUB / REPO.replace("/", "__") / f"pr-{number}" / name).read_text("utf-8")
        for placeholder, sha in self._shas.items():
            text = text.replace(placeholder, sha)
        return json.loads(text)

    def pull_request(self, repo: str, number: int) -> dict[str, Any]:
        data: dict[str, Any] = self._load(number, "pr.json")
        return data

    def commits(self, repo: str, number: int) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = self._load(number, "commits.json")
        return data

    def reviews(self, repo: str, number: int) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = self._load(number, "reviews.json")
        return data


def _request(number: int, identity: IdentityMap | None = None) -> CollectRequest:
    policy, notes = load_policy(POLICY)
    return CollectRequest(
        change=None,
        change_label=f"pr:{REPO}#{number}",
        policy=policy,
        identity_map=identity or IdentityMap(people=[]),
        run_id=f"test-{number}",
        collected_at=AT,
        notes=notes,
        target=PullRequestTarget(repo=REPO, number=number),
        bm25_only=True,
    )


def _sources(sandbox: dict[str, str]) -> ChangeSources:
    return ChangeSources(github=RecordedGitHub(sandbox), git=LocalRepo(Path(sandbox["path"])))


def test_pr_bundle_holds_real_github_git_and_analysis_evidence(sandbox: dict[str, str]) -> None:
    bundle = collect_bundle(_request(1), FixtureJiraSource(JIRA), EventLog("t"), _sources(sandbox))

    head = sandbox["headsha0000000000000000000000000000000001"]
    assert bundle.change.head_sha == head
    assert bundle.change.merge_base_sha == sandbox["basesha0000000000000000000000000000000001"]
    assert [c.sha for c in bundle.commits] == [head]
    assert [(r.reviewer_login, r.state, r.commit_id) for r in bundle.reviews] == [
        ("user-02", "APPROVED", head)
    ]
    assert {w.key for w in bundle.work_items} == {"SBX-4"}
    assert bundle.governance.codeowners_path == "CODEOWNERS"
    assert len(bundle.governance.owner_rules) == 3
    assert [a.adr_id for a in bundle.governance.adrs]
    assert bundle.governance_changes == []
    assert [(c.file, c.entity_id) for c in bundle.changed] == [
        ("payments/fees.py", "payments.fees.round_fee_to_cents")
    ]
    assert bundle.criteria, "SBX-4's acceptance criteria were not split"
    assert bundle.candidates and bundle.model_ids
    assert {"github", "keys", "governance", "code", "criteria", "semantic"} <= set(
        bundle.extractor_versions
    )
    assert not any("not collected" in note for note in bundle.collection_notes)


def test_pr_verdict_uses_the_new_evidence(sandbox: dict[str, str]) -> None:
    bundle = collect_bundle(_request(1), FixtureJiraSource(JIRA), EventLog("t"), _sources(sandbox))
    results = {r.case: r for r in evaluate_bundle(bundle, "0" * 64).results}
    assert results["C2"].outcome == "SATISFIED"
    assert results["C5"].outcome == "SATISFIED"  # the owner of payments/ approved the head
    links = bundle_links(bundle)
    assert [(lk.requirement_key, lk.entity_id, lk.state) for lk in links] == [
        ("SBX-4", "payments.fees.round_fee_to_cents", "OBSERVED")
    ]


def test_governance_edit_fires_c2_and_unapproved_owner_fires_c5(sandbox: dict[str, str]) -> None:
    bundle = collect_bundle(_request(2), FixtureJiraSource(JIRA), EventLog("t"), _sources(sandbox))
    assert [g.path for g in bundle.governance_changes] == ["CODEOWNERS"]
    assert ("loans.py", "loans.count_open_loans") in [(c.file, c.entity_id) for c in bundle.changed]
    results = {r.case: r for r in evaluate_bundle(bundle, "0" * 64).results}
    assert results["C2"].outcome == "VIOLATED"
    assert bundle.reviews == []


def test_collect_then_replay_gives_the_same_verdict(
    sandbox: dict[str, str], tmp_path: Path
) -> None:
    store = BundleStore(tmp_path / "store")
    bundle_hash, verdict = collect_and_evaluate(
        _request(1), FixtureJiraSource(JIRA), store, EventLog("t"), _sources(sandbox)
    )
    result = replay(tmp_path / "store" / bundle_hash, evaluate_bundle)
    assert result.recomputed_verdict_hash == verdict.verdict_hash
    assert load_bundle(tmp_path / "store" / bundle_hash).changed


def test_people_are_resolved_through_the_identity_map(sandbox: dict[str, str]) -> None:
    identity = IdentityMap(
        people=[
            IdentityEntry(jira_account_id="acc-1", github_login="user-01", git_emails=[]),
            IdentityEntry(jira_account_id="acc-2", github_login="user-02", git_emails=[]),
        ]
    )
    bundle = collect_bundle(
        _request(1, identity), FixtureJiraSource(JIRA), EventLog("t"), _sources(sandbox)
    )
    assert {(p.github_login, p.jira_account_id) for p in bundle.people} == {
        ("user-01", "acc-1"),
        ("user-02", "acc-2"),
    }


def test_commits_missing_from_the_clone_are_a_collection_error(sandbox: dict[str, str]) -> None:
    missing = {k: ("f" * 40 if k.startswith("basesha") else v) for k, v in sandbox.items()}
    sources = ChangeSources(github=RecordedGitHub(missing), git=LocalRepo(Path(sandbox["path"])))
    with pytest.raises(CollectionError) as caught:
        collect_bundle(_request(1), FixtureJiraSource(JIRA), EventLog("t"), sources)
    assert caught.value.exit_code == 3
    assert "git fetch" in str(caught.value)


def test_missing_recording_is_a_collection_error(sandbox: dict[str, str]) -> None:
    with pytest.raises(CollectionError):
        collect_bundle(_request(99), FixtureJiraSource(JIRA), EventLog("t"), _sources(sandbox))


def test_change_file_mode_still_notes_the_missing_stages(tmp_path: Path) -> None:
    from codeatlas.config import ChangeFile
    from codeatlas.pipeline import NOTE_NOT_COLLECTED

    policy, _ = load_policy(POLICY)
    change = ChangeFile(
        repo=REPO,
        pr_number=7,
        base_sha="a" * 40,
        head_sha="b" * 40,
        merge_base_sha="c" * 40,
        work_items=[],
        commits=[],
    )
    request = CollectRequest(
        change=change,
        change_label="c.json",
        policy=policy,
        identity_map=IdentityMap(people=[]),
        run_id="r",
        collected_at=AT,
    )
    bundle = collect_bundle(request, FixtureJiraSource(JIRA), EventLog("t"))
    assert NOTE_NOT_COLLECTED in bundle.collection_notes
    assert bundle.changed == [] and bundle.reviews == []


@pytest.mark.parametrize(
    "args",
    [
        ["collect"],  # neither a change file nor a PR
        ["collect", "--change", "x.json", "--repo", REPO, "--pr", "1"],  # both
        ["collect", "--repo", REPO],  # --pr missing
        ["collect", "--repo", REPO, "--pr", "1"],  # --repo-path missing
    ],
)
def test_cli_rejects_incomplete_targets(args: list[str]) -> None:
    result = CliRunner().invoke(app, [*args, "--fixtures", str(JIRA)])
    assert result.exit_code == 4, result.output


def test_split_by_entity_separates_whole_additions() -> None:
    def entity(name: str, start: int, end: int) -> CodeEntity:
        return CodeEntity(
            entity_id=name,
            kind="function",
            file="f.py",
            start_line=start,
            end_line=end,
            signature=None,
            docstring=None,
        )

    two = [entity("a", 3, 5), entity("b", 8, 12)]
    assert _split_by_entity((3, 12), two) == [(3, 5), (8, 12)]
    assert _split_by_entity((4, 4), two) == [(4, 4)]  # inside one function: unchanged
    assert _split_by_entity((20, 22), two) == [(20, 22)]  # no entity: unchanged


def test_local_repo_lists_files_and_zero_context_hunks(sandbox: dict[str, str]) -> None:
    git = LocalRepo(Path(sandbox["path"]))
    base = sandbox["basesha0000000000000000000000000000000001"]
    head = sandbox["headsha0000000000000000000000000000000001"]
    assert git.list_files(base, "docs/adr") == ["docs/adr/0001-fees.md"]
    assert git.list_files(base, "missing") == []
    assert "payments/fees.py" in git.list_files(base)
    (hunk,) = git.diff_hunks(base, head, context_lines=0)
    assert hunk.old_lines == 0 and hunk.new_lines == ROUND_FEE.count("\n")


def test_collect_evaluates_the_stored_bundle_not_the_in_memory_one(
    sandbox: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saving sorts lists and trims time and scores; replay reads the saved file, so collect must
    judge that same file, or a stored verdict can differ from its own replay."""
    seen: list[EvidenceBundle] = []
    real = pipeline.evaluate_bundle

    def spy(bundle: EvidenceBundle, bundle_hash: str) -> Verdict:
        seen.append(bundle)
        return real(bundle, bundle_hash)

    monkeypatch.setattr(pipeline, "evaluate_bundle", spy)
    store = BundleStore(tmp_path / "store")
    bundle_hash, _ = pipeline.collect_and_evaluate(
        _request(1), FixtureJiraSource(JIRA), store, EventLog("t"), _sources(sandbox)
    )
    assert seen == [load_bundle(store.folder(bundle_hash))]
