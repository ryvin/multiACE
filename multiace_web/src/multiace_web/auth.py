"""Bearer-token middleware. Optional — only enforces if a token is configured."""
from __future__ import annotations

from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


PROTECTED_PREFIXES = ("/api/", "/ws")


class TokenAuth(BaseHTTPMiddleware):
    def __init__(self, app, token: Optional[str] = None) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self._token:
            return await call_next(request)
        path = request.url.path
        if not any(path.startswith(p) for p in PROTECTED_PREFIXES):
            return await call_next(request)
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        provided = auth_header[len("Bearer "):]
        if provided != self._token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)
