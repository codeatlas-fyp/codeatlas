"""The deployed service: read-only JSON API and server-rendered pages over stored bundles."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import RequestResponseEndpoint

from codeatlas import __version__
from codeatlas.api import catalog as catalog_api
from codeatlas.api.auth import BasicAuthMiddleware
from codeatlas.api.cases import CaseError, CaseStore, ErrorCode
from codeatlas.api.connections import router as connections_router
from codeatlas.api.evaluations import router as evaluations_router
from codeatlas.api.routes import router as api_router
from codeatlas.api.settings import WebSettings
from codeatlas.db import make_engine, make_session_factory
from codeatlas.jobs.service import JobService
from codeatlas.web.pages import STATIC_DIR, render_error
from codeatlas.web.pages import router as page_router

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; "
        "frame-ancestors 'none'; form-action 'self'"
    ),
}


def create_app(settings: WebSettings | None = None) -> FastAPI:
    settings = settings or WebSettings()
    settings.validate_runtime()
    application = FastAPI(title="CodeAtlas", version=__version__)
    application.state.store = CaseStore(settings.bundle_dir)
    application.state.settings = settings
    application.state.engine = make_engine(settings.database_url)
    application.state.session_factory = make_session_factory(application.state.engine)
    application.state.jobs = JobService(
        application.state.session_factory, lease_seconds=settings.job_lease_seconds
    )

    @application.get("/health")
    def health() -> dict[str, str]:
        # `commit` is the git SHA the image was built from (Dockerfile ARG GIT_SHA), so the
        # CD smoke test can prove the live service runs the build it just deployed.
        return {"status": "ok", "version": __version__, "commit": settings.commit}

    @application.exception_handler(CaseError)
    async def case_error(request: Request, error: Exception) -> Response:
        if not isinstance(error, CaseError):
            raise error
        safe_error = error
        if error.code in {"bundle_corrupt", "internal_error"}:
            safe_error = CaseError(
                error.code,
                "Stored evaluation data failed validation."
                if error.code == "bundle_corrupt"
                else "The request could not be completed.",
                status=error.status,
            )
        if request.url.path.startswith("/api/"):
            return JSONResponse(safe_error.body(), status_code=safe_error.status)
        return render_error(request, safe_error)

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: Exception) -> Response:
        """Unknown paths and methods get the same error shape (API) or error page (HTML)."""
        if not isinstance(error, StarletteHTTPException):
            raise error
        if (
            request.url.path.startswith("/api/")
            and not request.url.path.startswith("/api/cases")
            and error.status_code < 500
        ):
            return JSONResponse(
                {"error": {"code": "request_rejected", "message": error.detail, "details": {}}},
                status_code=error.status_code,
            )
        code: ErrorCode = "case_not_found" if error.status_code == 404 else "internal_error"
        return await case_error(
            request, CaseError(code, str(error.detail), status=error.status_code)
        )

    @application.middleware("http")
    async def security_headers(request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.update(_SECURITY_HEADERS)
        return response

    if settings.web_user and settings.web_password:
        application.add_middleware(
            BasicAuthMiddleware,
            user=settings.web_user,
            password=settings.web_password.get_secret_value(),
        )

    application.include_router(api_router)
    application.include_router(connections_router)
    application.include_router(catalog_api.router)
    application.include_router(evaluations_router)
    application.include_router(page_router)
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return application


app = create_app()
