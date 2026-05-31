"""Bearer-token auth as a pure ASGI middleware wrapping the MCP streamable-HTTP app."""
import hmac

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class BearerAuthMiddleware:
    """Reject HTTP requests lacking a valid `Authorization: Bearer <token>` header.

    Runs before the MCP protocol layer, so it guards both the streamable-HTTP
    POST and the SSE GET sub-requests. Fails closed: if no token is configured,
    every request is rejected.
    """

    def __init__(self, app: ASGIApp, token: str):
        self.app = app
        self.token = token
        self._expected = f"Bearer {token}"

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        # Only guard HTTP; pass lifespan/websocket through untouched.
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        headers = dict(scope.get("headers") or [])
        provided = headers.get(b"authorization", b"").decode()

        # Constant-time compare; reject if no token configured.
        if not self.token or not hmac.compare_digest(provided, self._expected):
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
            return await response(scope, receive, send)

        return await self.app(scope, receive, send)
