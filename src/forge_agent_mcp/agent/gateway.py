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
from pydantic import Field

from ..corpus.vocab import Decision, Recommendation, StrictModel
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

    def __init__(self, code: str, message: str, *, data: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class CaseAlreadyDecidedError(GatewayToolError):
    """The case already has a committed decision; carry it back for reconciliation."""

    def __init__(self, decision: DecisionRecord) -> None:
        super().__init__(
            "case_already_decided",
            f"Case {decision.case_id!r} already has decision {decision.decision_id}.",
            data=decision,
        )
        self.decision = decision


class DecisionCommand(StrictModel):
    """One transient create_decision call, including the short-lived tokens."""

    case_id: str = Field(min_length=1)
    decision: Decision
    rationale: str = Field(min_length=1)
    conditions: list[str] = Field(default_factory=list)
    approval_token: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    approval_jwt: str = Field(min_length=1)


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

    def create_decision(self, command: DecisionCommand) -> DecisionRecord: ...

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
            code = str(content.get("error_code") or "tool_error")
            message = str(content.get("message") or _text_content(result) or "Gateway tool failed.")
            data = content.get("data")
            if code == "case_already_decided" and isinstance(data, dict):
                raise CaseAlreadyDecidedError(DecisionRecord.model_validate(data))
            raise GatewayToolError(code, message, data=data)
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

    def create_decision(self, command: DecisionCommand) -> DecisionRecord:
        return DecisionRecord.model_validate(
            self._call(
                "create_decision",
                {
                    "case_id": command.case_id,
                    "decision": command.decision,
                    "rationale": command.rationale,
                    "conditions": command.conditions,
                    "approval_token": command.approval_token,
                    "idempotency_key": command.idempotency_key,
                },
                token=command.approval_jwt,
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


__all__ = [
    "CaseAlreadyDecidedError",
    "DecisionCommand",
    "FastMCPGatewayClient",
    "GatewayClient",
    "GatewayToolError",
]
