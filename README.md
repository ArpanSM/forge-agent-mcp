# Forge Agent + MCP

A freight underwriting agent and an MCP business-tool gateway, built as a public, synthetic proof of a reliable document-operations workflow.

**Status:** implementation started — scaffold, model-provider abstraction and synthetic corpus generator.

- [Spec and implementation tickets](https://github.com/ArpanSM/forge-agent-mcp/issues/1)
- [Domain glossary](CONTEXT.md)
- [Agent guide](AGENTS.md)
- [Architecture decisions](docs/adr/)

## Repository commands

```bash
make install   # editable install with dev dependencies
make corpus    # regenerate the synthetic corpus into data/corpus/
make test      # offline pytest suite
make lint      # ruff
```

The corpus manifest at [`corpus/manifest.json`](corpus/manifest.json) is the source of
truth for the 8 carriers, 16 cases, document types, archetypes and the 12 + 4 eval/held-out
split. Generated PDFs and ground truth are written to `data/corpus/` and are gitignored.

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
