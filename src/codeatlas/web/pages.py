"""Server-rendered pages (spec §6 `web`): case list, case detail, requirement timeline,
trace links and replay. Every route is a GET; JavaScript only shows, hides or highlights what
the server already rendered.
"""

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from codeatlas import __version__
from codeatlas.api.cases import CaseError, CaseStore, CaseSummary
from codeatlas.api.routes import Store
from codeatlas.web.views import (
    CASE_NAMES,
    LINK_LABELS,
    VERDICT_RULES,
    case_detail,
    fmt_time,
    links_view,
    replay_view,
    requirement_view,
    short,
)

WEB_DIR = Path(__file__).parent
TEMPLATE_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

# autoescape=True on every template: PR titles, Jira text and commit messages are untrusted.
_env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True, undefined=StrictUndefined
)
_env.globals.update(
    version=__version__,
    case_names=CASE_NAMES,
    link_labels=LINK_LABELS,
    verdict_rules=VERDICT_RULES,
    fmt_time=fmt_time,
    short=short,
)
templates = Jinja2Templates(env=_env)

router = APIRouter(include_in_schema=False)

VERDICT_FILTERS = ("ALL", "FAIL", "REVIEW", "UNKNOWN", "PASS")


SIDEBAR_RECENT = 6


def _page(request: Request, name: str, status_code: int = 200, **context: Any) -> HTMLResponse:
    """Render a page; every page also gets the sidebar's case list."""
    store: CaseStore = request.app.state.store
    nav_cases = store.list_cases()
    context.setdefault("case", None)
    context.setdefault("tab", "")
    context.setdefault("key", "")
    context.update(nav_cases=nav_cases[:SIDEBAR_RECENT], nav_total=len(nav_cases))
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def render_error(request: Request, error: CaseError) -> HTMLResponse:
    return _page(request, "error.html", status_code=error.status, error=error)


def _matches(case: CaseSummary, verdict: str, q: str) -> bool:
    if verdict != "ALL" and case.verdict != verdict:
        return False
    if not q:
        return True
    text = " ".join(
        [
            case.bundle_hash,
            *case.keys,
            f"pr #{case.pr_number}",
            case.title or "",
            case.author_login or "",
            case.repo or "",
        ]
    ).lower()
    return q.lower() in text


@router.get("/", response_class=HTMLResponse)
def case_list(
    request: Request,
    store: Store,
    verdict: Annotated[str, Query(max_length=10)] = "ALL",
    q: Annotated[str, Query(max_length=100)] = "",
    order: Annotated[str, Query(max_length=10)] = "newest",
) -> HTMLResponse:
    verdict = verdict.upper() if verdict.upper() in VERDICT_FILTERS else "ALL"
    cases = store.list_cases()
    counts = {f: sum(1 for c in cases if f == "ALL" or c.verdict == f) for f in VERDICT_FILTERS}
    shown = [c for c in cases if _matches(c, verdict, q.strip())]
    if order == "oldest":
        shown.reverse()
    repos = sorted({c.repo for c in cases if c.repo})
    return _page(
        request,
        "cases.html",
        cases=shown,
        total=len(cases),
        counts=counts,
        filters=VERDICT_FILTERS,
        verdict=verdict,
        q=q,
        order="oldest" if order == "oldest" else "newest",
        repo_label=", ".join(r.split("/")[-1] for r in repos) or "no repository yet",
    )


@router.get("/cases/{bundle_hash}", response_class=HTMLResponse)
def case_page(request: Request, bundle_hash: str, store: Store) -> HTMLResponse:
    case = store.get(bundle_hash)
    return _page(
        request,
        "case_detail.html",
        case=case,
        view=case_detail(case),
        replay=store.replay(bundle_hash),
        tab="overview",
    )


@router.get("/cases/{bundle_hash}/requirements/{key}", response_class=HTMLResponse)
def requirement_page(request: Request, bundle_hash: str, key: str, store: Store) -> HTMLResponse:
    case = store.requirement(bundle_hash, key)
    return _page(
        request,
        "requirement.html",
        case=case,
        view=requirement_view(case, key),
        replay=store.replay(bundle_hash),
        tab="timeline",
        key=key,
    )


@router.get("/cases/{bundle_hash}/links", response_class=HTMLResponse)
def links_page(request: Request, bundle_hash: str, store: Store) -> HTMLResponse:
    case, links = store.links(bundle_hash)
    return _page(
        request,
        "links.html",
        case=case,
        view=links_view(case, links),
        replay=store.replay(bundle_hash),
        tab="links",
    )


@router.get("/cases/{bundle_hash}/replay", response_class=HTMLResponse)
def replay_page(request: Request, bundle_hash: str, store: Store) -> HTMLResponse:
    outcome = store.replay(bundle_hash)
    case = None
    if not outcome.refused:
        case = store.get(bundle_hash)
    return _page(
        request,
        "replay.html",
        case=case,
        view=replay_view(outcome),
        replay=outcome,
        tab="replay",
        bundle_hash=bundle_hash,
    )
