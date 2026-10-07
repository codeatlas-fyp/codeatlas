"""Pipeline orchestration (spec §5; step-8 spec).

`collect_bundle` runs the online stages that exist in Phase 1 (change, policy, work items, Jira
requirements, lifecycle) and freezes them into an `EvidenceBundle`; `evaluate_bundle` runs the
offline stages (rules and verdict) and is also the evaluator injected into `bundle.replay`.
GitHub, git, governance, code and semantic stages are Yusra's (steps 9-13); until they exist the
change file stands in for the GitHub and git evidence.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from codeatlas import __version__
from codeatlas.analyze.lifecycle import JiraHistory, LifecycleContext, TextForms, reconstruct
from codeatlas.bundle.hashing import verdict_hash
from codeatlas.bundle.store import BundleStore
from codeatlas.collect.jira.adf import adf_to_text, wiki_to_text
from codeatlas.config import ChangeFile
from codeatlas.errors import CollectionError, EvidenceValidationError
from codeatlas.evaluate.rules import default_rules, ruleset_version
from codeatlas.evaluate.rules.base import NOTE_APPROVERS_UNKNOWN, Rule
from codeatlas.evaluate.verdict import decide, run_rules
from codeatlas.logging import EventLog
from codeatlas.schema import (
    ChangeEvent,
    ChangeRef,
    Commit,
    EvidenceBundle,
    GovernanceSnapshot,
    IdentityMap,
    LifecycleFacts,
    Policy,
    RequirementVersion,
    TraceLink,
    Verdict,
    WorkItemRef,
    WorkItemSource,
)

NOTE_NOT_COLLECTED = "governance, reviews, code and semantic evidence not collected (steps 9-13)"
TEXT_FORMS = TextForms(current=adf_to_text, history=wiki_to_text)


class JiraSource(WorkItemSource, Protocol):
    def fetch_status_categories(self, project_key: str) -> dict[str, str]: ...


@dataclass(frozen=True)
class CollectRequest:
    change: ChangeFile
    change_label: str  # where the change came from, e.g. the change file's name
    policy: Policy
    identity_map: IdentityMap
    run_id: str
    collected_at: datetime
    notes: list[str] = field(default_factory=list)


def evaluate_bundle(
    bundle: EvidenceBundle,
    bundle_hash: str,
    rules: Iterable[Rule] | None = None,
    links: list[TraceLink] | None = None,
) -> Verdict:
    """Run the rules over a frozen bundle and seal the verdict with its hash.

    A rule that raises is a bug: no verdict is produced and the error has exit code 4.
    """
    chosen = tuple(default_rules() if rules is None else rules)
    try:
        results = run_rules(chosen, bundle, links or [])
    except Exception as error:
        raise EvidenceValidationError(f"rule evaluation failed: {error}") from error
    value = decide(results)
    version = ruleset_version(chosen)
    return Verdict(
        value=value,
        results=results,
        ruleset_version=version,
        bundle_hash=bundle_hash,
        verdict_hash=verdict_hash(bundle_hash, version, results, value),
    )


def _members(
    source: JiraSource, group: str | None, ids: list[str], notes: list[str]
) -> frozenset[str] | None:
    """Account ids from the policy plus readable group members; None when nobody is known."""
    members = set(ids)
    group_readable = False
    if group:
        try:
            members |= source.group_members(group)
            group_readable = True
        except CollectionError as error:
            if error.status not in (403, 404):
                raise
            notes.append(f"group {group} not readable (HTTP {error.status})")
    return frozenset(members) if members or group_readable else None


def collect_bundle(request: CollectRequest, source: JiraSource, log: EventLog) -> EvidenceBundle:
    """Stages 1-10 that exist in Phase 1; raises `CollectionError` (exit 3) if Jira fails."""
    change, at = request.change, request.collected_at
    notes = [*request.notes, NOTE_NOT_COLLECTED]
    ref = f"change-file:{request.change_label}"
    with log.stage("resolve_change"):
        work_items = [
            WorkItemRef(
                evidence_id=f"github:pr:{change.pr_number}:{item.found_in}:{item.key}",
                source="github",
                source_ref=ref,
                retrieved_at=at,
                extractor_version=__version__,
                key=item.key,
                found_in=item.found_in,
                commit_sha=item.commit_sha,
            )
            for item in change.work_items
        ]
        commits = [
            Commit(
                evidence_id=f"git:commit:{c.sha}",
                source="git",
                source_ref=f"{ref}@{c.sha}",
                retrieved_at=at,
                extractor_version=__version__,
                sha=c.sha,
                author_email=c.author_email,
                author_login=c.author_login,
                committed_at=c.committed_at,
                message=c.message,
            )
            for c in change.commits
        ]

    events: list[ChangeEvent] = []
    versions: list[RequirementVersion] = []
    lifecycle: list[LifecycleFacts] = []
    keys = sorted({item.key for item in work_items})
    with log.stage("collect_requirements"):
        approval = request.policy.approval
        authority = request.policy.priority_authority
        approvers = _members(source, approval.approver_group, approval.approver_account_ids, notes)
        authorities = _members(source, authority.group, authority.account_ids, notes)
        categories: dict[str, dict[str, str]] = {}
        for key in keys:
            try:
                issue = source.fetch_issue(key)
            except CollectionError as error:
                if error.status != 404:
                    raise
                notes.append(f"work item {key} not found")
                continue
            project = key.split("-")[0]
            if project not in categories:
                categories[project] = source.fetch_status_categories(project)
            if approvers is None:
                notes.append(f"{NOTE_APPROVERS_UNKNOWN}{key}")
            history = JiraHistory(issue=issue, changelog=source.fetch_changelog(key))
            context = LifecycleContext(
                policy=request.policy,
                approvers=approvers or frozenset(),
                authorities=authorities,
                new_statuses=frozenset(
                    name for name, kind in categories[project].items() if kind == "new"
                ),
                retrieved_at=at,
                extractor_version=__version__,
            )
            with log.stage("lifecycle"):
                result = reconstruct(history, context, TEXT_FORMS)
            events += result.events
            versions += result.versions
            lifecycle.append(result.facts)

    with log.stage("freeze"):
        return EvidenceBundle(
            schema_version="0",
            change=ChangeRef(
                repo=change.repo,
                pr_number=change.pr_number,
                base_sha=change.base_sha,
                head_sha=change.head_sha,
                merge_base_sha=change.merge_base_sha,
            ),
            codeatlas_version=__version__,
            collected_at=at,
            run_id=request.run_id,
            extractor_versions={"jira": __version__, "lifecycle": __version__},
            model_ids=[],
            model_file_hashes={},
            policy=request.policy,
            identity_map=request.identity_map,
            work_items=work_items,
            change_events=events,
            versions=versions,
            lifecycle=lifecycle,
            commits=commits,
            reviews=[],
            people=[],
            governance=GovernanceSnapshot(
                evidence_id=f"git:governance:{change.base_sha}",
                source="git",
                source_ref=f"{ref}@{change.base_sha}",
                retrieved_at=at,
                extractor_version=__version__,
                base_sha=change.base_sha,
                codeowners_path=None,
                owner_rules=[],
                adrs=[],
                policy_blob_sha=None,
            ),
            governance_changes=[],
            entities=[],
            changed=[],
            edges=[],
            criteria=[],
            candidates=[],
            collection_notes=sorted(set(notes)),
        )


def collect_and_evaluate(
    request: CollectRequest, source: JiraSource, store: BundleStore, log: EventLog
) -> tuple[str, Verdict]:
    """Collect, freeze, store, evaluate. Nothing is stored unless collection completed."""
    bundle = collect_bundle(request, source, log)
    with log.stage("store"):
        bundle_hash = store.save(bundle)
    with log.stage("evaluate"):
        verdict = evaluate_bundle(bundle, bundle_hash)
    store.save_verdict(verdict)
    return bundle_hash, verdict


def now_utc() -> datetime:
    """Collection time, to the second (the only clock read in a run)."""
    return datetime.now(UTC).replace(microsecond=0)
