"""Read-only access to stored bundles, shared by the JSON API and the web pages (spec §7.6).

Nothing here writes, collects or calls Jira, GitHub or a model: every answer comes from
`<bundle_dir>/<bundle_hash>/{bundle,verdict}.json` written by the CLI or CI.
"""

import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from codeatlas.bundle.hashing import verdict_hash
from codeatlas.bundle.replay import replay
from codeatlas.bundle.store import BUNDLE_FILE, VERDICT_FILE, load_bundle, load_verdict
from codeatlas.errors import CodeAtlasError, ReplayMismatchError
from codeatlas.evaluate.links import bundle_links
from codeatlas.evaluate.rules import default_rules, ruleset_version
from codeatlas.evaluate.verdict import decide, run_rules
from codeatlas.schema import CheckResult, EvidenceBundle, TraceLink, Verdict, VerdictValue

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

ErrorCode = Literal["case_not_found", "requirement_not_in_case", "bundle_corrupt", "internal_error"]
_STATUS: dict[ErrorCode, int] = {
    "case_not_found": 404,
    "requirement_not_in_case": 404,
    "bundle_corrupt": 422,
    "internal_error": 500,
}


class CaseError(Exception):
    """An API error with the single error shape of spec §7.6."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: dict[str, str] | None = None,
        status: int | None = None,
    ):
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message
        self.details = details or {}
        self.status = status or _STATUS[code]

    def body(self) -> dict[str, object]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class Findings(_Out):
    """Check results counted the way the case list shows them."""

    PASS: int = 0
    FAIL: int = 0
    REVIEW: int = 0
    UNKNOWN: int = 0


class CaseSummary(_Out):
    bundle_hash: str
    repo: str | None
    pr_number: int | None
    keys: list[str]
    verdict: VerdictValue | None  # None when the bundle is corrupt
    evaluated_at: datetime | None  # the bundle's collected_at
    title: str | None
    author_login: str | None
    findings: Findings
    corrupt: bool
    error: str | None


class Case(_Out):
    summary: CaseSummary
    bundle: EvidenceBundle
    verdict: Verdict


class ReplayOutcome(_Out):
    bundle_hash: str
    stored_verdict_hash: str | None
    recomputed_verdict_hash: str | None
    match: bool
    refused: bool  # True when the bundle or verdict failed its integrity check
    message: str


def check_hash(bundle_hash: str) -> None:
    """Only 64 lowercase hex characters can name a case, so a path can never escape the store."""
    if not _HASH_RE.match(bundle_hash):
        raise CaseError("case_not_found", f"no case {bundle_hash!r}")


def evaluate_stored(bundle: EvidenceBundle, bundle_hash: str) -> Verdict:
    """The replay evaluator: the same steps as `pipeline.evaluate_bundle`.

    `api` cannot import `pipeline`, because `pipeline` imports `collect` (contract K2). A unit
    test checks both give identical verdicts on every fixture bundle.
    """
    rules = default_rules()
    results = run_rules(rules, bundle, bundle_links(bundle))
    value = decide(results)
    version = ruleset_version(rules)
    return Verdict(
        value=value,
        results=results,
        ruleset_version=version,
        bundle_hash=bundle_hash,
        verdict_hash=verdict_hash(bundle_hash, version, results, value),
    )


def findings(results: list[CheckResult]) -> Findings:
    counts: Counter[str] = Counter()
    for result in results:
        if result.outcome == "SATISFIED":
            counts["PASS"] += 1
        elif result.outcome == "VIOLATED":
            counts["FAIL" if result.severity == "block" else "REVIEW"] += 1
        elif result.outcome == "INSUFFICIENT_EVIDENCE" and result.required:
            counts["UNKNOWN"] += 1
    return Findings(**counts)


def _title(bundle: EvidenceBundle) -> str | None:
    """Summary of the newest version of the first requirement key."""
    keys = sorted({item.key for item in bundle.work_items})
    for key in keys:
        versions = sorted((v for v in bundle.versions if v.key == key), key=lambda v: v.version_no)
        if versions and versions[-1].summary:
            return versions[-1].summary
    return None


def _author(bundle: EvidenceBundle) -> str | None:
    """The most frequent commit author (the schema holds no PR author)."""
    names = Counter(c.author_login or c.author_email for c in bundle.commits)
    if not names:
        return None
    return sorted(names.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _summary(bundle_hash: str, bundle: EvidenceBundle, verdict: Verdict) -> CaseSummary:
    return CaseSummary(
        bundle_hash=bundle_hash,
        repo=bundle.change.repo,
        pr_number=bundle.change.pr_number,
        keys=sorted({item.key for item in bundle.work_items}),
        verdict=verdict.value,
        evaluated_at=bundle.collected_at,
        title=_title(bundle),
        author_login=_author(bundle),
        findings=findings(verdict.results),
        corrupt=False,
        error=None,
    )


def _corrupt(bundle_hash: str, error: str) -> CaseSummary:
    return CaseSummary(
        bundle_hash=bundle_hash,
        repo=None,
        pr_number=None,
        keys=[],
        verdict=None,
        evaluated_at=None,
        title=None,
        author_login=None,
        findings=Findings(),
        corrupt=True,
        error=error,
    )


class CaseStore:
    """Read-only view of a bundle store folder."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _folder(self, bundle_hash: str) -> Path:
        check_hash(bundle_hash)
        folder = self.root / bundle_hash
        if not (folder / BUNDLE_FILE).is_file() or not (folder / VERDICT_FILE).is_file():
            raise CaseError("case_not_found", f"no case {bundle_hash}")
        return folder

    def list_cases(self) -> list[CaseSummary]:
        """Every stored case, newest first; corrupt bundles are listed, never hidden."""
        if not self.root.is_dir():
            return []
        found: list[CaseSummary] = []
        for folder in self.root.iterdir():
            if not _HASH_RE.match(folder.name) or not (folder / BUNDLE_FILE).is_file():
                continue
            try:
                found.append(self.get(folder.name).summary)
            except CaseError as error:
                found.append(_corrupt(folder.name, error.message))
        good = sorted(
            (c for c in found if not c.corrupt),
            key=lambda c: (c.evaluated_at, c.bundle_hash),
            reverse=True,
        )
        return good + sorted((c for c in found if c.corrupt), key=lambda c: c.bundle_hash)

    def get(self, bundle_hash: str) -> Case:
        folder = self._folder(bundle_hash)
        try:
            bundle = load_bundle(folder)
            verdict = load_verdict(folder)
        except ReplayMismatchError as error:
            raise CaseError("bundle_corrupt", str(error), {"bundle_hash": bundle_hash}) from error
        if verdict.bundle_hash != bundle_hash:
            raise CaseError(
                "bundle_corrupt",
                "verdict.json belongs to another bundle",
                {"bundle_hash": bundle_hash},
            )
        return Case(summary=_summary(bundle_hash, bundle, verdict), bundle=bundle, verdict=verdict)

    def requirement(self, bundle_hash: str, key: str) -> Case:
        case = self.get(bundle_hash)
        if key not in case.summary.keys:
            raise CaseError(
                "requirement_not_in_case",
                f"{key} is not linked to this case",
                {"bundle_hash": bundle_hash, "key": key},
            )
        return case

    def links(self, bundle_hash: str) -> tuple[Case, list[TraceLink]]:
        case = self.get(bundle_hash)
        return case, bundle_links(case.bundle)

    def replay(self, bundle_hash: str) -> ReplayOutcome:
        """Recompute offline; a tampered bundle is reported as refused, never raised."""
        folder = self._folder(bundle_hash)
        try:
            result = replay(folder, evaluate_stored, strict=False)
        except ReplayMismatchError as error:
            return ReplayOutcome(
                bundle_hash=bundle_hash,
                stored_verdict_hash=None,
                recomputed_verdict_hash=None,
                match=False,
                refused=True,
                message=str(error),
            )
        except CodeAtlasError as error:
            raise CaseError("internal_error", str(error)) from error
        return ReplayOutcome(
            bundle_hash=bundle_hash,
            stored_verdict_hash=result.stored_verdict_hash,
            recomputed_verdict_hash=result.recomputed_verdict_hash,
            match=result.match,
            refused=False,
            message="recomputed verdict is identical" if result.match else "verdict differs",
        )
