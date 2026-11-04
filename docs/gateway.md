# MCP gateway

The gateway is one FastMCP 4.1.6 server (MCP Python SDK 2.3.0) exposing eight typed
business tools over Streamable HTTP and stdio. HTTP is the security boundary; stdio is a
trusted local convenience and **skips authentication and scopes**.

```bash
FORGE_APPROVAL_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  make gateway    # HTTP on 127.0.0.1:8765/mcp, seeded from corpus/manifest.json
make gateway-client # list tools, read a case, show a read token getting a 403
make gateway-demo   # start an in-process server and run the same client
make write-demo     # approval, replay, conflict and audit walkthrough
python -m forge_agent_mcp.gateway issue --scope freight:read
```

`make gateway` requires `FORGE_APPROVAL_SECRET`; there is no runtime default. The
`write-demo` and `gateway-demo` commands generate an ephemeral in-process key when the
variable is unset.

The database path is owned by the CLI and defaults to `data/gateway.db`; the library store
requires an explicit path. The corpus manifest is the source of truth for cases, documents
and the case-type policy packs.

## Tools

| Tool | Scope | Side effect | Result |
|---|---|---|---|
| `read_case` | `freight:read` | None | Case, carrier, document metadata and a sanitized state summary |
| `get_document` | `freight:read` | None | Text, pages, citation bboxes and content hash |
| `get_policy` | `freight:read` | None | Case-type policy pack, thresholds and decision rule |
| `check_duplicate` | `freight:read` | None | Invoice-number and document-hash matches |
| `record_extraction` | `freight:write` | Case state only | Filed extraction with citations |
| `record_recommendation` | `freight:write` | Case state only | Recommendation, conditions, flags and evidence |
| `create_decision` | `freight:approve` | One decision + CRM stage + audit + idempotency row | Committed decision, or a replay/conflict refusal |
| `get_audit` | `freight:read` | None | Append-only audit events for the case |

Every tool has a Pydantic input and output contract, a result envelope
(`ok`, `data`, `error_code`, `message`), and MCP annotations. Business errors return
`isError: true` with a stable code such as `case_not_found`, `document_not_found`,
`invalid_citation`, `invalid_evidence`, `invalid_request`, `approval_required`,
`approval_malformed`, `approval_expired`, `approval_mismatch`, `approval_reused` or
`idempotency_conflict`; protocol errors remain protocol errors. `tools/list` follows
registration order and is deterministic.

`get_policy` filters the global flag catalog by case type: onboarding never advertises
invoice-only flags (`duplicate_invoice`, `invoice_arithmetic_mismatch`, `pod_mismatch`,
`round_dollar_invoice`) and invoice never advertises onboarding-only flags.
`record_recommendation` stores evidence but does not score severity or change case state;
the rule engine (#3) owns severity and the graph (#7) owns state transitions.

## Auth

- A local HS256 issuer mints `iss`, `aud`, `sub`, `tid`, `scope`, `iat` and `exp`.
- `JWTVerifier` requires the exact issuer and audience and the global `freight:read` scope.
- A per-tool scope is enforced twice: FastMCP `require_scopes` hides/fails the tool, and a
  small transport-level gate returns the MCP-profile response before the handler runs.
- Invalid or expired token -> **401**.
- Valid token missing the tool scope -> **403** with
  `WWW-Authenticate: Bearer error="insufficient_scope", scope="freight:write"`.
- The `tid` claim must own the case on every call. A cross-tenant case -> **403**
  `tenant_mismatch` (no `insufficient_scope` challenge). Security audit rows go to the
  caller's tenant (or `system`), never the victim tenant, and carry `target_tenant`.

The config module ships a synthetic local-development JWT secret so the demo runs out of
the box. `.env.example` contains a placeholder only; set `GATEWAY_JWT_SECRET` outside a
local demo and put TLS in front of HTTP.

## Approval token

`create_decision` requires a second, console-minted HMAC-SHA256 token. The console holds
`FORGE_APPROVAL_SECRET`; the agent never does and there is no built-in default. The token is
a signed JWT with:

| Claim | Meaning |
|---|---|
| `iss` | `forge-console` |
| `aud` | the gateway audience (`forge-freight-gateway`) |
| `sub` | approver identity |
| `tid` | tenant |
| `case_id` | the one case the approval covers |
| `decision` | `approve`, `conditional_approve`, `decline` or `request_documents` |
| `payload_hash` | SHA-256 of the canonical case, decision, rationale and conditions |
| `exp`, `iat` | short expiry |
| `jti` | unique token id, single-use after a commit |

The gateway verifies the signature, issuer, audience, expiry, tenant, case, decision and
payload hash before writing. An omitted or empty token returns `approval_required`; a bad
signature, blank approver or wrong issuer/audience returns `approval_malformed`; a past
`exp` returns `approval_expired`; a token bound to another tenant, case, decision or payload
returns `approval_mismatch`; a consumed `jti` with a new idempotency key returns
`approval_reused`. Each refusal appends one `write_denied` audit row and writes no
decision, CRM stage or idempotency result. A denied approval can be retried with a fresh
token and the same idempotency key.

`request_documents` is a non-funding request: it records the request decision and CRM stage
`awaiting_documents`, never an approval or a funding decision.

## Idempotency and the decision transaction

`create_decision` canonicalises the business arguments (case, decision, rationale,
conditions) to a hash and stores it with `(tenant, principal, tool, idempotency_key)`
under a unique SQLite primary key. One transaction writes the decision, CRM stage, case
status, approval use, idempotency row and `write_committed` audit row.

- Same key + same canonical arguments -> the stored decision with `replayed: true`, one
  decision row and an `idempotent_replay` audit row.
- Same key + different arguments -> `idempotency_conflict`, no new write, and an
  `idempotency_conflict` audit row.
- A replay is checked before the approval token, so a retry after a timeout still works
  when the original token has expired or was consumed.
- Only successful commits store an idempotency result, so a refused approval leaves the
  key free for a retry.

`make write-demo` runs the whole path against a local server and prints the audit chain:
omitted/empty approval -> refusal, valid approval -> commit, same key -> replay, changed
args -> conflict, then expired, mismatched and reused tokens -> rejection, with exactly one
decision and one CRM stage at the end.

## stdio

stdio does not pass through the HTTP gate and FastMCP marks the transport as trusted, so
scopes and tenant checks are skipped. The server prints a startup warning. Use stdio for a
local Claude Desktop-style client or tests, never as the authorization boundary; use
Streamable HTTP for scoped calls. This caveat is covered by `tests/test_gateway_stdio.py`.

## Audit

Every read and case-state write appends an audit row with tenant, case, tool, event,
outcome, principal, input hash and output hash. Gate denials append `auth_denied` under the
caller's tenant with the reason and `target_tenant`; invalid tokens produce a 401 with no
victim audit. A committed decision writes `write_committed` in the decision transaction,
with the decision id, CRM stage, approver, `jti`, idempotency key and payload hash. Replays
and conflicts append `idempotent_replay` and `idempotency_conflict`; refused approvals
append `write_denied`. SQLite triggers reject `UPDATE` and `DELETE` on the audit table.

## Deliberately out of scope

This slice does not implement the LangGraph agent and console (#7), the eval harness (#8),
OAuth 2.1 authorization server, PRM/RFC 9728, RFC 8707, PKCE, CIMD, JWKS rotation, TLS,
revocation, a real IdP, rate limits or a real CRM. The "CRM stage" is a local SQLite row,
not an external integration. Those are production work or later tickets, not implied by
this demo.
