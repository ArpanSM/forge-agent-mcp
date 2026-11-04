"""FastMCP middleware that turns argument-validation failures into envelopes."""

from __future__ import annotations

import json
from typing import Any

from fastmcp.exceptions import ValidationError as FastMCPValidationError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools import ToolResult


class InvalidRequestEnvelopeMiddleware(Middleware):
    """Keep bad calls inside the gateway's result-envelope contract.

    FastMCP validates tool arguments before the handler runs and would otherwise
    return an ``isError`` result with ``structuredContent: null``. The advertised
    envelope is more useful to a client and still a business-level rejection.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: Any,
    ) -> ToolResult:
        try:
            return await call_next(context)
        except FastMCPValidationError as exc:
            envelope = {
                "ok": False,
                "data": None,
                "error_code": "invalid_request",
                "message": str(exc),
            }
            return ToolResult(
                content=json.dumps(envelope),
                structured_content=envelope,
                is_error=True,
            )
