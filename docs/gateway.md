# MCP gateway

The gateway is one FastMCP 4.1.6 server (MCP Python SDK 2.3.0) exposing eight typed
business tools over Streamable HTTP and stdio. HTTP is the security boundary; stdio is a
trusted local convenience and **skips authentication and scopes**.

```bash
make gateway        # HTTP on 127.0.0.1:8765/mcp, seeded from corpus/manifest.json
make gateway-client # list tools, read a case, show a read token getting a 403
make gateway-demo   # start an in-process server and run the same client
python -m forge_agent_mcp.gateway issue --scope freight:read
```

The database defaults to `data/gateway.db` and is created and seeded on first start. The
corpus manifest is the source of truth for cases, documents and the policy packs.

## Tools

| Tool | Scope | Side effect | Result |
|---|---|---|---|
| `read_case` | `freight:read` | None | Case, carrier, document metadata and current state |
| `get_document` | `freight:read` | None | Text, pages, citation bboxes and content hash |
| `get_policy` | `freight:read` | None | Policy pack, thresholds and decision rule |
| `check_duplicate` | `freight:read` | None | Invoice-number and document-hash matches |
| `record_extraction` | `freight:write` | Case state only | Filed extraction with citations |
| `record_recommendation` | `freight:write` | Case state only | Recommendation, conditions and flags |
| `create_decision` | `freight:approve` | None in this slice | Always `approval_required` until #6 |
| `get_audit` | `freight:read` | None | Append-only audit events for the case |

Every tool has a Pydantic input and output contract, a result envelope
(`ok`, `data`, `error_code`, `message`), and MCP annotations. Business errors return
`isError: true` with a stable code such as `case_not_found`, `document_not_found`,
`invalid_flag` or `approval_required`; protocol errors remain protocol errors.
`tools/list` follows registration order and is deterministic.

## Auth

- A local HS256 issuer mints `iss`, `aud`, `sub`, `tid`, `scope`, `iat` and `exp`.
- `JWTVerifier` requires the exact issuer and audience and the global `freight:read` scope.
- A per-tool scope is enforced twice: FastMCP `require_scopes` hides/fails the tool, and a
  small transport-level gate returns the MCP-profile response before the handler runs.
- Invalid or expired token -> **401**.
- Valid token missing the tool scope -> **403** with
  `WWW-Authenticate: Bearer error="insufficient_scope", scope="freight:write"`.
- The `tid` claim must own the case on every call. A cross-tenant case -> **403** with a
  `tenant_mismatch` body and an `auth_denied` audit row.
- `create_decision` is registered with the `freight:approve` scope but deliberately refuses
  until ticket #6 implements the console-minted approval token and idempotent write.

The demo secret in `config.py` and `.env.example` is synthetic and local-only. Set
`GATEWAY_JWT_SECRET` outside a local demo and put TLS in front of HTTP.

## stdio

stdio does not pass through the HTTP gate and FastMCP marks the transport as trusted, so
scopes and tenant checks are skipped. The server prints a startup warning. Use stdio for a
local Claude Desktop-style client or tests, never as the authorization boundary; use
Streamable HTTP for scoped calls. This caveat is covered by `tests/test_gateway_stdio.py`.

## Audit

Every read and case-state write appends an audit row with tenant, case, tool, event,
outcome, principal, input hash and output hash. Gate denials append `auth_denied` with the
reason and caller tenant. SQLite triggers reject `UPDATE` and `DELETE` on the audit table.
The decision, approval and idempotency tables exist but stay empty until #6.

## Deliberately out of scope

Ticket #5 does not implement the approval-token/idempotent decision write (#6), the
LangGraph agent and console (#7), the eval harness (#8), OAuth 2.1 authorization server,
PRM/RFC 9728, RFC 8707, PKCE, CIMD, JWKS rotation, TLS, revocation, a real IdP or rate
limits. Those are production work or later tickets, not implied by this demo.
