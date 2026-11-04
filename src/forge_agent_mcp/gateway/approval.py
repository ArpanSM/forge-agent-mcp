"""Console-side approval tokens for the freight gateway.

The reviewer console holds a key the agent never sees and mints a short-lived
HMAC-SHA256 token bound to one decision payload. The gateway verifies the
signature, issuer, audience, expiry, tenant, case, decision and payload hash
before it writes anything. A ``jti`` makes a committed token single-use; the
store enforces that atomically in the decision transaction.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import jwt
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..corpus.vocab import Decision
from .canonical import canonical_hash

CONSOLE_ISSUER = "forge-console"
APPROVAL_ALGORITHM = "HS256"
MALFORMED_CODE = "approval_malformed"
EXPIRED_CODE = "approval_expired"
MISMATCH_CODE = "approval_mismatch"
REUSED_CODE = "approval_reused"
REQUIRED_CODE = "approval_required"


class ApprovalTokenError(Exception):
    """A rejected approval token with the stable code the tool envelope returns."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ApprovalClaims(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iss: str
    aud: str
    sub: str
    tid: str
    case_id: str
    decision: Decision
    payload_hash: str
    exp: int
    iat: int
    jti: str = Field(min_length=1)

    @field_validator("sub")
    @classmethod
    def _require_approver(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("approver must not be blank")
        return value


def decision_payload(
    *, case_id: str, decision: Decision, rationale: str, conditions: Sequence[str]
) -> dict[str, Any]:
    """Return the canonical business payload an approval and retry are bound to."""

    return {
        "case_id": case_id,
        "decision": decision,
        "rationale": rationale,
        "conditions": list(conditions),
    }


def decision_payload_hash(
    *, case_id: str, decision: Decision, rationale: str, conditions: Sequence[str]
) -> str:
    payload = decision_payload(
        case_id=case_id,
        decision=decision,
        rationale=rationale,
        conditions=conditions,
    )
    return canonical_hash(payload)


@dataclass(frozen=True)
class ApprovalTokenIssuer:
    """Mint approval tokens; this lives with the console, never with the agent."""

    secret: str = field(repr=False)
    audience: str
    issuer: str = CONSOLE_ISSUER
    default_ttl_seconds: int = 900

    def issue(
        self,
        *,
        approver: str,
        tenant_id: str,
        case_id: str,
        decision: Decision,
        payload_hash: str,
        ttl_seconds: int | None = None,
        now: int | None = None,
        jti: str | None = None,
    ) -> str:
        if not approver.strip():
            raise ValueError("approver must not be blank")
        issued_at = int(time.time()) if now is None else now
        ttl = self.default_ttl_seconds if ttl_seconds is None else ttl_seconds
        payload: dict[str, Any] = {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": approver,
            "tid": tenant_id,
            "case_id": case_id,
            "decision": decision,
            "payload_hash": payload_hash,
            "iat": issued_at,
            "exp": issued_at + ttl,
            "jti": jti or f"jti_{uuid.uuid4().hex}",
        }
        return jwt.encode(payload, self.secret, algorithm=APPROVAL_ALGORITHM)

    def issue_for_decision(
        self,
        *,
        approver: str,
        tenant_id: str,
        case_id: str,
        decision: Decision,
        rationale: str,
        conditions: Sequence[str],
        ttl_seconds: int | None = None,
        now: int | None = None,
        jti: str | None = None,
    ) -> str:
        payload_hash = decision_payload_hash(
            case_id=case_id,
            decision=decision,
            rationale=rationale,
            conditions=conditions,
        )
        return self.issue(
            approver=approver,
            tenant_id=tenant_id,
            case_id=case_id,
            decision=decision,
            payload_hash=payload_hash,
            ttl_seconds=ttl_seconds,
            now=now,
            jti=jti,
        )


def verify_approval_token(
    token: str,
    *,
    secret: str,
    audience: str,
    tenant_id: str,
    case_id: str,
    decision: Decision,
    payload_hash: str,
    issuer: str = CONSOLE_ISSUER,
) -> ApprovalClaims:
    """Verify one approval token and return its claims or raise ``ApprovalTokenError``."""

    if not token.strip():
        raise ApprovalTokenError(MALFORMED_CODE, "An approval token is required.")

    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[APPROVAL_ALGORITHM],
            audience=audience,
            issuer=issuer,
            options={
                "require": [
                    "iss",
                    "aud",
                    "sub",
                    "tid",
                    "case_id",
                    "decision",
                    "payload_hash",
                    "iat",
                    "exp",
                    "jti",
                ]
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise ApprovalTokenError(EXPIRED_CODE, "The approval token has expired.") from exc
    except jwt.PyJWTError as exc:
        raise ApprovalTokenError(MALFORMED_CODE, "The approval token is not valid.") from exc

    try:
        claims = ApprovalClaims.model_validate(payload)
    except ValueError as exc:
        raise ApprovalTokenError(
            MALFORMED_CODE, "The approval token is missing required claims."
        ) from exc

    mismatch = (
        claims.tid != tenant_id
        or claims.case_id != case_id
        or claims.decision != decision
        or claims.payload_hash != payload_hash
    )
    if mismatch:
        raise ApprovalTokenError(
            MISMATCH_CODE,
            "The approval token is not bound to this tenant, case, decision and payload.",
        )
    return claims
