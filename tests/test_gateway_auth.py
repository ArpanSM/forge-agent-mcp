from __future__ import annotations

import asyncio

import pytest

from forge_agent_mcp.gateway.auth import (
    APPROVE_SCOPE,
    READ_SCOPE,
    WRITE_SCOPE,
    LocalTokenIssuer,
    access_token_principal,
    access_token_tenant,
    build_verifier,
)

ISSUER = "http://127.0.0.1:8765"
AUDIENCE = "forge-freight-gateway"
SECRET = "gateway-test-secret-at-least-32-bytes-long"


def _issuer(**kwargs) -> LocalTokenIssuer:
    values = {"secret": SECRET, "issuer": ISSUER, "audience": AUDIENCE}
    values.update(kwargs)
    return LocalTokenIssuer(**values)


def _verify(token: str):
    verifier = build_verifier(secret=SECRET, issuer=ISSUER, audience=AUDIENCE)
    return asyncio.run(verifier.verify_token(token))


def test_issuer_mints_a_token_with_identity_tenant_and_scopes() -> None:
    token = _issuer().issue(
        subject="agent-underwriter",
        tenant_id="tenant_northwind",
        scopes=[READ_SCOPE, WRITE_SCOPE],
    )

    access = _verify(token)

    assert access is not None
    assert access.subject == "agent-underwriter"
    assert access.claims["iss"] == ISSUER
    assert access.claims["aud"] == AUDIENCE
    assert access.claims["tid"] == "tenant_northwind"
    assert set(access.scopes) == {READ_SCOPE, WRITE_SCOPE}
    assert access_token_tenant(access) == "tenant_northwind"
    assert access_token_principal(access) == "agent-underwriter"


@pytest.mark.parametrize(
    ("issuer", "audience"),
    [
        ("https://wrong-issuer.example.com", AUDIENCE),
        (ISSUER, "wrong-audience"),
    ],
)
def test_verifier_rejects_an_exact_issuer_or_audience_mismatch(issuer: str, audience: str) -> None:
    token = _issuer().issue(
        subject="agent-underwriter",
        tenant_id="tenant_northwind",
        scopes=[READ_SCOPE],
    )

    wrong = build_verifier(secret=SECRET, issuer=issuer, audience=audience)

    assert asyncio.run(wrong.verify_token(token)) is None


def test_verifier_rejects_an_expired_token() -> None:
    token = _issuer().issue(
        subject="agent-underwriter",
        tenant_id="tenant_northwind",
        scopes=[READ_SCOPE],
        ttl_seconds=-5,
    )

    assert _verify(token) is None


def test_verifier_requires_the_global_read_scope() -> None:
    token = _issuer().issue(
        subject="agent-approver",
        tenant_id="tenant_northwind",
        scopes=[APPROVE_SCOPE],
    )

    assert _verify(token) is None


def test_verifier_rejects_a_token_signed_with_another_key() -> None:
    token = _issuer(secret="another-test-secret-at-least-32-bytes").issue(
        subject="agent-underwriter",
        tenant_id="tenant_northwind",
        scopes=[READ_SCOPE],
    )

    assert _verify(token) is None


def test_tenant_and_principal_helpers_are_explicit_for_unauthenticated_stdio() -> None:
    assert access_token_tenant(None) is None
    assert access_token_principal(None) == "stdio-local"
