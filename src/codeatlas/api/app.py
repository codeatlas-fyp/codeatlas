"""The deployed service: read-only JSON API and server-rendered pages over stored bundles."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import RequestResponseEndpoint

from codeatlas import __version__
from codeatlas.api.auth import BasicAuthMiddleware
from codeatlas.api.cases import CaseError, CaseStore, ErrorCode
from codeatlas.api.routes import router as api_router
from codeatlas.api.settings import WebSettings
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
    application = FastAPI(title="CodeAtlas", version=__version__)
    application.state.store = CaseStore(settings.bundle_dir)

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @application.exception_handler(CaseError)
    async def case_error(request: Request, error: Exception) -> Response:
        if not isinstance(error, CaseError):
            raise error
        if request.url.path.startswith("/api/"):
            return JSONResponse(error.body(), status_code=error.status)
        return render_error(request, error)

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: Exception) -> Response:
        """Unknown paths and methods get the same error shape (API) or error page (HTML)."""
        if not isinstance(error, StarletteHTTPException):
            raise error
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
    application.include_router(page_router)
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return application


app = create_app()
