"""Integration test: our collectors use codeatlas.errors.CollectionError correctly.

Catches signature drift if Areej changes the error class. Mypy might miss
runtime keyword mismatches on raised exceptions.
"""

import pytest

from codeatlas.errors import CollectionError


def test_collection_error_accepts_our_keyword_shape() -> None:
    """Collectors use these exact CollectionError keyword arguments."""
    error = CollectionError(
        source="github",
        status=503,
        retryable=True,
        detail="GET /repos/x/y failed",
    )
    assert error.source == "github"
    assert error.status == 503
    assert error.retryable is True
    assert "GET /repos/x/y failed" in str(error)


def test_collection_error_allows_none_status_for_network_errors() -> None:
    """Timeouts and DNS failures raise with status=None."""
    error = CollectionError(
        source="github",
        status=None,
        retryable=False,
        detail="CODEATLAS_GITHUB_TOKEN not set",
    )
    assert error.status is None
    assert error.retryable is False


def test_collection_error_rejects_wrong_keyword() -> None:
    """Passing message= instead of detail= is rejected."""
    with pytest.raises(TypeError):
        CollectionError(
            source="github",
            status=500,
            retryable=True,
            message="old keyword name",  # type: ignore[call-arg]
        )
