# Production path

**What this is:** the gap between the local demo and a deployment that could take real traffic.
**For:** engineers and technical buyers reviewing the design.
**Status:** planning note. None of this is built yet.

The demo is deliberately local. The approval gate, scopes, idempotency and audit trail are
real code, but the infrastructure around them is not. This page lists what would change, why
it matters, and the order I would do it in. It is a plan, not a promise.

| Area | Demo today | Production needs | Why it matters |
|---|---|---|---|
| Identity | Local HS256 issuer and a console-minted HMAC approval token | OAuth 2.1 resource-server profile, a real identity provider, protected-resource metadata (RFC 9728), audience checks, PKCE, short-lived tokens, key rotation and revocation | Clients connect from Claude, ChatGPT or Cursor. Their IdP issues and revokes the tokens, not a local secret. |
| Tenancy | A `tid` claim is checked on every call; one demo tenant is seeded | Tenant-scoped credentials, Postgres row-level security, per-tenant keys, admin roles and cross-tenant tests | One leaked token must never read another customer's packets. |
| Storage | SQLite in one process | Postgres with row-level security, migrations, backups and point-in-time recovery; an audit store with retention rules | SQLite is fine for a demo. Real volume and concurrent reviewers need a server database. |
| Durability | SQLite checkpointer and an idempotency table | A durable queue and worker, an outbox, retry policy, a dead-letter queue and runs that resume after a restart | Documents take minutes. A restart should not lose a case or duplicate a write. |
| Limits | None | Per-token and per-tenant rate limits, request size limits, timeouts and pagination on list tools | A public MCP server is scanned and abused. One client should not starve another. |
| Observability | Audit log and a trace ID per case | OpenTelemetry traces and metrics, structured logs, dashboards for tool success, latency and cost, and alerting | "It worked yesterday" needs a trace, not a guess. |
| Documents | Text-layer synthetic PDFs, a deterministic parser and one noisy variant | OCR (Tesseract, Docling or Textract), table extraction, confidence thresholds, a review queue for low confidence and real packet samples | Real packets are scans with skew, stamps and missing pages. |
| Integrations | A local SQLite CRM stage | The customer's CRM or underwriting API, an outbox with retries, reconciliation and sandbox credentials | The decision has to land in the system the business already uses. |
| Deployment | `make` on one machine | Secret manager, TLS, a container image, CI/CD, staging, health checks, backups and rollback | A public endpoint needs a deploy and a way back. |
| Legal and data | Synthetic data only, no compliance claim | A data processing agreement, retention policy, PII handling, provider review and jurisdiction review | Freight packets contain business and personal data. |
| Evaluation | Offline `FakeProvider`, 12 eval cases and 4 held-out cases | Live-model evaluation, a larger labelled set, drift checks, per-tenant eval and cost per case | The 100% numbers are a contract test, not a quality claim. |

## What I would do first

1. **Put the gateway behind a real identity provider and Postgres row-level security.** This
   is the gate for everything else. Until tokens come from the customer's IdP and the database
   enforces tenancy, the rest is decoration.
2. **Add a durable worker and an outbox.** Extraction and review take time, and the current
   SQLite checkpointer is tied to one machine. A queue, retries and a dead-letter path make
   the workflow survive a restart.
3. **Add tracing and a real OCR path, then re-run the evaluation on messy scans.** Measure
   before adding more tools. The existing harness can compare a naive pipeline, a fixed
   pipeline and the OCR path on the same cases.

## What I would keep

The parts that should not change:

- Typed tools with scopes. The model chooses an operation, not a SQL query.
- Deterministic rules for severity and hard stops. The model explains; it does not overrule.
- The approval token bound to the case, payload, approver and expiry.
- Idempotent writes and the single-decision constraint.
- The append-only audit log and the separation between console and agent.
- The evaluation as a release gate.

## Where each change lands

| Change | Main code today |
|---|---|
| Identity provider and OAuth profile | `src/forge_agent_mcp/gateway/auth.py`, `middleware.py` |
| Postgres and row-level security | `src/forge_agent_mcp/gateway/store.py` |
| Durable worker and outbox | `src/forge_agent_mcp/agent/graph.py`, `console/service.py` |
| Rate limits and pagination | `src/forge_agent_mcp/gateway/server.py`, `tools.py` |
| Tracing and cost metrics | `src/forge_agent_mcp/gateway/middleware.py`, `evals/` |
| OCR and table extraction | `src/forge_agent_mcp/extraction/` |
| SSO and reviewer roles | `src/forge_agent_mcp/console/app.py`, `service.py` |

## What this is not

This is not a production claim. The repository has no hosted API, no real customer data, no
compliance certification and no service-level agreement. The demo exists to show the design
and the failure modes, and to make the next build cheaper.
