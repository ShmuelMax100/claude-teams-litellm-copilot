"""Authenticated gateway delegation; both HTTP interfaces require a bound user."""
import base64
from contextvars import ContextVar
import hashlib
import hmac
import json
import re
import time
from starlette.responses import JSONResponse
from config import UserCredential, strict_object

current_credential: ContextVar[UserCredential | None] = ContextVar("fusion_credential", default=None)


def decode_part(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Invalid assertion encoding")
    decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if base64.urlsafe_b64encode(decoded).rstrip(b"=").decode() != value:
        raise ValueError("Noncanonical assertion")
    return decoded


def verify_identity(assertion, key, now=None):
    now = time.time() if now is None else now
    if not isinstance(assertion, str) or len(assertion) > 2048:
        raise ValueError("Invalid assertion")
    version, payload, signature = assertion.split(".")
    if version != "v1":
        raise ValueError("Invalid assertion version")
    expected = hmac.new(key.encode(), f"v1.{payload}".encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(decode_part(signature), expected):
        raise ValueError("Invalid assertion signature")
    claims = json.loads(decode_part(payload).decode("utf-8"), object_pairs_hook=strict_object)
    if (not isinstance(claims, dict) or set(claims) != {"v", "aud", "sub", "iat", "exp", "jti"}
            or type(claims["v"]) is not int or claims["v"] != 1 or claims["aud"] != "fusion-mcp"
            or not isinstance(claims["sub"], str) or not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,128}", claims["sub"])
            or type(claims["iat"]) is not int or type(claims["exp"]) is not int
            or not 0 < claims["exp"] - claims["iat"] <= 60 or claims["iat"] > now + 5
            or claims["exp"] <= now or not isinstance(claims["jti"], str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", claims["jti"])):
        raise ValueError("Invalid assertion claims")
    return claims["sub"]


class BearerGate:
    def __init__(self, app, config):
        self.app, self.config = app, config

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in ("/health/live", "/health/ready"):
            return await self.app(scope, receive, send)
        headers = scope.get("headers", [])
        async def reject(code, status=401):
            return await JSONResponse({"error": {"type": "authentication_error", "code": code,
                "message": "Fusion requires a valid delegated user credential."}}, status_code=status,
                headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"})(scope, receive, send)
        if any(k.lower() == b"origin" for k, _ in headers):
            return await reject("browser_access_denied", 403)
        auth = [v for k, v in headers if k.lower() == b"authorization"]
        identities = [v for k, v in headers if k.lower() == b"x-fusion-identity"]
        expected = ("Bearer " + self.config.service_token).encode()
        if len(auth) != 1 or not hmac.compare_digest(auth[0], expected) or len(identities) != 1:
            return await reject("unauthorized")
        try:
            subject = verify_identity(identities[0].decode("ascii"), self.config.identity_key)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return await reject("invalid_identity")
        credential = self.config.credentials.get(subject)
        if credential is None:
            return await reject("user_not_enrolled", 403)
        if credential.expires_at <= time.time():
            return await reject("credential_expired")
        reset = current_credential.set(credential)
        try:
            await self.app(scope, receive, send)
        finally:
            current_credential.reset(reset)
