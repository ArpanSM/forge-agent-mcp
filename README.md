# Forge Agent + MCP

A freight underwriting agent and an MCP business-tool gateway, built as a public, synthetic proof of a reliable document-operations workflow.

**Status:** implementation started — scaffold, model-provider abstraction, synthetic corpus generator, cited extraction, and the MCP gateway with auth, tenant isolation and audit. Agent, console and evals next.

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
make gateway         # FastMCP Streamable HTTP server on 127.0.0.1:8765/mcp
make gateway-client  # list tools, read a case, show a scoped 403
make gateway-demo    # in-process server + client demo
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
case; one high flag is a conditional approval, except missing documents which request
documents; medium flags add conditions; low flags approve with clarification. Text aimed at
an AI agent is flagged as `document_instruction_injection`, quoted nowhere, ignored, and
held for manual review; it can never override a hard stop.

## MCP gateway

`make gateway` starts the FastMCP server over Streamable HTTP at
`http://127.0.0.1:8765/mcp`. It exposes eight typed tools with `freight:read`,
`freight:write` and `freight:approve` scopes, a local HS256 issuer, a tenant check and an
append-only audit log. `make gateway-client` lists the tools, reads a case and shows a
scoped 403.

stdio is available for local tools and tests, but it is a trusted local transport and
skips auth; it is not the scoped demo. The startup warning and
[`docs/gateway.md`](docs/gateway.md) say so explicitly.

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

MIT.
