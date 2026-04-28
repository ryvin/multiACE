"""Bearer-token middleware. Optional — only enforces if a token is configured.

NOTE: BaseHTTPMiddleware only runs on HTTP requests, not WebSocket frames.
WebSocket auth must be enforced separately inside the /ws handler (see server.py).
The /ws prefix here only protects the HTTP upgrade request itself; once a connection
is established, this middleware no longer participates.
"""
from __future__ import annotations

import hmac
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


PROTECTED_PREFIXES = ("/api/", "/ws/")
PROTECTED_EXACT = ("/api", "/ws")


class TokenAuth(BaseHTTPMiddleware):
    def __init__(self, app, token: Optional[str] = None) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self._token:
            return await call_next(request)
        path = request.url.path
        is_protected = (
            path in PROTECTED_EXACT
            or any(path.startswith(p) for p in PROTECTED_PREFIXES)
        )
        if not is_protected:
            return await call_next(request)
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        provided = auth_header[len("Bearer "):]
        if not hmac.compare_digest(provided, self._token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)
