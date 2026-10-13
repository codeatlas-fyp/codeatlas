"""C4: assignee differs from the observed implementer (FR-6.4; spec AC10).

The four required cases (Saleha's part, "C4 is correct"): the assignee wrote the code (PASS),
someone else did (REVIEW), an author is not in the identity map (UNKNOWN), and there is no
assignee (UNKNOWN). Plus the break-it check: an unmapped author never yields a FAIL or REVIEW.
"""

import hashlib
from datetime import timedelta

import pytest

from codeatlas.evaluate.rules import default_rules
from codeatlas.evaluate.rules.c4_assignee_not_implementer import (
    AssigneeNotImplementer,
    assignees_during,
    resolve_author,
)
from codeatlas.pipeline import evaluate_bundle
from codeatlas.schema import CheckResult, Commit, EvidenceBundle, IdentityEntry, IdentityMap
from tests.samples import BUNDLE, COMMIT, LIFECYCLE, POLICY

T1 = COMMIT.committed_at
RULE = AssigneeNotImplementer()

ALICE = IdentityEntry(
    jira_account_id="acc-alice", github_login="alice", git_emails=["alice@example.test"]
)
BOB = IdentityEntry(jira_account_id="acc-bob", github_login="bob", git_emails=["bob@example.test"])
MAP = IdentityMap(people=[ALICE, BOB])


def commit_by(login: str | None, email: str, minutes: int = 0) -> Commit:
    sha = hashlib.sha1(f"{login}|{email}|{minutes}".encode()).hexdigest()  # noqa: S324
    return COMMIT.model_copy(
        update={
            "evidence_id": f"git:commit:{sha}",
            "sha": sha,
            "author_login": login,
            "author_email": email,
            "committed_at": T1 + timedelta(minutes=minutes),
        }
    )


def bundle(
    commits: list[Commit],
    assignee_history: list[tuple[object, str | None]],
    identity: IdentityMap = MAP,
) -> EvidenceBundle:
    lifecycle = LIFECYCLE.model_copy(update={"assignee_history": assignee_history})
    return BUNDLE.model_copy(
        update={"commits": commits, "lifecycle": [lifecycle], "identity_map": identity}
    )


def judge(b: EvidenceBundle) -> CheckResult:
    return RULE.check(b, [], POLICY)


def before(minutes: int = 60) -> object:
    return T1 - timedelta(minutes=minutes)


# --- the four required cases ---------------------------------------------------------------


def test_assignee_wrote_the_code_passes() -> None:
    result = judge(bundle([commit_by("alice", "alice@example.test")], [(before(), "acc-alice")]))
    assert result.outcome == "SATISFIED"
    assert "acc-alice" in result.message


def test_someone_else_wrote_the_code_is_review() -> None:
    result = judge(bundle([commit_by("bob", "bob@example.test")], [(before(), "acc-alice")]))
    assert result.outcome == "VIOLATED"
    assert result.severity == "review"
    assert "acc-alice" in result.message and "acc-bob" in result.message
    assert result.evidence_ids


def test_author_not_in_identity_map_is_unknown() -> None:
    result = judge(bundle([commit_by("mallory", "m@example.test")], [(before(), "acc-alice")]))
    assert result.outcome == "INSUFFICIENT_EVIDENCE"
    assert result.required is True  # so the verdict becomes UNKNOWN, not PASS
    assert "mallory" in result.message


def test_no_assignee_is_unknown() -> None:
    result = judge(bundle([commit_by("alice", "alice@example.test")], [(before(), None)]))
    assert result.outcome == "INSUFFICIENT_EVIDENCE"
    assert "no assignee" in result.message


# --- break-it check and edge cases ----------------------------------------------------------


@pytest.mark.parametrize("mapped_author", ["bob", None])
def test_unmapped_author_never_fails_or_reviews(mapped_author: str | None) -> None:
    """AC10: an author missing from the map gives UNKNOWN, never a wrong FAIL/REVIEW."""
    commits = [commit_by("mallory", "m@example.test")]
    if mapped_author:
        commits.append(commit_by(mapped_author, f"{mapped_author}@example.test", 5))
    result = judge(bundle(commits, [(before(), "acc-alice")]))
    assert result.outcome == "INSUFFICIENT_EVIDENCE"


def test_assignee_among_several_authors_passes_even_with_an_unmapped_one() -> None:
    commits = [commit_by("mallory", "m@example.test"), commit_by("alice", "x@other.test", 5)]
    assert judge(bundle(commits, [(before(), "acc-alice")])).outcome == "SATISFIED"


def test_reassignment_during_coding_counts_every_assignee() -> None:
    commits = [commit_by("bob", "bob@example.test"), commit_by("bob", "bob@example.test", 30)]
    history = [(before(), "acc-alice"), (T1 + timedelta(minutes=10), "acc-bob")]
    assert judge(bundle(commits, history)).outcome == "SATISFIED"


def test_assignee_after_the_last_commit_does_not_count() -> None:
    commits = [commit_by("bob", "bob@example.test")]
    history = [(before(), "acc-alice"), (T1 + timedelta(minutes=10), "acc-bob")]
    assert judge(bundle(commits, history)).outcome == "VIOLATED"


def test_no_commits_and_no_work_item_are_unknown() -> None:
    assert judge(bundle([], [(before(), "acc-alice")])).outcome == "INSUFFICIENT_EVIDENCE"
    no_key = bundle([commit_by("alice", "a@x.test")], [(before(), "acc-alice")]).model_copy(
        update={"work_items": []}
    )
    assert judge(no_key).message.startswith("no work item")


def test_issue_not_found_is_unknown() -> None:
    b = bundle([commit_by("alice", "alice@example.test")], [(before(), "acc-alice")])
    assert judge(b.model_copy(update={"lifecycle": []})).outcome == "INSUFFICIENT_EVIDENCE"


def test_resolve_author_matches_email_then_login_ignoring_case() -> None:
    assert resolve_author(commit_by(None, "ALICE@Example.test"), MAP) == "acc-alice"
    assert resolve_author(commit_by("Bob", "unknown@x.test"), MAP) == "acc-bob"
    assert resolve_author(commit_by("eve", "eve@x.test"), MAP) is None


def test_assignees_during_uses_open_ended_last_entry() -> None:
    facts = LIFECYCLE.model_copy(update={"assignee_history": [(before(), "acc-alice")]})
    assert assignees_during(facts, T1, T1 + timedelta(days=30)) == {"acc-alice"}


def test_c4_is_a_default_rule_and_sample_bundle_passes_it() -> None:
    assert "C4" in {rule.case for rule in default_rules()}
    result = next(r for r in evaluate_bundle(BUNDLE, "0" * 64).results if r.case == "C4")
    assert result.outcome == "SATISFIED"  # user-02 is assigned and wrote the sample commit
