"""Local re-export of CollectionError so this package can stand alone.

When this branch merges into develop with Areej's Phase 1 (`codeatlas.errors`
already lives there — see `feature/bundle-replay` src/codeatlas/errors.py),
this file is deleted and the import points at `codeatlas.errors` instead.

[INTEGRATION NOTE] Deleting this file is part of the merge PR, not this branch.
"""

from __future__ import annotations

try:
    # If Areej's errors module is already present, use it: that is the real class.
    from codeatlas.errors import CollectionError
except ImportError:
    # Stand-alone fallback; same shape as spec §7.8.
    class CollectionError(Exception):
        """Jira or GitHub failure. Converts to exit code 3 at CLI boundary."""

        def __init__(
            self, *, source: str, status: int | None, retryable: bool, message: str
        ) -> None:
            super().__init__(message)
            self.source = source
            self.status = status
            self.retryable = retryable


__all__ = ["CollectionError"]
