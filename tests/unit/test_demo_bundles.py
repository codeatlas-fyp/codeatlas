"""The demo bundles the deployed site serves (deploy/bundles): real sandbox evaluations.

They are produced by `codeatlas collect`, never typed by hand. These tests make sure each one is
intact, replays to its stored verdict with the current rules, renders on every page, and carries
no credential, so a bad refresh is caught in CI before it reaches the live site.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codeatlas.api.app import create_app
from codeatlas.api.cases import CaseStore
from codeatlas.api.settings import WebSettings

DEMO = Path(__file__).parents[2] / "deploy" / "bundles"
HASHES = sorted(p.name for p in DEMO.iterdir() if p.is_dir()) if DEMO.is_dir() else []

# GitHub tokens (classic, fine-grained, app) and Atlassian API tokens.
TOKEN_RE = re.compile(rb"(ghp_|github_pat_|ghs_|gho_|ATATT)[A-Za-z0-9_\-]{10,}")


def test_there_are_demo_bundles() -> None:
    assert HASHES, "deploy/bundles is empty: the live site would have no cases"
    assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in HASHES)


@pytest.mark.parametrize("bundle_hash", HASHES)
def test_demo_bundle_replays_to_its_stored_verdict(bundle_hash: str) -> None:
    outcome = CaseStore(DEMO).replay(bundle_hash)
    assert not outcome.refused, outcome.message
    assert outcome.match, f"{bundle_hash[:12]}: {outcome.message} (rerun collect after rule edits)"


@pytest.mark.parametrize("bundle_hash", HASHES)
def test_demo_bundle_renders_on_every_page(bundle_hash: str) -> None:
    client = TestClient(create_app(WebSettings(bundle_dir=DEMO)))
    keys = client.get(f"/api/cases/{bundle_hash}").json()["summary"]["keys"]
    pages = [
        "/",
        f"/cases/{bundle_hash}",
        f"/cases/{bundle_hash}/links",
        f"/cases/{bundle_hash}/replay",
        *(f"/cases/{bundle_hash}/requirements/{key}" for key in keys),
    ]
    for page in pages:
        response = client.get(page)
        assert response.status_code == 200, f"{page} gave {response.status_code}"


@pytest.mark.parametrize("bundle_hash", HASHES)
def test_demo_bundle_contains_no_token(bundle_hash: str) -> None:
    for file in (DEMO / bundle_hash).iterdir():
        assert not TOKEN_RE.search(file.read_bytes()), f"{file.name} looks like it holds a token"
