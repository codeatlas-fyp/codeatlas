"""HTTP basic auth for the deployed demo (spec §13): every route except /health needs it."""

import base64
import binascii
import secrets

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

OPEN_PATHS = frozenset({"/health"})


class BasicAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, user: str, password: str) -> None:
        super().__init__(app)
        self._user = user.encode()
        self._password = password.encode()

    def _allowed(self, header: str | None) -> bool:
        if not header or not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:], validate=True)
        except (binascii.Error, ValueError):
            return False
        user, sep, password = decoded.partition(b":")
        if not sep:
            return False
        # compare_digest on both parts, always, so timing reveals nothing.
        user_ok = secrets.compare_digest(user, self._user)
        password_ok = secrets.compare_digest(password, self._password)
        return user_ok and password_ok

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in OPEN_PATHS or self._allowed(request.headers.get("authorization")):
            return await call_next(request)
        return Response(
            "Authentication required",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="CodeAtlas"'},
        )
