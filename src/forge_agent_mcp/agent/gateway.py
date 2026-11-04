"""Gateway seam for the agent.

``GatewayClient`` is the protocol the graph and console depend on. Production
uses :class:`FastMCPGatewayClient` over Streamable HTTP with a scoped bearer
token; tests use the in-memory fake. The decision call takes the separate
``freight:approve`` token minted by the console, so the long-lived agent client
never needs the approve scope.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from ..corpus.vocab import Decision, Recommendation
from ..gateway.models import (
    AuditView,
    CaseView,
    DecisionRecord,
    DocumentView,
    DuplicateView,
    ExtractedField,
    ExtractionRecord,
    FlagRecord,
    PolicyPack,
    RecommendationRecord,
)


class GatewayToolError(Exception):
    """A business refusal returned by a gateway tool."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class GatewayClient(Protocol):
    """The typed MCP operations the agent and console use."""

    def read_case(self, case_id: str) -> CaseView: ...

    def get_document(self, case_id: str, document_id: str) -> DocumentView: ...

    def get_policy(self, case_id: str) -> PolicyPack: ...

    def check_duplicate(
        self,
        case_id: str,
        invoice_number: str | None = None,
        document_hash: str | None = None,
    ) -> DuplicateView: ...

    def record_extraction(
        self,
        case_id: str,
        document_id: str,
        fields: list[ExtractedField],
    ) -> ExtractionRecord: ...

    def record_recommendation(
        self,
        case_id: str,
        recommendation: Recommendation,
        rationale: str,
        conditions: list[str],
        flags: list[FlagRecord],
    ) -> RecommendationRecord: ...

    def create_decision(
        self,
        case_id: str,
        decision: Decision,
        rationale: str,
        conditions: list[str],
        approval_token: str,
        idempotency_key: str,
        approval_jwt: str,
    ) -> DecisionRecord: ...

    def get_audit(self, case_id: str, limit: int = 200) -> AuditView: ...


class FastMCPGatewayClient:
    """Synchronous facade over the async FastMCP Streamable HTTP client."""

    def __init__(self, url: str, token: str) -> None:
        if not token.strip():
            raise ValueError("a gateway bearer token is required")
        self.url = url
        self.token = token

    def _call(self, name: str, arguments: dict[str, Any], *, token: str | None = None) -> Any:
        return asyncio.run(
            self._call_async(name, arguments, token=token or self.token),
        )

    async def _call_async(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        token: str,
    ) -> Any:
        transport = StreamableHttpTransport(self.url, auth=token)
        async with Client(transport) as client:
            result = await client.call_tool_mcp(name, arguments)
        if result.is_error:
            content = result.structured_content or {}
            code = content.get("error_code") or "tool_error"
            message = content.get("message") or _text_content(result) or "Gateway tool failed."
            raise GatewayToolError(str(code), str(message))
        content = result.structured_content
        if not isinstance(content, dict) or content.get("ok") is not True:
            raise GatewayToolError(
                "invalid_tool_result",
                f"Tool {name!r} did not return a successful result envelope.",
            )
        return content.get("data")

    # -- reads -----------------------------------------------------------

    def read_case(self, case_id: str) -> CaseView:
        return CaseView.model_validate(self._call("read_case", {"case_id": case_id}))

    def get_document(self, case_id: str, document_id: str) -> DocumentView:
        return DocumentView.model_validate(
            self._call("get_document", {"case_id": case_id, "document_id": document_id})
        )

    def get_policy(self, case_id: str) -> PolicyPack:
        return PolicyPack.model_validate(self._call("get_policy", {"case_id": case_id}))

    def check_duplicate(
        self,
        case_id: str,
        invoice_number: str | None = None,
        document_hash: str | None = None,
    ) -> DuplicateView:
        arguments: dict[str, Any] = {"case_id": case_id}
        if invoice_number is not None:
            arguments["invoice_number"] = invoice_number
        if document_hash is not None:
            arguments["document_hash"] = document_hash
        return DuplicateView.model_validate(self._call("check_duplicate", arguments))

    # -- case-state writes ----------------------------------------------

    def record_extraction(
        self,
        case_id: str,
        document_id: str,
        fields: list[ExtractedField],
    ) -> ExtractionRecord:
        return ExtractionRecord.model_validate(
            self._call(
                "record_extraction",
                {
                    "case_id": case_id,
                    "document_id": document_id,
                    "fields": [field.model_dump(mode="json") for field in fields],
                },
            )
        )

    def record_recommendation(
        self,
        case_id: str,
        recommendation: Recommendation,
        rationale: str,
        conditions: list[str],
        flags: list[FlagRecord],
    ) -> RecommendationRecord:
        return RecommendationRecord.model_validate(
            self._call(
                "record_recommendation",
                {
                    "case_id": case_id,
                    "recommendation": recommendation,
                    "rationale": rationale,
                    "conditions": conditions,
                    "flags": [flag.model_dump(mode="json") for flag in flags],
                },
            )
        )

    def create_decision(
        self,
        case_id: str,
        decision: Decision,
        rationale: str,
        conditions: list[str],
        approval_token: str,
        idempotency_key: str,
        approval_jwt: str,
    ) -> DecisionRecord:
        return DecisionRecord.model_validate(
            self._call(
                "create_decision",
                {
                    "case_id": case_id,
                    "decision": decision,
                    "rationale": rationale,
                    "conditions": conditions,
                    "approval_token": approval_token,
                    "idempotency_key": idempotency_key,
                },
                token=approval_jwt,
            )
        )

    def get_audit(self, case_id: str, limit: int = 200) -> AuditView:
        return AuditView.model_validate(
            self._call("get_audit", {"case_id": case_id, "limit": limit})
        )


def _text_content(result: Any) -> str:
    content = getattr(result, "content", None) or []
    parts: list[str] = []
    for item in content:
        text = getattr(item, "text", None)
        if text:
            parts.append(str(text))
    return " ".join(parts)


__all__ = ["FastMCPGatewayClient", "GatewayClient", "GatewayToolError"]
