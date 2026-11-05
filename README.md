# Forge Agent + MCP

A freight underwriting agent and an MCP business-tool gateway, built as a public, synthetic proof of a reliable document-operations workflow.

**Status:** implementation started — scaffold, model-provider abstraction, synthetic corpus generator, cited extraction, the deterministic rule engine, the MCP gateway with auth, tenant isolation, audit and the approval-token/idempotent decision write, and the LangGraph agent plus reviewer console. Evaluation next.

- [Spec and implementation tickets](https://github.com/ArpanSM/forge-agent-mcp/issues/1)
- [Gateway guide](docs/gateway.md)
- [Domain glossary](CONTEXT.md)
- [Agent guide](AGENTS.md)
- [Architecture decisions](docs/adr/)

## Repository commands

```bash
make install         # editable install with dev dependencies
make corpus          # regenerate the synthetic corpus into data/corpus/
make test            # offline pytest suite; live tests are marked and deselected
make test-live       # opt-in live model tests (sets FORGE_LIVE_MODEL=1)
make lint            # ruff
make extract CASE=case_onb_ridgeline   # classify documents and print cited fields
make review CASE=case_inv_northgate    # flags, severity, recommendation and evidence
make gateway         # requires FORGE_APPROVAL_SECRET; FastMCP HTTP on 127.0.0.1:8765/mcp
make gateway-client  # list tools, read a case, show a scoped 403
make gateway-demo    # in-process server + client demo
make write-demo      # approval refusal, commit, replay, conflict and audit chain
make demo            # gateway + agent + console; completes both demo cases, then serves results
make console         # gateway + agent + console; two cases wait for the human review
make demo-auto       # headless: the same two cases completed by a scripted reviewer
```

If the system Python is externally managed, pass the flags your environment needs, for
example `make install PIP_FLAGS="--user --break-system-packages"`.

## Gateway auth

Streamable HTTP is the security boundary: it verifies the local HS256 issuer, the exact
audience, the global `freight:read` scope, each tool's write/approve scope and the tenant
claim. Invalid or expired tokens get `401`; a scoped shortfall gets `403
insufficient_scope` with `WWW-Authenticate`.

**stdio skips auth and scopes.** It is a trusted local transport for a local client, not
the scoped demo and not an authorization boundary. The server prints a startup warning
when it runs over stdio; use HTTP to demonstrate scopes, tenant isolation and the 403
challenge.

The corpus manifest at [`corpus/manifest.json`](corpus/manifest.json) is the source of
truth for the 8 carriers, 16 cases, document types, archetypes and the 12 + 4 eval/held-out
split. Generated PDFs and ground truth are written to `data/corpus/` and are gitignored.

## Extraction with citations

`make extract` classifies every document in a case from its page text and extracts the
fields the rule engine needs. Each value carries a citation with the document id, page,
bbox and the verbatim source quote; a citation is accepted only if the quote is on that
page and the bbox covers it.

The default `MODEL_PROVIDER=fake` selects `FakeProvider`; because it has no document
knowledge, classification and extraction fall back to the deterministic template parser,
so the command runs offline. Set `MODEL_PROVIDER=orcarouter` and `ORCAROUTER_API_KEY`, or
`MODEL_PROVIDER=lfm3`, to use a live provider. Classification and extraction both go
through the provider abstraction, with the keyword and template parsers as validated
fallbacks.

A schema or citation failure is retried once. If the provider still fails, extraction
tries the deterministic parser for a known template; only if that also fails is the
document reported as `needs_more_info`. Every accepted value keeps a verified citation,
so extraction never invents a value. Reports are written to
`data/extractions/<case>.json` for the later rule engine and gateway. The live model test
is marked `live`, excluded from `make test`, and additionally requires
`FORGE_LIVE_MODEL=1`; `make test-live` sets that flag.

## Deterministic rule engine

`make review` extracts a case with the offline deterministic parser, loads the typed
structured records (watchlist, prior invoices, UCC and broker bond), runs every validator
and prints the flags with their rule id and citation. The engine is pure Python and never
calls a model or the network.

Hard stops (watchlist hit, identity mismatch, inactive authority, expired or underinsured
policy, duplicate invoice) always decline or escalate. Two or more high flags hold the
case; one high flag is a conditional approval. Missing required documents always request
documents, even when another high flag is present. An invoice whose total or line items
cannot be parsed is flagged high and held as unverifiable. Medium flags add conditions;
low flags approve with clarification.

Text aimed at an AI agent is flagged as `document_instruction_injection`, quoted nowhere,
ignored, and held for manual review. The injection cannot change the risk facts or
override a hard stop. Demo thresholds live in
[`src/forge_agent_mcp/rules/policy.py`](src/forge_agent_mcp/rules/policy.py) and mirror
the gateway policy pack. The corpus also catalogues `altered_metadata` and
`formatting_inconsistency`; both are deferred to a later slice and are not emitted here.

## MCP gateway

`make gateway` starts the FastMCP server over Streamable HTTP at
`http://127.0.0.1:8765/mcp`. It exposes eight typed tools with `freight:read`,
`freight:write` and `freight:approve` scopes, a local HS256 issuer, a tenant check and an
append-only audit log. `make gateway-client` lists the tools, reads a case and shows a
scoped 403.

`create_decision` also requires a console-minted HMAC approval token bound to the tenant,
case, decision, payload and expiry, with a single-use `jti`. The gateway refuses to start
without `FORGE_APPROVAL_SECRET`; the agent never holds that key. The decision, CRM stage,
approval use, idempotency row and audit event commit in one transaction. A same-key retry
replays the stored result; changed arguments return `idempotency_conflict`; expired,
mismatched, forged and reused tokens are refused; an omitted or empty token returns
`approval_required`. `request_documents` records a non-funding request with CRM stage
`awaiting_documents`, never an approval. `make write-demo` runs every path and prints the
audit chain.

stdio is available for local tools and tests, but it is a trusted local transport and
skips auth; it is not the scoped demo. The startup warning and
[`docs/gateway.md`](docs/gateway.md) say so explicitly.

## Agent and reviewer console

The agent is one LangGraph 1.2.19 graph with a SQLite checkpointer. It runs eight stages:
intake and classification, extraction with citations, deterministic validation, the
tenant-wide duplicate cross-check and evidence reconciliation, summary and recommendation,
human approval (`interrupt()`), execution through MCP, and audit verification. A pending
approval survives a process restart; resuming uses `Command(resume=...)` and the same
thread id, so extraction and recommendation are not re-run. A refused approval returns to
the approval stage for a fresh token; a committed decision is reconciled without a second
write.

The reviewer console is FastAPI, Jinja2 and HTMX with no build step. It shows the case
queue, documents, extracted fields with page and bbox citations, flags grouped by severity,
the recommendation and conditions, the decision form, and the audit preview. The console
mints two things only after the reviewer acts: the single-use HMAC approval token bound to
the case, decision and payload, and a short-lived `freight:approve` bearer token for the
decision call. Neither token enters graph state or the `CaseResult`; the runner holds them
in a transient vault for the execute node. The long-lived agent token only carries
`freight:read` and `freight:write`. Hard stops cannot be approved from the UI or the
service; a hold recommendation can only request documents or decline; and a case can have
exactly one committed decision.

```bash
make demo       # gateway + agent + console; both cases completed, then inspect the results
make console    # manual review: two cases wait at the approval interrupt
make demo-auto  # headless completion without the console
```

`make demo` prints the console URL and a walkthrough, completes the onboarding and invoice
cases, and keeps the console open so the committed decisions and audit chains can be
inspected. It does not open a browser. `make console` leaves the same two cases pending for
a human review. The decision is written once with a deterministic idempotency key, and the
audit chain is verified on the way back. See [`docs/agent.md`](docs/agent.md) for the state
model, token boundary, idempotency and failure paths.

## The problem

Freight factors underwrite small carriers and buy their invoices. The work arrives as a packet of PDFs and scans: an onboarding application, W-9, certificate of insurance, bank letter, authority snapshot, rate confirmation, BOL, POD and invoice. An underwriter keys the fields, checks identity, insurance and authority, catches duplicate invoices and arithmetic errors, writes a summary, and updates the CRM.

The work is slow, inconsistent and hard to audit. A generic chatbot is not enough. The agent needs typed business tools, scoped permissions, a human approval before any side effect, and a replay-safe audit trail.

## What this will be

A synthetic end-to-end slice that:

1. Ingests a carrier packet and classifies the documents.
2. Extracts fields with page and bounding-box citations.
3. Runs deterministic validators for identity, insurance, authority, bank changes, duplicate invoices, invoice arithmetic and PODs.
4. Produces a red-flag summary and a recommendation with evidence.
5. Pauses for a human underwriter to approve, approve with conditions, decline or request documents.
6. Writes one audited, idempotent decision and CRM update through an MCP gateway.

The gateway exposes eight typed tools with `freight:read`, `freight:write` and `freight:approve` scopes, tenant binding, a console-minted approval token, an idempotency store and an append-only audit log.

## What makes it credible

- **Negative paths are the demo:** a write without approval is refused, a retry does not duplicate, a wrong scope gets a 403, a cross-tenant call is blocked, an injected document does not change the decision, and the audit chain names the approver.
- **Deterministic rules own the hard stops.** The LLM extracts and explains; it cannot override a hard stop.
- **A real eval:** 12 fixed cases plus 4 held out, a naive baseline, a fixed pipeline, deterministic metrics and hard gates for unsafe behaviour.
- **No real data:** every company, carrier, document, bank account and number is synthetic and watermarked. No employer code, names, metrics or retail workflows.

## Stack

- Python 3.12, FastAPI, Pydantic, SQLite
- LangGraph 1.2.19 with a SQLite checkpointer and human interrupt
- FastMCP 4.1.6 / MCP Python SDK 2.3.0 over Streamable HTTP and stdio
- OrcaRouter `tencent/hy4-preview-free` as the primary free model; local LFM3-1.7B as the fallback
- FastAPI + Jinja2 + HTMX for the reviewer console

## Status

The spec is the destination. Implementation is tracked in public tickets #2–#9, with native blocking edges. The first demo is local; a recorded walkthrough is the public proof. A hosted deployment is a follow-up, not part of the first slice.

## Licence

Project code is MIT (see `LICENSE`). The vendored `htmx.min.js` is htmx 2.0.4 under the
BSD 0-Clause License; see `src/forge_agent_mcp/console/static/HTMX-LICENSE`.
