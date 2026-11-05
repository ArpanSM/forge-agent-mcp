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
| 4 | `cross_check` | The full validators already ran in `validate`; this stage calls `check_duplicate` for invoice cases, turns a gateway-only duplicate into a hard-stop flag, and reconciles evidence. |
| 5 | `summarise` | `recommend` maps flags to the fixed rubric; `record_recommendation` files the flags, conditions and rationale; builds the interrupt payload. |
| 6 | `human_approval` | Records the approval stage before the internal `await_approval` node calls `interrupt()`; the console resumes that wait. A refused approval returns here for a fresh token. |
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
- A failed run can be reset with `CaseRunner.reset`; the console exposes **Retry run** and
  starts a fresh thread without touching the gateway decision store.
- If the gateway already committed a decision before the checkpoint advanced, `reconcile`
  adopts that decision, verifies the audit chain and closes the thread. It does not write
  again.

## Approval tokens

Two different things are minted after the human action, both in `ConsoleService.decide`:

1. **Approval token** — HMAC-SHA256 JWT signed by the console with
   `FORGE_APPROVAL_SECRET`. It is bound to `iss`, `aud`, `sub`, `tid`, `case_id`,
   `decision`, `payload_hash`, `exp`, `iat` and a single-use `jti`. The gateway verifies
   signature, issuer, audience, expiry, tenant, case, decision and payload hash; the store
   marks the `jti` used in the decision transaction. The agent never holds the signing key.
2. **Agent bearer token for the decision call** — a short-lived HS256 JWT with
   `freight:read` and `freight:approve`, minted by the same local issuer the console uses.
   The long-lived agent client carries only `freight:read` and `freight:write`; the console
   passes the separate token to the runner, which holds it in the transient vault for the
   execute node.

The approval token is never minted on page load or when a case reaches the interrupt. It is
created only inside the reviewer action. Both raw tokens are held in an in-memory
`ApprovalTokenVault` for the execute node; graph state and `CaseResult` keep only an opaque
vault reference and the payload hash. A process restart clears the vault, so the reviewer
must supply a fresh approval.

An expired, mismatched, reused or missing approval returns the graph to the approval stage,
records the refusal in state and in the gateway's `write_denied` audit, and accepts a fresh
approval. `idempotency_conflict` remains a terminal alert.

## Idempotency

`decision_idempotency_key` hashes the canonical case id, decision, rationale and conditions
into `idem_<40 hex>`. It is deterministic for an identical human payload. A retry with the
same payload reaches the gateway's `(tenant, principal, tool, key)` record and returns the
stored decision with `replayed: true`; changed arguments return `idempotency_conflict`. The
gateway commits the decision, CRM stage, approval use, idempotency row and audit event in
one transaction. A unique index on `(tenant_id, case_id)` and a transaction check allow
exactly one decision per case; a second attempt returns `case_already_decided` with the
existing decision, which the graph reconciles.

## Allowed actions

The console derives the offered actions from the rule-engine recommendation and severity,
and the service enforces the same list:

| Recommendation | Available actions |
|---|---|
| Hard stop (by severity) | decline |
| decline / escalate | decline |
| hold | request documents, decline |
| request documents | request documents, decline |
| conditional approve | conditional approve, decline, request documents |
| approve | approve, conditional approve, decline, request documents |

A hard-stop flag is detected by `severity == "hard_stop"`, so a new hard-stop rule cannot
become silently approvable. A hold recommendation cannot be approved. A document-injection
or missing-document case can request documents or decline. A conditional approval requires
at least one condition. The reviewer note is optional; when supplied it is included in the
decision rationale, and the decision is attributed to the reviewer.

`request_documents` records a non-funding decision with CRM stage `awaiting_documents`.
`decline` records a declined decision and CRM stage. Neither creates a funding approval.

## Console routes

| Route | Purpose |
|---|---|
| `GET /` | Queue with onboarding/invoice cases, status, recommendation and flag counts. |
| `GET /cases/{case_id}` | Header, documents, cited fields, flags, recommendation, decision form, gateway checks and audit preview. |
| `POST /cases/{case_id}/run` | Start the graph; it stops at the approval interrupt. |
| `POST /cases/{case_id}/retry` | Reset a failed thread and start it again. |
| `POST /cases/{case_id}/decision` | Validate the action, mint tokens, resume the graph, write once. |
| `GET /cases/{case_id}/audit` | HTMX partial used to refresh the audit preview on demand. |

The UI uses HTMX boost for navigation and forms and an on-demand audit refresh partial.
Templates are server-rendered; there is no Node build.

## Demo

```bash
make demo       # complete both cases, then serve the console with the results
make console    # manual review: both cases wait at the interrupt
make demo-auto  # headless completion, no console
```

`make demo` resets the demo databases by default, seeds the gateway from
`corpus/manifest.json`, starts a real FastMCP HTTP server in a background thread, completes
Ridgeline (onboarding, approve) and Northgate (invoice, request documents), and serves the
console on `http://127.0.0.1:8000` so the committed decisions, evidence and audit chains
can be inspected. `make console` starts the same stack with both cases waiting for a human
review. `make demo` does not open a browser. Use `--keep` to preserve a previous demo
database.

## Deliberately out of scope

The eval harness, baseline, metrics and held-out set are a later ticket. The demo does not
deploy a hosted API, use a real IdP, call a real model during the offline path, or write a
real CRM. The model provider is not on the critical path for this slice: extraction and
rules are deterministic, which is what makes the demo and the tests offline.
