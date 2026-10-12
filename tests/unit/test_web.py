"""Web pages and read-only API over the bundle store (spec §7.6, §13; AC15, AC16).

Every page renders every fixture bundle; tampering is shown, never hidden; untrusted text is
escaped; nothing writes; basic auth guards everything but /health.
"""

import base64
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from codeatlas.api.app import create_app
from codeatlas.api.cases import CaseStore, evaluate_stored, findings
from codeatlas.api.settings import WebSettings
from codeatlas.bundle.store import BundleStore, load_bundle, load_verdict
from codeatlas.pipeline import evaluate_bundle
from codeatlas.schema import CheckResult, EvidenceBundle
from codeatlas.web.views import hash_groups
from tests.samples import BUNDLE

FIXTURES = Path(__file__).parents[1] / "fixtures" / "bundles"
FIXTURE_HASHES = sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())


def _client(root: Path, **settings: object) -> TestClient:
    return TestClient(create_app(WebSettings(bundle_dir=root, **settings)))


def _store_bundle(root: Path, bundle: EvidenceBundle) -> str:
    store = BundleStore(root)
    hash_ = store.save(bundle)
    store.save_verdict(evaluate_bundle(bundle, hash_))
    return hash_


@pytest.fixture
def client() -> TestClient:
    return _client(FIXTURES)


@pytest.fixture
def tampered(tmp_path: Path) -> tuple[Path, str]:
    """A store holding one good copy of the sample and one copy with an edited byte."""
    source = FIXTURES / FIXTURE_HASHES[0]
    shutil.copytree(source, tmp_path / source.name)
    bad = tmp_path / ("f" * 64)
    shutil.copytree(source, bad)
    data = (bad / "bundle.json").read_bytes().replace(b'"run_id":"', b'"run_id":"x', 1)
    (bad / "bundle.json").write_bytes(data)
    return tmp_path, bad.name


# --- every page renders every scenario ------------------------------------------------------


@pytest.mark.parametrize("bundle_hash", FIXTURE_HASHES)
@pytest.mark.parametrize(
    "path",
    [
        "/cases/{h}",
        "/cases/{h}/links",
        "/cases/{h}/replay",
        "/api/cases/{h}",
        "/api/cases/{h}/links",
        "/api/cases/{h}/replay",
    ],
)
def test_every_page_renders_every_fixture(client: TestClient, bundle_hash: str, path: str) -> None:
    assert client.get(path.format(h=bundle_hash)).status_code == 200


@pytest.mark.parametrize("bundle_hash", FIXTURE_HASHES)
def test_every_requirement_page_renders(client: TestClient, bundle_hash: str) -> None:
    keys = client.get(f"/api/cases/{bundle_hash}").json()["summary"]["keys"]
    assert keys
    for key in keys:
        assert client.get(f"/cases/{bundle_hash}/requirements/{key}").status_code == 200
        assert client.get(f"/api/cases/{bundle_hash}/requirements/{key}").status_code == 200


def test_case_list_shows_verdict_findings_and_links(client: TestClient) -> None:
    html = client.get("/").text
    for bundle_hash in FIXTURE_HASHES:
        assert f'href="/cases/{bundle_hash}"' in html
        assert bundle_hash[:8] in html
    assert 'class="stamp stamp--' in html


def test_case_list_filters_and_search(client: TestClient) -> None:
    case = client.get("/api/cases").json()[0]
    verdict, key = case["verdict"], case["keys"][0]
    other = next(v for v in ("PASS", "FAIL", "REVIEW", "UNKNOWN") if v != verdict)
    row = f'class="row__case" href="/cases/{case["bundle_hash"]}"'  # a table row, not the sidebar
    assert row in client.get(f"/?verdict={verdict}").text
    assert row not in client.get(f"/?verdict={other}").text
    assert row in client.get(f"/?q={key}").text
    assert "No cases match" in client.get("/?q=nothing-matches-this").text
    assert client.get("/?verdict=<x>").status_code == 200  # unknown filter falls back to ALL


def test_api_list_has_spec_fields(client: TestClient) -> None:
    cases = client.get("/api/cases").json()
    assert len(cases) == len(FIXTURE_HASHES)
    for case in cases:
        for name in ("bundle_hash", "repo", "pr_number", "keys", "verdict", "evaluated_at"):
            assert name in case
        verdict = load_verdict(FIXTURES / case["bundle_hash"])
        assert case["findings"] == findings(verdict.results).model_dump()


@pytest.mark.parametrize("bundle_hash", FIXTURE_HASHES)
def test_case_page_shows_all_nine_cases_and_their_evidence(
    client: TestClient, bundle_hash: str
) -> None:
    html = client.get(f"/cases/{bundle_hash}").text
    verdict = load_verdict(FIXTURES / bundle_hash)
    for n in range(1, 10):
        assert f">C{n}<" in html  # every case appears, run or not
    if len(verdict.results) < 9:
        assert "Not run" in html  # a case with no rule yet says so, never looks passed
    for result in verdict.results:
        for evidence_id in result.evidence_ids:
            assert evidence_id in html


@pytest.mark.parametrize("bundle_hash", FIXTURE_HASHES)
def test_sidebar_links_to_all_five_screens(client: TestClient, bundle_hash: str) -> None:
    keys = client.get(f"/api/cases/{bundle_hash}").json()["summary"]["keys"]
    html = client.get(f"/cases/{bundle_hash}/links").text
    sidebar = html.split('<aside class="side"', 1)[1].split("</aside>", 1)[0]
    for href in (
        "/",
        f"/cases/{bundle_hash}",
        f"/cases/{bundle_hash}/links",
        f"/cases/{bundle_hash}/replay",
        *(f"/cases/{bundle_hash}/requirements/{k}" for k in keys),
    ):
        assert f'href="{href}"' in sidebar
    assert 'aria-current="page"' in sidebar  # the open screen is marked


def test_empty_store_renders(tmp_path: Path) -> None:
    client = _client(tmp_path / "missing")
    assert "No cases yet" in client.get("/").text
    assert client.get("/api/cases").json() == []


# --- errors use one shape ------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["nothex", "..%2F..%2Fetc%2Fpasswd", "A" * 64, "a" * 63])
def test_bad_hash_is_case_not_found(client: TestClient, bad: str) -> None:
    response = client.get(f"/api/cases/{bad}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "case_not_found"


def test_unknown_case_html_page_is_404(client: TestClient) -> None:
    response = client.get("/cases/" + "a" * 64)
    assert response.status_code == 404
    assert "No such case" in response.text


def test_requirement_not_in_case(client: TestClient) -> None:
    response = client.get(f"/api/cases/{FIXTURE_HASHES[0]}/requirements/SBX-999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "requirement_not_in_case"


# --- replay and tampering -------------------------------------------------------------------


@pytest.mark.parametrize("bundle_hash", FIXTURE_HASHES)
def test_replay_matches_on_every_fixture(client: TestClient, bundle_hash: str) -> None:
    body = client.get(f"/api/cases/{bundle_hash}/replay").json()
    assert body["match"] is True
    assert body["stored_verdict_hash"] == body["recomputed_verdict_hash"]


@pytest.mark.parametrize("bundle_hash", FIXTURE_HASHES)
def test_api_evaluator_equals_pipeline_evaluator(bundle_hash: str) -> None:
    """`api` cannot import `pipeline` (K2), so it repeats evaluate_bundle; they must agree."""
    bundle = load_bundle(FIXTURES / bundle_hash)
    assert evaluate_stored(bundle, bundle_hash) == evaluate_bundle(bundle, bundle_hash)


def test_tampered_bundle_is_listed_and_refused(tampered: tuple[Path, str]) -> None:
    root, bad = tampered
    client = _client(root)
    cases = client.get("/api/cases").json()
    assert [c["corrupt"] for c in cases] == [False, True]
    assert "CORRUPT" in client.get("/").text

    detail = client.get(f"/api/cases/{bad}")
    assert detail.status_code == 422
    assert detail.json()["error"]["code"] == "bundle_corrupt"

    replay = client.get(f"/api/cases/{bad}/replay").json()
    assert replay["refused"] is True and replay["match"] is False
    page = client.get(f"/cases/{bad}/replay")
    assert page.status_code == 200
    assert "REFUSED" in page.text


# --- security --------------------------------------------------------------------------------


def test_script_in_requirement_title_is_shown_as_text(tmp_path: Path) -> None:
    """AC16: untrusted text from Jira or GitHub is escaped, never run."""
    evil = "<script>alert(1)</script>"
    versions = [v.model_copy(update={"summary": evil}) for v in BUNDLE.versions]
    bundle_hash = _store_bundle(tmp_path, BUNDLE.model_copy(update={"versions": versions}))
    client = _client(tmp_path)
    for path in ("/", f"/cases/{bundle_hash}", f"/cases/{bundle_hash}/links"):
        html = client.get(path).text
        assert evil not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_only_http_links_become_hrefs(tmp_path: Path) -> None:
    commits = [c.model_copy(update={"source_ref": "javascript:alert(1)"}) for c in BUNDLE.commits]
    bundle_hash = _store_bundle(tmp_path, BUNDLE.model_copy(update={"commits": commits}))
    html = _client(tmp_path).get(f"/cases/{bundle_hash}").text
    assert 'href="javascript:' not in html


def test_app_has_no_write_routes(client: TestClient) -> None:
    """The service only reads: no POST, PUT, PATCH or DELETE route exists."""
    for route in client.app.routes:  # type: ignore[attr-defined]
        methods = getattr(route, "methods", None) or set()
        assert methods <= {"GET", "HEAD"}, route


def test_security_headers(client: TestClient) -> None:
    headers = client.get("/").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert "script-src 'self'" in headers["content-security-policy"]


def test_basic_auth_guards_everything_but_health() -> None:
    client = _client(FIXTURES, web_user="demo", web_password=SecretStr("s3cret"))
    assert client.get("/health").status_code == 200
    for path in ("/", "/api/cases", f"/cases/{FIXTURE_HASHES[0]}", "/static/app.css"):
        response = client.get(path)
        assert response.status_code == 401
        assert response.headers["www-authenticate"].startswith("Basic")
    wrong = base64.b64encode(b"demo:nope").decode()
    assert client.get("/", headers={"Authorization": f"Basic {wrong}"}).status_code == 401
    assert client.get("/", headers={"Authorization": "Basic !!!"}).status_code == 401
    good = base64.b64encode(b"demo:s3cret").decode()
    assert client.get("/", headers={"Authorization": f"Basic {good}"}).status_code == 200


# --- view helpers ----------------------------------------------------------------------------


def _result(outcome: str, severity: str = "review", required: bool = True) -> CheckResult:
    return CheckResult.model_validate(
        {
            "case": "C1",
            "outcome": outcome,
            "severity": severity,
            "required": required,
            "message": "",
            "evidence_ids": [],
        }
    )


def test_findings_counts_each_outcome() -> None:
    counted = findings(
        [
            _result("SATISFIED"),
            _result("VIOLATED", "block"),
            _result("VIOLATED", "review"),
            _result("INSUFFICIENT_EVIDENCE", required=True),
            _result("INSUFFICIENT_EVIDENCE", required=False),
            _result("NOT_APPLICABLE", "none", False),
        ]
    )
    assert counted.model_dump() == {"PASS": 1, "FAIL": 1, "REVIEW": 1, "UNKNOWN": 1}


def test_hash_groups_mark_differences() -> None:
    a, b = "a" * 64, "a" * 56 + "b" * 8
    groups = hash_groups(a, b)
    assert len(groups) == 8
    assert [g.match for g in groups] == [True] * 7 + [False]
    assert hash_groups(None, a) == []


def test_case_store_lists_newest_first(tmp_path: Path) -> None:
    older = BUNDLE
    newer = BUNDLE.model_copy(
        update={"run_id": "run-0002", "collected_at": BUNDLE.collected_at.replace(year=2027)}
    )
    _store_bundle(tmp_path, older)
    newer_hash = _store_bundle(tmp_path, newer)
    assert CaseStore(tmp_path).list_cases()[0].bundle_hash == newer_hash
