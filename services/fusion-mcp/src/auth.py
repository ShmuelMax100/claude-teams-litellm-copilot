"""Service-to-service bearer gate; deliberately not end-user delegation."""
import hmac
from starlette.responses import JSONResponse


class BearerGate:
    def __init__(self, app, config):
        self.app = app
        self.config = config

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in ("/health/live", "/health/ready"):
            return await self.app(scope, receive, send)
        headers = scope.get("headers", [])
        # Browser clients must go through the authenticated LiteLLM gateway.
        if any(k.lower() == b"origin" for k, _ in headers):
            return await JSONResponse({"error": "browser_access_denied"}, status_code=403)(scope, receive, send)
        auth = [v for k, v in headers if k.lower() == b"authorization"]
        expected = ("Bearer " + self.config.mcp_token).encode()
        if (self.config.auth_mode != "single_user" or len(auth) != 1 or
                not hmac.compare_digest(auth[0], expected)):
            return await JSONResponse({"error": "unauthorized"}, status_code=401,
                headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
        await self.app(scope, receive, send)
