# ADR 0002: Gateway auth and approval

- **Status:** Accepted
- **Date:** 2026-11-05
- **Ticket:** [#2](https://github.com/ArpanSM/forge-agent-mcp/issues/2), spec [#1](https://github.com/ArpanSM/forge-agent-mcp/issues/1)

## Context

The agent must not approve its own recommendation, and the demo must show real scoped
authorisation without an identity provider or paid infrastructure.

## Decision

- A local issuer mints **HS256** agent JWTs with `iss`, `aud`, `sub`, `tid`, `scope` and a
  short expiry.
- The server verifies issuer and audience, requires `freight:read` globally, and requires
  `freight:write` or `freight:approve` per tool.
- Invalid or expired token → **401**. A valid token missing a tool scope → **403** with
  `WWW-Authenticate: Bearer error="insufficient_scope"`, enforced by a small
  transport-level gate because FastMCP's native per-tool denial is 200 + `isError`.
- The tenant claim is checked against the case on every call.
- The reviewer console mints a separate HMAC **approval token** bound to case, decision,
  payload hash, approver, expiry and a single-use `jti`. The agent never holds that key.
- HTTP is the security boundary. **stdio skips auth** as a trusted local convenience; the
  README and a startup warning say so.
- **No OAuth 2.1 authorization server, PRM, RFC 8707, PKCE, CIMD, JWKS rotation, TLS,
  revocation, real IdP or rate limiting in this slice.** They are documented as production
  work.

## Consequences

- Scopes and the 403/401 distinction are directly demonstrable and testable.
- The custom gate and approval-token code are part of the artifact, not polish.
- stdio is never presented as secure.
