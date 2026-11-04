# Agent and reviewer console

The agent is a LangGraph case runner. The console is the human boundary in front of it.
Both use the MCP gateway for business state and side effects; neither writes the CRM or
audit tables directly.

```text
corpus PDFs ──► extraction (cite) ──► rule engine ──► duplicate check
                                                      │
console ◄── interrupt(approval request) ◄── recommendation
   │
   ├─ approval token (HMAC, payload-bound, single use)
   ├─ freight:approve bearer token (short lived)
   └─ Command(resume=…) ──► create_decision ──► audit verification
```

## The eight stages

| Stage | Node | What it does |
|---|---|---|
| 1 | `intake` | `read_case` and `get_policy` through the gateway. |
| 2 | `extract` | Classifies the packet and extracts fields with citations using the existing extraction module; records each document with `record_extraction`. |
| 3 | `validate` | Runs the deterministic rule engine (`validate`) over the extraction and typed corpus records. |
| 4 | `cross_check` | Calls `check_duplicate` for invoice cases; a gateway-only duplicate becomes a hard-stop flag. |
| 5 | `summarise` | `recommend` maps flags to the fixed rubric; `record_recommendation` files the flags, conditions and rationale; builds the interrupt payload. |
| 6 | `human_approval` | Pauses with `interrupt()` until the console resumes the thread. |
| 7 | `execute` | Calls `create_decision` with the human payload and a deterministic idempotency key, using the console-minted approval token and `freight:approve` bearer token. |
| 8 | `audit_close` | Reads `get_audit`, checks that the decision appears exactly once in an ordered chain, closes the run. |

`CaseRunner.start(case_id)` returns a `CaseResult` at `pending_approval`.
`CaseRunner.resume(case_id, ApprovalAction)` returns the completed result. `CaseRunner.get`
reads the checkpoint without touching the gateway. The console uses the same runner.

## State and restart

- The checkpointer is a separate SQLite database (`data/console-demo/checkpoints.db` in the
  demo). The gateway keeps cases, decisions, approvals, idempotency and audit in its own
  SQLite store.
- The thread id is `case:<case_id>`.
- A pending approval is a checkpoint plus an `approval_request` in graph state. A new
  process opens the same database and resumes the same thread; extraction and
  recommendation are not re-run.
- A completed case is returned as-is. Resuming it again does not invoke the graph and does
  not create a second write.

## Approval tokens

Two different things are minted after the human action, both in `ConsoleService.decide`:

1. **Approval token** — HMAC-SHA256 JWT signed by the console with
   `FORGE_APPROVAL_SECRET`. It is bound to `iss`, `aud`, `sub`, `tid`, `case_id`,
   `decision`, `payload_hash`, `exp`, `iat` and a single-use `jti`. The gateway verifies
   signature, issuer, audience, expiry, tenant, case, decision and payload hash; the store
   marks the `jti` used in the decision transaction. The agent never holds the signing key.
2. **Agent bearer token for the decision call** — a short-lived HS256 JWT with
   `freight:read` and `freight:approve`, minted by the same local issuer the console uses.
   The long-lived agent client carries only `freight:read` and `freight:write`; the decision
   call uses the separate token passed in the resume payload.

The approval token is never minted on page load or when a case reaches the interrupt. It is
created only inside the reviewer action.

## Idempotency

`decision_idempotency_key` hashes the canonical case id, decision, rationale and conditions
into `idem_<40 hex>`. It is deterministic for an identical human payload. A retry with the
same payload reaches the gateway's `(tenant, principal, tool, key)` record and returns the
stored decision with `replayed: true`; changed arguments return `idempotency_conflict`. The
gateway commits the decision, CRM stage, approval use, idempotency row and audit event in
one transaction.

## Allowed actions

The console offers approve, conditional approve, decline and request documents. The service
enforces the same restrictions as the UI:

- A hard-stop flag (watchlist, inactive authority, identity mismatch, expired/underinsured
  insurance, duplicate invoice) blocks approval and document requests. Decline the case;
  a hard stop can never be approved.
- A document-instruction injection blocks approval; the text is quarantined, so the reviewer
  can request documents or decline.
- A missing required document blocks approval; request documents or decline.
- A conditional approval requires at least one condition.
- Other high or medium flags allow a human override with a written note; the recommendation
  is not binding, but the decision is attributed to the reviewer.

`request_documents` records a non-funding decision with CRM stage `awaiting_documents`.
`decline` records a declined decision and CRM stage. Neither creates a funding approval.

## Console routes

| Route | Purpose |
|---|---|
| `GET /` | Queue with onboarding/invoice cases, status, recommendation and flag counts. |
| `GET /cases/{case_id}` | Header, documents, cited fields, flags, recommendation, decision form, gateway checks and audit preview. |
| `POST /cases/{case_id}/run` | Start the graph; it stops at the approval interrupt. |
| `POST /cases/{case_id}/decision` | Validate the action, mint tokens, resume the graph, write once. |
| `GET /cases/{case_id}/audit` | HTMX partial used to refresh the audit preview. |

The UI uses HTMX boost for navigation and forms and a polling audit partial. Templates are
server-rendered; there is no Node build.

## Demo

```bash
make demo       # gateway + agent + console, two cases wait for the reviewer
make demo-auto  # the same two cases completed by a scripted reviewer
```

`make demo` resets the demo databases by default, seeds the gateway from
`corpus/manifest.json`, starts a real FastMCP HTTP server in a background thread, starts the
console on `http://127.0.0.1:8000`, and pre-runs Ridgeline (onboarding, approve) and
Northgate (invoice, request documents) to the interrupt. It prints a walkthrough and does
not open a browser. Use `--keep` to preserve a previous demo database.

## Deliberately out of scope

The eval harness, baseline, metrics and held-out set are a later ticket. The demo does not
deploy a hosted API, use a real IdP, call a real model during the offline path, or write a
real CRM. The model provider is not on the critical path for this slice: extraction and
rules are deterministic, which is what makes the demo and the tests offline.
