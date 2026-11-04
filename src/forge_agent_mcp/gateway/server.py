"""Gateway factory: one FastMCP server over Streamable HTTP and stdio."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.middleware import Middleware

from .approval import CONSOLE_ISSUER, DEFAULT_APPROVAL_SECRET
from .auth import TOOL_SCOPES, LocalTokenIssuer, build_verifier
from .gate import GatewayGate
from .middleware import InvalidRequestEnvelopeMiddleware
from .store import GatewayStore
from .tools import register_tools

STDIO_AUTH_WARNING = (
    "WARNING: stdio transport is a trusted local convenience and skips authentication "
    "and scopes. Use Streamable HTTP as the security boundary."
)


@dataclass
class Gateway:
    store: GatewayStore
    issuer: LocalTokenIssuer
    verifier: JWTVerifier
    mcp: FastMCP
    tool_scopes: dict[str, str]

    def _gate(self) -> Middleware:
        return Middleware(
            GatewayGate,
            store=self.store,
            verifier=self.verifier,
            tool_scopes=self.tool_scopes,
        )

    @property
    def app(self) -> Any:
        return self.http_app()

    def http_app(self, path: str = "/mcp", *, stateless_http: bool = True) -> Any:
        return self.mcp.http_app(
            path=path,
            stateless_http=stateless_http,
            middleware=[self._gate()],
        )

    def run(
        self,
        transport: Literal["stdio", "http", "streamable-http"] = "http",
        **transport_kwargs: Any,
    ) -> None:
        if transport == "stdio":
            print(STDIO_AUTH_WARNING, file=sys.stderr, flush=True)
            self.mcp.run(transport="stdio", show_banner=False, **transport_kwargs)
            return
        transport_kwargs.setdefault("path", "/mcp")
        transport_kwargs.setdefault("stateless_http", True)
        transport_kwargs.setdefault("show_banner", False)
        extra_middleware = transport_kwargs.pop("middleware", [])
        transport_kwargs["middleware"] = [self._gate(), *extra_middleware]
        self.mcp.run(transport="http", **transport_kwargs)


def create_gateway(
    store: GatewayStore,
    auth: LocalTokenIssuer,
    *,
    tool_scopes: dict[str, str] | None = None,
    approval_secret: str = DEFAULT_APPROVAL_SECRET,
    approval_audience: str | None = None,
    approval_issuer: str = CONSOLE_ISSUER,
) -> Gateway:
    """Build the gateway and register the eight tools in a stable order."""

    verifier = build_verifier(
        secret=auth.secret,
        issuer=auth.issuer,
        audience=auth.audience,
    )
    mcp = FastMCP(
        name="Forge Freight Gateway",
        version="0.1.0",
        instructions=(
            "Synthetic freight underwriting gateway. Reads require freight:read; case-state "
            "writes require freight:write; decisions require freight:approve."
        ),
        auth=verifier,
    )
    register_tools(
        mcp,
        store,
        approval_secret=approval_secret,
        approval_audience=approval_audience or auth.audience,
        approval_issuer=approval_issuer,
    )
    mcp.add_middleware(InvalidRequestEnvelopeMiddleware())
    return Gateway(
        store=store,
        issuer=auth,
        verifier=verifier,
        mcp=mcp,
        tool_scopes=dict(tool_scopes or TOOL_SCOPES),
    )
