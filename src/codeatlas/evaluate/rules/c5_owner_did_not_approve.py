"""C5 — code owner did not approve the head commit (spec §3 case C5, step-10).

Trigger: for each file in the diff, no CODEOWNERS owner has an APPROVED
review whose `commit_id` equals `bundle.change.head_sha`. A stale approval
(APPROVED on an earlier commit, then more commits pushed) does NOT count.

Severity: block (configurable via policy.cases["C5"].severity). This is the
default behaviour — the spec section 3 lists C5 as block.

Edge cases (spec §13 "edge cases"):
    - No CODEOWNERS file at base         → NOT_APPLICABLE
    - CODEOWNERS has no rule for a path  → NOT_APPLICABLE *for that path only*
    - Owner is only the PR author        → INSUFFICIENT_EVIDENCE (handled upstream
                                           by identity map; here, if the owner
                                           login matches an authored commit's
                                           author_login, we return INSUFFICIENT)
    - Owner team can't be expanded       → INSUFFICIENT_EVIDENCE (team expansion
                                           is Saleha's identity map; a bare
                                           team mention we don't recognise
                                           drops to INSUFFICIENT)

Pure. Reads only the bundle.
"""

from __future__ import annotations

from codeatlas.analyze.governance import owners_for
from codeatlas.schema import CaseId, CheckResult, EvidenceBundle, Policy, TraceLink

CASE: CaseId = "C5"
VERSION = "1"
REQUIRED_EVIDENCE = ("governance", "reviews", "changed", "commits", "change")


class C5OwnerDidNotApprove:
    case: CaseId = CASE
    version: str = VERSION
    required_evidence: tuple[str, ...] = REQUIRED_EVIDENCE

    def check(
        self, bundle: EvidenceBundle, links: list[TraceLink], policy: Policy
    ) -> CheckResult:
        severity = _severity_for(policy)

        # No CODEOWNERS at base → NOT_APPLICABLE
        if bundle.governance.codeowners_path is None or not bundle.governance.owner_rules:
            return CheckResult(
                case=CASE,
                outcome="NOT_APPLICABLE",
                severity="none",
                required=False,
                message="No CODEOWNERS file at the base commit.",
                evidence_ids=[bundle.governance.evidence_id],
            )

        head_sha = bundle.change.head_sha
        approvers_at_head = {
            review.reviewer_login
            for review in bundle.reviews
            if review.state == "APPROVED" and review.commit_id == head_sha
        }

        pr_author_logins = {
            commit.author_login for commit in bundle.commits if commit.author_login
        }

        changed_paths = sorted({ce.file for ce in bundle.changed})
        missing: list[tuple[str, list[str]]] = []
        insufficient_reasons: list[str] = []

        for path in changed_paths:
            owners = owners_for(path, bundle.governance.owner_rules)
            if not owners:
                continue  # NOT_APPLICABLE for this path; keep checking the rest

            # Author-is-sole-owner → INSUFFICIENT (can't self-approve)
            owner_logins = {o.lstrip("@") for o in owners if o.startswith("@") and "/" not in o}
            team_owners = [o for o in owners if o.startswith("@") and "/" in o]
            if team_owners and not any(o in bundle.identity_map.people for o in team_owners):
                insufficient_reasons.append(
                    f"{path}: owner team {team_owners[0]} not resolved in identity map"
                )
                continue
            if owner_logins and owner_logins <= pr_author_logins:
                insufficient_reasons.append(
                    f"{path}: only owner(s) {sorted(owner_logins)} authored the PR"
                )
                continue

            # Finally, the real check.
            if not (owner_logins & approvers_at_head):
                missing.append((path, sorted(owner_logins)))

        if missing:
            paths_part = ", ".join(f"{p} (needs one of {owners})" for p, owners in missing[:3])
            evidence_ids = [
                bundle.governance.evidence_id,
                *[r.evidence_id for r in bundle.reviews],
            ]
            return CheckResult(
                case=CASE,
                outcome="VIOLATED",
                severity=severity,
                required=False,
                message=(
                    f"Code owner did not approve head {head_sha[:12]} for "
                    f"{len(missing)} path(s): {paths_part}"
                    + (f" (+{len(missing) - 3} more)" if len(missing) > 3 else "")
                ),
                evidence_ids=evidence_ids,
            )

        if insufficient_reasons:
            return CheckResult(
                case=CASE,
                outcome="INSUFFICIENT_EVIDENCE",
                severity="none",
                required=False,
                message="; ".join(insufficient_reasons[:3]),
                evidence_ids=[bundle.governance.evidence_id],
            )

        return CheckResult(
            case=CASE,
            outcome="SATISFIED",
            severity="none",
            required=False,
            message=f"All owned paths have an owner APPROVED review at head {head_sha[:12]}.",
            evidence_ids=sorted({r.evidence_id for r in bundle.reviews if r.state == "APPROVED"}),
        )


def _severity_for(policy: Policy) -> str:
    case_policy = policy.cases.get("C5")
    if case_policy and case_policy.severity:
        return case_policy.severity
    return "block"
