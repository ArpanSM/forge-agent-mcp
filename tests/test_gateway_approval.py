from __future__ import annotations

import time

import jwt
import pytest

from forge_agent_mcp.gateway.approval import (
    CONSOLE_ISSUER,
    ApprovalTokenError,
    ApprovalTokenIssuer,
    decision_payload_hash,
    verify_approval_token,
)

SECRET = "console-approval-test-secret-at-least-32-bytes"
AUDIENCE = "forge-freight-gateway"
TENANT = "tenant_northwind"
CASE_ID = "case_onb_ridgeline"
APPROVER = "underwriter@example.test"


def _hash(*, decision: str = "approve", rationale: str = "Clean packet.") -> str:
    return decision_payload_hash(
        case_id=CASE_ID,
        decision=decision,
        rationale=rationale,
        conditions=[],
    )


def _issuer(*, secret: str = SECRET) -> ApprovalTokenIssuer:
    return ApprovalTokenIssuer(secret=secret, audience=AUDIENCE)


def _verify(token: str, **overrides):
    values = {
        "secret": SECRET,
        "audience": AUDIENCE,
        "tenant_id": TENANT,
        "case_id": CASE_ID,
        "decision": "approve",
        "payload_hash": _hash(),
    }
    values.update(overrides)
    return verify_approval_token(token, **values)


def test_decision_payload_hash_is_canonical_and_binds_the_payload() -> None:
    first = decision_payload_hash(
        case_id=CASE_ID,
        decision="approve",
        rationale="Clean packet.",
        conditions=["Collect notice of assignment."],
    )
    same = decision_payload_hash(
        case_id=CASE_ID,
        decision="approve",
        rationale="Clean packet.",
        conditions=["Collect notice of assignment."],
    )
    changed_conditions = decision_payload_hash(
        case_id=CASE_ID,
        decision="approve",
        rationale="Clean packet.",
        conditions=["Collect a different document."],
    )
    changed_decision = _hash(decision="decline")

    assert len(first) == 64
    assert first == same
    assert changed_conditions != first
    assert changed_decision != first


def test_valid_approval_token_round_trips_with_every_bound_claim() -> None:
    issued_at = int(time.time())
    token = _issuer().issue(
        approver=APPROVER,
        tenant_id=TENANT,
        case_id=CASE_ID,
        decision="approve",
        payload_hash=_hash(),
        ttl_seconds=600,
        now=issued_at,
        jti="jti-valid",
    )

    claims = _verify(token)

    assert claims.iss == CONSOLE_ISSUER
    assert claims.aud == AUDIENCE
    assert claims.sub == APPROVER
    assert claims.tid == TENANT
    assert claims.case_id == CASE_ID
    assert claims.decision == "approve"
    assert claims.payload_hash == _hash()
    assert claims.jti == "jti-valid"
    assert claims.exp == issued_at + 600


@pytest.mark.parametrize(
    ("token_factory", "error_code"),
    [
        (
            lambda: _issuer(secret="another-console-secret-at-least-32-bytes").issue(
                approver=APPROVER,
                tenant_id=TENANT,
                case_id=CASE_ID,
                decision="approve",
                payload_hash=_hash(),
            ),
            "approval_malformed",
        ),
        (
            lambda: jwt.encode(
                {
                    "iss": "not-the-console",
                    "aud": AUDIENCE,
                    "sub": APPROVER,
                    "tid": TENANT,
                    "case_id": CASE_ID,
                    "decision": "approve",
                    "payload_hash": _hash(),
                    "iat": int(time.time()),
                    "exp": int(time.time()) + 600,
                    "jti": "jti-wrong-issuer",
                },
                SECRET,
                algorithm="HS256",
            ),
            "approval_malformed",
        ),
        (
            lambda: jwt.encode(
                {
                    "iss": CONSOLE_ISSUER,
                    "aud": "another-gateway",
                    "sub": APPROVER,
                    "tid": TENANT,
                    "case_id": CASE_ID,
                    "decision": "approve",
                    "payload_hash": _hash(),
                    "iat": int(time.time()),
                    "exp": int(time.time()) + 600,
                    "jti": "jti-wrong-audience",
                },
                SECRET,
                algorithm="HS256",
            ),
            "approval_malformed",
        ),
        (
            lambda: jwt.encode(
                {
                    "iss": CONSOLE_ISSUER,
                    "aud": AUDIENCE,
                    "sub": APPROVER,
                    "tid": TENANT,
                    "case_id": CASE_ID,
                    "decision": "approve",
                    "payload_hash": _hash(),
                    "iat": int(time.time()),
                    "exp": int(time.time()) + 600,
                },
                SECRET,
                algorithm="HS256",
            ),
            "approval_malformed",
        ),
        (lambda: "not-a-token", "approval_malformed"),
        (
            lambda: _issuer().issue(
                approver=APPROVER,
                tenant_id=TENANT,
                case_id=CASE_ID,
                decision="approve",
                payload_hash=_hash(),
                ttl_seconds=-5,
            ),
            "approval_expired",
        ),
    ],
)
def test_malformed_foreign_and_expired_tokens_are_rejected(token_factory, error_code: str) -> None:
    with pytest.raises(ApprovalTokenError) as raised:
        _verify(token_factory())

    assert raised.value.code == error_code


@pytest.mark.parametrize(
    "overrides",
    [
        {"tenant_id": "tenant_other"},
        {"case_id": "case_inv_ridgeline"},
        {"decision": "decline"},
        {
            "payload_hash": decision_payload_hash(
                case_id=CASE_ID,
                decision="approve",
                rationale="Different rationale.",
                conditions=[],
            )
        },
    ],
)
def test_tokens_bound_to_another_tenant_case_decision_or_payload_are_rejected(
    overrides: dict[str, str],
) -> None:
    token = _issuer().issue(
        approver=APPROVER,
        tenant_id=TENANT,
        case_id=CASE_ID,
        decision="approve",
        payload_hash=_hash(),
    )

    with pytest.raises(ApprovalTokenError) as raised:
        _verify(token, **overrides)

    assert raised.value.code == "approval_mismatch"
