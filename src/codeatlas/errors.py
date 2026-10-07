"""Error types and their exit codes (spec §5, §7.8, §15; step-3 spec "Errors").

§7.8 and §15 name the same errors differently; the §7.8 names are the classes and the §15 names
are aliases. Missing evidence is data, not an exception: it becomes UNKNOWN or UNRESOLVED.
"""


class CodeAtlasError(Exception):
    """Base for every CodeAtlas error. `exit_code` is what the CLI exits with."""

    exit_code: int = 4


class CollectionError(CodeAtlasError):
    """Jira or GitHub failed. Raised by collectors; nothing above `collect/` sees httpx errors."""

    exit_code = 3

    def __init__(
        self, source: str, *, status: int | None, retryable: bool, detail: str = ""
    ) -> None:
        self.source = source
        self.status = status
        self.retryable = retryable
        message = f"{source} request failed"
        if status is not None:
            message += f" with HTTP {status}"
        if detail:
            message += f": {detail}"
        super().__init__(message)


class SourceUnavailable(CollectionError):
    """The source could not be reached at all (network error, timeout, retries exhausted)."""


class ConfigError(CodeAtlasError):
    """Invalid policy, identity map or settings."""

    exit_code = 4


class EvidenceValidationError(CodeAtlasError):
    """Evidence fails schema validation, or a store operation would leave it inconsistent."""

    exit_code = 4


class ReplayMismatchError(CodeAtlasError):
    """A stored bundle or verdict does not match what its bytes recompute to (tampering)."""

    exit_code = 5


EvidenceInvalid = EvidenceValidationError
ReplayMismatch = ReplayMismatchError
