"""Transport-level authorization gate for the MCP HTTP boundary.

FastMCP 4.1.6 enforces per-tool scopes at the component layer: the tool is
hidden from ``tools/list`` and a call returns HTTP 200 with ``isError: true``.
The MCP authorization profile requires a spec-correct ``403`` with
``WWW-Authenticate: Bearer error="insufficient_scope"``. This ASGI middleware
adds that wire behaviour, and it enforces tenant binding before the handler
runs. stdio never passes through this gate; that transport is a trusted local
convenience and is documented as such.
"""

from __future__ import annotations

import json
from typing import Any

from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.responses import Response

from .auth import access_token_principal, access_token_tenant
from .store import GatewayStore


class GatewayGate:
    """Enforce per-tool scopes and the tenant claim on ``tools/call``."""

    def __init__(
        self,
        app: Any,
        *,
        store: GatewayStore,
        verifier: JWTVerifier,
        tool_scopes: dict[str, str],
    ) -> None:
        self.app = app
        self.store = store
        self.verifier = verifier
        self.tool_scopes = tool_scopes

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break

        payload = _json_object(body)
        if payload is not None and payload.get("method") == "tools/call":
            handled = await self._authorize(scope, payload)
            if handled:
                await handled(scope, receive, send)
                return

        sent = False

        async def replay() -> dict[str, Any]:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    async def _authorize(self, scope: dict[str, Any], payload: dict[str, Any]) -> Response | None:
        params = payload.get("params") or {}
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(tool_name, str):
            return None
        case_id = arguments.get("case_id") if isinstance(arguments, dict) else None
        if not isinstance(case_id, str) or not case_id:
            case_id = None

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_auth = headers.get(b"authorization", b"").decode()
        token = raw_auth[7:] if raw_auth.lower().startswith("bearer ") else ""
        access = await self.verifier.verify_token(token) if token else None

        case_tenant = self.store.case_tenant(case_id) if case_id else None

        if access is None:
            if token and case_id:
                self._deny(
                    tenant_id=case_tenant or "unknown",
                    case_id=case_id,
                    tool=tool_name,
                    principal="unknown",
                    reason="invalid_token",
                    detail={"required_scope": self.tool_scopes.get(tool_name)},
                )
            return None

        tenant = access_token_tenant(access)
        principal = access_token_principal(access)

        required_scope = self.tool_scopes.get(tool_name)
        if required_scope and required_scope not in access.scopes:
            self._deny(
                tenant_id=case_tenant or tenant or "unknown",
                case_id=case_id,
                tool=tool_name,
                principal=principal,
                reason="insufficient_scope",
                detail={
                    "required_scope": required_scope,
                    "caller_tenant": tenant,
                },
            )
            return _json_error(
                403,
                {
                    "error": "insufficient_scope",
                    "error_description": (f"Tool {tool_name!r} requires scope {required_scope!r}"),
                    "scope": required_scope,
                },
                challenge=(f'Bearer error="insufficient_scope", scope="{required_scope}"'),
            )

        if tenant is None or (case_tenant is not None and case_tenant != tenant):
            self._deny(
                tenant_id=case_tenant or tenant or "unknown",
                case_id=case_id,
                tool=tool_name,
                principal=principal,
                reason="tenant_mismatch",
                detail={
                    "caller_tenant": tenant,
                    "case_tenant": case_tenant,
                    "required_scope": required_scope,
                },
            )
            return _json_error(
                403,
                {
                    "error": "tenant_mismatch",
                    "error_description": ("The authenticated tenant does not own this case."),
                },
                challenge=(
                    'Bearer error="insufficient_scope", error_description="Tenant mismatch"'
                ),
            )
        return None

    def _deny(
        self,
        *,
        tenant_id: str,
        case_id: str | None,
        tool: str,
        principal: str,
        reason: str,
        detail: dict[str, Any],
    ) -> None:
        self.store.append_audit(
            tenant_id=tenant_id,
            case_id=case_id,
            tool=tool,
            event="auth_denied",
            outcome="denied",
            principal=principal,
            detail={"reason": reason, **detail},
        )


def _json_object(body: bytes) -> dict[str, Any] | None:
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _json_error(status_code: int, detail: dict[str, Any], *, challenge: str) -> Response:
    return Response(
        content=json.dumps(detail),
        status_code=status_code,
        media_type="application/json",
        headers={
            "WWW-Authenticate": challenge,
            "Cache-Control": "no-store",
        },
    )
