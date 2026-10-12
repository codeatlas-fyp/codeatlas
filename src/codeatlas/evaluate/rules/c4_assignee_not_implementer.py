"""C4: the requirement's assignee did not write the code (FR-6.4; old R7).

Who wrote the code comes from the commits; who should have comes from the Jira assignee while
the code was being written (the coding period: first to last commit). Git and GitHub name
people by email and login, Jira by account id, so the identity map frozen in the bundle joins
them. The map is reviewed by hand because Jira hides emails.

Per key:
- no assignee during the coding period: INSUFFICIENT_EVIDENCE (UNKNOWN);
- an assignee wrote at least one commit: SATISFIED;
- some commit author is not in the identity map: INSUFFICIENT_EVIDENCE (UNKNOWN), never
  VIOLATED, because the unmapped author might be the assignee (spec AC10);
- every author is mapped and none is an assignee: VIOLATED (REVIEW).

Pure: reads only the bundle.
"""

from datetime import datetime

from codeatlas.evaluate.rules.base import (
    KeyResult,
    combine,
    facts_for,
    no_work_item,
    severity,
    work_item_keys,
)
from codeatlas.schema import (
    CaseId,
    CheckResult,
    Commit,
    EvidenceBundle,
    IdentityMap,
    LifecycleFacts,
    Policy,
    TraceLink,
)


def resolve_author(commit: Commit, identity: IdentityMap) -> str | None:
    """The Jira account id of a commit's author, or None when the map does not know them.

    The git email is tried first (it is on every commit); the GitHub login second. Both
    comparisons ignore case, because GitHub logins and email domains are case-insensitive.
    """
    email = commit.author_email.strip().lower()
    login = (commit.author_login or "").strip().lower()
    for person in identity.people:
        if email and email in {e.strip().lower() for e in person.git_emails}:
            return person.jira_account_id
    for person in identity.people:
        if login and person.github_login and person.github_login.strip().lower() == login:
            return person.jira_account_id
    return None


def assignees_during(facts: LifecycleFacts, start: datetime, end: datetime) -> set[str]:
    """Every account assigned at any moment between `start` and `end` (inclusive).

    `assignee_history` is a list of (from_time, account_or_None) changes; each entry holds
    until the next one.
    """
    history = sorted(facts.assignee_history, key=lambda item: item[0])
    found: set[str] = set()
    for index, (since, account) in enumerate(history):
        until = history[index + 1][0] if index + 1 < len(history) else None
        overlaps = since <= end and (until is None or until > start)
        if overlaps and account:
            found.add(account)
    return found


class AssigneeNotImplementer:
    case: CaseId = "C4"
    version: str = "1"
    required_evidence: tuple[str, ...] = ("work_items", "commits", "lifecycle", "identity_map")

    def check(self, bundle: EvidenceBundle, links: list[TraceLink], policy: Policy) -> CheckResult:
        rule_severity = severity(policy, self.case, "review")
        keys = work_item_keys(bundle)
        if not keys:
            return no_work_item(self.case, rule_severity)
        if not bundle.commits:
            missing = KeyResult("INSUFFICIENT_EVIDENCE", "no commits, so no implementer", [])
            return combine(self.case, dict.fromkeys(keys, missing), rule_severity)

        ordered = sorted(bundle.commits, key=lambda c: (c.committed_at, c.sha))
        start, end = ordered[0].committed_at, ordered[-1].committed_at
        authors = {c.sha: resolve_author(c, bundle.identity_map) for c in ordered}
        mapped = {account for account in authors.values() if account}
        unmapped = sorted(
            {c.author_login or c.author_email for c in ordered if authors[c.sha] is None}
        )
        commit_ids = [c.evidence_id for c in ordered]

        results: dict[str, KeyResult] = {}
        for key in keys:
            facts = facts_for(bundle, key)
            if facts is None:
                results[key] = KeyResult("INSUFFICIENT_EVIDENCE", "issue not found", [])
                continue
            assignees = assignees_during(facts, start, end)
            if not assignees:
                results[key] = KeyResult(
                    "INSUFFICIENT_EVIDENCE", "no assignee while the code was written", []
                )
            elif assignees & mapped:
                who = ", ".join(sorted(assignees & mapped))
                results[key] = KeyResult(
                    "SATISFIED",
                    f"the assignee ({who}) wrote the code",
                    [c.evidence_id for c in ordered if authors[c.sha] in assignees],
                )
            elif unmapped:
                results[key] = KeyResult(
                    "INSUFFICIENT_EVIDENCE",
                    f"author(s) not in the identity map: {', '.join(unmapped)}",
                    commit_ids,
                )
            else:
                results[key] = KeyResult(
                    "VIOLATED",
                    f"assigned to {', '.join(sorted(assignees))}, "
                    f"but the code was written by {', '.join(sorted(mapped))}",
                    commit_ids,
                )
        return combine(self.case, results, rule_severity)
