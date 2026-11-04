"""Local issuer and verifier for the gateway's scoped agent tokens.

The gateway uses a deliberately small auth model: a local HS256 issuer mints
short-lived JWTs, and the MCP server verifies the exact issuer, audience and
global read scope with FastMCP's ``JWTVerifier``. This is a synthetic proof, not
an OAuth 2.1 authorization server. See ``docs/gateway.md`` for what is deferred.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import jwt
from fastmcp.server.auth.providers.jwt import JWTVerifier

READ_SCOPE = "freight:read"
WRITE_SCOPE = "freight:write"
APPROVE_SCOPE = "freight:approve"

TOOL_SCOPES: dict[str, str] = {
    "read_case": READ_SCOPE,
    "get_document": READ_SCOPE,
    "get_policy": READ_SCOPE,
    "check_duplicate": READ_SCOPE,
    "record_extraction": WRITE_SCOPE,
    "record_recommendation": WRITE_SCOPE,
    "create_decision": APPROVE_SCOPE,
    "get_audit": READ_SCOPE,
}


@dataclass(frozen=True)
class LocalTokenIssuer:
    """Mint HS256 bearer tokens for local demos and tests."""

    secret: str
    issuer: str
    audience: str
    default_ttl_seconds: int = 3600

    def issue(
        self,
        *,
        subject: str,
        tenant_id: str,
        scopes: Iterable[str],
        ttl_seconds: int | None = None,
        now: int | None = None,
    ) -> str:
        issued_at = int(time.time()) if now is None else now
        ttl = self.default_ttl_seconds if ttl_seconds is None else ttl_seconds
        payload: dict[str, Any] = {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": subject,
            "tid": tenant_id,
            "scope": " ".join(dict.fromkeys(scopes)),
            "iat": issued_at,
            "exp": issued_at + ttl,
        }
        return jwt.encode(payload, self.secret, algorithm="HS256")


def build_verifier(*, secret: str, issuer: str, audience: str) -> JWTVerifier:
    """Build the verifier with exact issuer/audience and the global read scope."""

    return JWTVerifier(
        public_key=secret,
        issuer=issuer,
        audience=audience,
        algorithm="HS256",
        required_scopes=[READ_SCOPE],
    )


def access_token_tenant(access_token: Any | None) -> str | None:
    """Return the tenant claim (``tid``), or ``None`` when unauthenticated."""

    if access_token is None:
        return None
    claims = getattr(access_token, "claims", None) or {}
    tenant = claims.get("tid") or claims.get("tenant_id")
    return str(tenant) if tenant else None


def access_token_principal(access_token: Any | None, *, default: str = "stdio-local") -> str:
    """Return the token subject, or the trusted local principal for stdio."""

    if access_token is None:
        return default
    return access_token.subject or "unknown"
