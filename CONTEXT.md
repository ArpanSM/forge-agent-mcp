# Domain context

This file is the shared vocabulary for the freight underwriting agent and MCP gateway. It is intentionally narrow: it covers the synthetic freight-factoring workflow, the agent states and the gateway contract.

**Clean-room:** all companies, carriers, people, documents, bank details, MC/DOT numbers and decisions in this repository are synthetic. No employer code, names, metrics, screenshots or retail workflows are used.

## The fictional world

| Term | Meaning |
|---|---|
| **Northwind Freight Capital** | The fictional freight factor. It buys invoices from small carriers and advances most of the invoice value. |
| **Ridgeline Haulage** | The fictional carrier used in the demo packets. |
| **Carrier** | A trucking company that hauls loads for shippers or brokers and wants to be paid before the broker pays. |
| **Factor** | A company that buys a carrier's invoice at a discount, advances cash, and collects from the broker. |
| **Broker** | The intermediary that arranges a load between a shipper and a carrier. |
| **Shipper** | The company that owns the freight being moved. |
| **Load** | One shipment, identified by a load number, route, weight and commodity. |
| **Invoice** | The carrier's bill for a delivered load. Factoring buys this receivable. |
| **Advance** | The percentage of the invoice paid to the carrier up front. |
| **Reserve** | The portion held back until the broker pays. |
| **Recourse** | The factor's right to charge the advance back to the carrier if the broker does not pay. |

## Documents

| Term | Meaning |
|---|---|
| **Onboarding packet** | The carrier's application and supporting documents used to decide whether to factor for them. |
| **Application / KYC** | Legal name, DBA, EIN, MC/DOT, address, ownership, contact details and requested terms. |
| **W-9** | US taxpayer identification form. Used to cross-check the legal name and EIN. |
| **Certificate of insurance (COI)** | Proof of auto liability, cargo and other coverage, with limits, effective dates and certificate holder. |
| **Bank letter / voided check** | Proof of the carrier's bank account and account holder name. |
| **MC authority snapshot** | A synthetic record of the carrier's operating authority status and age. |
| **UCC filing** | A secured-interest filing that may show a competing lender or lien. |
| **Rate confirmation** | The agreed rate and accessorials for a load. |
| **BOL (bill of lading)** | The receipt and contract for the freight, signed at pickup. |
| **POD (proof of delivery)** | The signed receipt showing the load was delivered. |
| **Notice of assignment** | The document telling the broker to pay the factor instead of the carrier. |
| **Watchlist** | A synthetic sanctions/PEP-style list used to test screening. It is not a real OFAC feed. |

## Underwriting

| Term | Meaning |
|---|---|
| **Underwriting** | Deciding whether to onboard a carrier or fund an invoice, and on what conditions. |
| **Red flag** | A rule finding that raises risk. Each flag has a code, severity, rule and evidence. |
| **Hard stop** | A flag that must lead to decline or escalation, never approval. |
| **High / medium / low** | Severity levels that drive hold, conditional approval, adjusted reserve or clarification. |
| **Recommendation** | The agent's proposed decision and rationale. It is not the final decision. |
| **Decision** | The human-approved outcome: approve, conditional approve, decline or request documents. |
| **Condition** | A specific requirement attached to a conditional approval. |
| **Evidence gap** | A required fact the packet does not establish. |
| **Double brokering** | A carrier re-brokering a load it accepted, often hiding the real carrier. |
| **Duplicate invoice** | The same invoice number, document hash or load submitted more than once. |
| **Stale / altered document** | A document whose dates, metadata or content do not match the packet. |

## Agent and gateway

| Term | Meaning |
|---|---|
| **Case** | One onboarding or invoice-funding review. A case has documents, extraction, flags, a recommendation, an approval and a decision. |
| **Case type** | `onboarding` or `invoice`. Both share the workflow but use different policy packs. |
| **Extraction** | Structured fields pulled from a document, each with a citation to document, page and bounding box. |
| **Citation** | The exact source location for an extracted field: document ID, page and bbox. |
| **Validator** | A deterministic rule over extracted fields and structured records. |
| **Policy pack** | The thresholds, flag definitions and decision rule for a case type. |
| **Agent run** | One LangGraph execution through intake, extraction, validation, cross-check, summary, approval and write. |
| **Human-in-the-loop (HITL)** | The reviewer approves, rejects or requests documents before a side effect. |
| **Approval token** | A console-minted HMAC token bound to the case, decision, payload, approver and expiry. The agent cannot mint it. |
| **Idempotency key** | A caller-supplied key that makes a retried write return the original result instead of creating a second row. |
| **MCP gateway** | The FastMCP server that exposes the business tools. It is the only path to the CRM and audit store. |
| **Tool** | One typed MCP operation, with an input schema, output schema, scope and side-effect description. |
| **Scope** | A permission such as `freight:read`, `freight:write` or `freight:approve`. |
| **Tenant** | The isolation boundary. Every case, token and tool call is tenant-scoped. |
| **Audit event** | An append-only record of a tool call, actor, input hash, output hash, outcome and linked decision. |
| **Trace ID** | The identifier that ties one case run and all its tool calls together. |
| **stdio / Streamable HTTP** | The two MCP transports. HTTP is the security boundary; stdio is a trusted local convenience and skips auth. |

## Out of scope vocabulary

Fraud ML scoring, identity/liveness, real OFAC/FMCSA/bank/credit-bureau integrations, real money movement, AI dispatch, load boards, TMS/ELD, billing, voice and multi-tenant SaaS are out of scope for the first slice. They are documented as future work, not implied by the demo.
