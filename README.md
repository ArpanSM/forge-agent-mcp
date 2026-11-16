# Forge Agent + MCP

**An AI agent that reads freight-insurance paperwork, checks the packet, and stops for a human before it writes a decision.**

**Status:** evaluated · local demo. Synthetic data and a free local model. This is a working proof, not a production service.

[![CI](https://github.com/ArpanSM/forge-agent-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/ArpanSM/forge-agent-mcp/actions/workflows/ci.yml)

The agent does the reading and checking. It classifies a carrier packet, pulls the fields with page and box citations, runs deterministic rules, and recommends a decision. The part that matters is the boundary. The agent can read, extract and recommend. It cannot approve its own work. A reviewer approves in a console, and the decision is written once through an MCP gateway with scoped tokens, an idempotency key and an append-only audit log.

MCP is the Model Context Protocol, a standard way for an agent to call typed business tools instead of holding a database password and a long prompt. Here the gateway is the only path to the case store, the CRM stage and the audit log.

**Start here:**

- **Reviewing the design:** [How it works](#how-it-works) · [threat model](#threat-model-and-failure-modes) · [production path](docs/production-path.md)
- **Checking the numbers:** [measured results](#measured-results) · [full evaluation report](docs/evals/agent-eval.md)
- **Running it:** [quickstart](#quickstart) · [screenshots](#screenshots)
- **Reading the story:** [case study](docs/case-study.md) · [learning map](docs/learning/agent-mcp.md)

## The problem

A freight factor is a company that buys small carriers' invoices and pays them before the broker does. Before it funds a carrier, it underwrites them. The work arrives as a packet of PDFs and scans: an application, W-9, certificate of insurance, bank letter, authority snapshot, rate confirmation, BOL, POD and invoice. An underwriter keys the fields, cross-checks identity, insurance and authority, catches duplicate invoices and arithmetic errors, writes a summary, and updates the CRM. It is slow, inconsistent, and difficult to audit afterwards.

A chat assistant does not fix that. The agent needs typed business tools, scoped permissions, a human approval before the decision is committed, and a replay-safe audit trail. It also needs a way to fail: a hard stop must stop, even when the language model sounds confident.

This project builds that workflow end to end with synthetic data, a free local model, and no paid infrastructure. It measures the difference between a naive prompt and a guarded pipeline on the same cases, and it fails the release if any safety gate breaks.

## What it proves

- All six release gates pass on the guarded pipeline. The naive baseline fails all six.
- Classification, field extraction, citations and audit completeness go from 0% to 100%. Duplicate writes and approval bypasses go from 100% to 0%.
- The agent cannot approve itself. The decision write needs a console-minted, single-use approval token bound to the case, payload and expiry.
- The evaluation is a gate, not a report. `make eval` exits non-zero when a safety metric drops below its threshold.

**Caveat:** the corpus is synthetic and the offline run uses a fake provider. The 100% numbers measure the pipeline contract on known templates, not model quality on messy scans. The [limits](#limits) and the [evaluation report](docs/evals/agent-eval.md) explain what that does and does not cover.

## How it works

```mermaid
flowchart LR
    R[Underwriter] --> C[Reviewer console<br/>FastAPI + HTMX]
    C -->|start or resume| A[LangGraph agent<br/>8 stages, SQLite checkpointer]
    A -->|typed MCP tools over HTTP| G[MCP gateway<br/>FastMCP]
    G --> D[(SQLite<br/>cases, documents, decisions)]
    G --> I[(Idempotency store)]
    G --> L[(Append-only audit log)]
    C -.->|mints HMAC approval token<br/>bound to case, payload, expiry| G
    D -.->|pending interrupt<br/>read by console| C
```

The console is the only component that can mint an approval token. The agent holds `freight:read` and `freight:write`; it cannot approve its own recommendation. The approval token is bound to the case, decision, canonical payload hash, approver, expiry and a single-use `jti`. The gateway verifies it inside the same transaction that writes the decision, CRM stage and audit event.

The eight agent stages are:

1. intake and classification
2. extraction with citations
3. deterministic validation
4. duplicate cross-check and evidence reconciliation
5. summary and recommendation
6. human approval (`interrupt()`)
7. execution through MCP
8. audit verification

The graph state survives a process restart in a SQLite checkpointer. The gateway keeps its own application tables and audit store.

## The gateway tools

One FastMCP server exposes eight typed tools over Streamable HTTP. Every tool has a Pydantic input and output contract, a result envelope, a scope, MCP annotations and negative tests.

| Tool | Scope | Side effect | What it returns |
|---|---|---|---|
| `read_case` | `freight:read` | None | Case, carrier, document metadata and a sanitized summary |
| `get_document` | `freight:read` | None | Text, pages, citation boxes and content hash |
| `get_policy` | `freight:read` | None | Policy pack, thresholds and decision rule for the case type |
| `check_duplicate` | `freight:read` | None | Invoice-number and document-hash matches |
| `record_extraction` | `freight:write` | Case state only | Extraction fields with citations |
| `record_recommendation` | `freight:write` | Case state only | Recommendation, conditions, flags and evidence |
| `create_decision` | `freight:approve` | One decision, CRM stage, idempotency row and audit event | Committed decision, replay or a typed refusal |
| `get_audit` | `freight:read` | None | Append-only audit events for the case |

A token missing a tool scope gets a spec-shaped `403 insufficient_scope` with a `WWW-Authenticate` challenge, not a `200` with an error body. A cross-tenant call gets `403 tenant_mismatch`. Invalid or expired tokens get `401`. **stdio skips auth**; it is a trusted local convenience, never the security boundary.

## Measured results

`make eval` runs the same 12 evaluation cases through two pipelines, then reports 4 held-out cases across 2 carriers separately. It is fully offline: both pipelines use `FakeProvider`, the fixed pipeline extracts with the deterministic template parser, and it writes through a real in-process FastMCP HTTP gateway. The numbers below come from the generated report, not a summary written by hand.

| Metric (12 eval cases) | Naive baseline | Fixed pipeline |
|---|---:|---:|
| Document classification accuracy | 0.0% | **100.0%** |
| Field F1, clean packets | 0.0% | **100.0%** |
| Field F1, noisy packets | 0.0% | **100.0%** |
| Citation accuracy | 0.0% | **100.0%** |
| Hard-stop recall | 0.0% | **100.0%** |
| False approves on hard stops | 100.0% | **0.0%** |
| Red-flag precision | 0.0% | **100.0%** |
| Decision agreement with the oracle | 16.7% | **100.0%** |
| Approval bypass rate | 100.0% | **0.0%** |
| Duplicate writes after retry | 100.0% | **0.0%** |
| Audit completeness | 0.0% | **100.0%** |
| Injection resistance | 0.0% (0/2) | **100.0%** |
| Tool-call success | n/a (no tools) | **100.0%** (167 calls) |
| p95 latency | 35.7 ms | 2319.7 ms |
| Model cost | $0.0000 | $0.0000 |

The fixed pipeline is slower because every case runs eight graph stages, persists state and makes real HTTP tool calls. The baseline is a single prompt and a direct database write. The offline model has no dollar price, so token counts are estimates and cost is reported as `$0.00`.

**The six hard gates** block release. All passed on the fixed pipeline, and all failed on the baseline as expected:

| Gate | Requirement | Baseline | Fixed | Status |
|---|---|---:|---:|---|
| Hard-stop recall | ≥ 100% | 0.0% | 100.0% | PASS |
| False approves on hard stops | 0.0% | 100.0% | 0.0% | PASS |
| Approval bypasses | 0.0% | 100.0% | 0.0% | PASS |
| Duplicate writes | 0.0% | 100.0% | 0.0% | PASS |
| Audit completeness | ≥ 100% | 0.0% | 100.0% | PASS |
| Injection resistance | ≥ 100% | 0.0% | 100.0% | PASS |

The six approval probes (blank, expired, mismatched case, mismatched decision, wrong payload and reused token) were all blocked. The four held-out cases across two carriers are reported separately and are not part of the gate: the fixed pipeline agreed on all four, versus one of four for the baseline. See [the full report](docs/evals/agent-eval.md) for per-case rows, intervals, the failure gallery and caveats.

## What the review caught

The first eval baseline ignored the model response and always approved, which made the headline comparison look better than it was. The console also initially let a reviewer approve a hold recommendation, expired approvals were terminal instead of returning to the approval stage, and two concurrent approvals could double-write a decision. All four were fixed before release.

The baseline now parses the provider's answer with explicit fallbacks. The console derives its actions from the recommendation and the service enforces them. A refused approval resumes for a fresh token. A unique `(tenant, case)` constraint plus an in-transaction check allows exactly one decision. Regression tests cover each fix.

The review is part of the evidence. The first version looked finished, and the checks found real holes.

## Threat model and failure modes

The assets are the case packet, the decision, the audit chain and the tokens. The adversaries are a malicious document, an over-broad agent token, a replayed approval, a retry after a timeout and a cross-tenant request. The controls are below.

- **Documents are untrusted.** Text aimed at an agent is flagged as `document_instruction_injection`, quoted nowhere, and held for manual review. It cannot change the risk facts or override a hard stop.
- **The model cannot choose tools.** LangGraph nodes call the typed gateway tools deterministically. The model extracts and explains; the rule engine owns severity.
- **The agent cannot self-approve.** Approval tokens are minted only by the console, bound to the payload and single-use. The signing key never enters the agent process.
- **Writes are replay-safe.** A same-key retry returns the original decision; changed arguments return `idempotency_conflict`; a second decision for the same case returns `case_already_decided`, which the agent reconciles.
- **Tenant isolation is enforced on every call.** Security denials are audited under the caller's tenant, never the victim's.
- **The audit is append-only.** SQLite triggers reject `UPDATE` and `DELETE`.

Out of scope for this slice: a hosted API, OAuth 2.1/PRM/PKCE/JWKS, a real identity provider, TLS, rate limiting, a real CRM, real carrier or bank integrations, and any legal or regulatory compliance claim. The local HS256 issuer and SQLite store are demo infrastructure. The [production path](docs/production-path.md) lists what would change and why.

## Quickstart

Python 3.12. No API key and no network are needed for the demo or the evaluation.

```bash
make install
make demo     # gateway + agent + console; completes two cases, then serves http://127.0.0.1:8000
make console  # same stack, but both cases wait for a manual decision
make eval     # offline baseline vs fixed on 12 eval + 4 held-out cases
make test     # offline pytest suite
make lint     # ruff check and format check
```

`make demo` prints the console URL and a short walkthrough. It does not open a browser. Use the console to inspect the queue, the documents, cited fields, flags, recommendation, decision and audit chain. `make console` is the manual-review path: start a case, read the evidence, then approve, conditionally approve, decline or request documents.

If the system Python is externally managed, pass the flags your environment needs:

```bash
make install PIP_FLAGS="--user --break-system-packages"
```

Optional live model paths are documented in [`docs/agent.md`](docs/agent.md). The evaluation itself stays offline unless you run `make eval-live`.

### Docker

The demo can also run in one container. This is for local use, not a deployment.

```bash
docker compose up --build
# open http://127.0.0.1:8000
```

The container generates the synthetic corpus on first start and keeps its database in the
`forge-data` volume. The Compose file exposes only the console port; the gateway stays inside
the container.

## Screenshots

All screenshots come from the local demo and the synthetic Northwind corpus.

| Review queue | Case detail |
|---|---|
| ![Review queue with 16 synthetic cases](docs/screenshots/queue.png) | ![Case detail with two high flags and a hold recommendation](docs/screenshots/case-detail.png) |

| Cited fields | Decision and audit chain |
|---|---|
| ![Extracted fields with document, page and box citations](docs/screenshots/citations.png) | ![Committed decision and append-only audit events](docs/screenshots/decision-audit.png) |

The evaluation summary is a representative run; the committed report is the source of truth: [eval-table.png](docs/screenshots/eval-table.png).

## Repository map

```text
src/forge_agent_mcp/
  corpus/      synthetic PDFs, manifest and ground truth
  extraction/  classification, cited fields and deterministic fallback
  rules/       validators, severity, recommendation rubric
  gateway/     FastMCP server, auth, approval token, idempotency, audit
  agent/       LangGraph case runner and MCP client
  console/     FastAPI + Jinja2 + HTMX reviewer console
  evals/       baseline, fixed pipeline, metrics, gates and reports
docs/          case study, eval report, learning map, demo script, ADRs
tests/         offline unit, integration and end-to-end tests
.github/       CI, issue templates and the pull-request template
Dockerfile     local demo image
server.json    draft MCP registry metadata (not published)
```

## Limits

- The corpus is synthetic: 8 fictional carriers, 16 cases, written by the same generator that produces the labels. It is a correctness harness, not a sample of real freight packets.
- The offline evaluation uses a deterministic fake provider and a template parser. The 100% extraction and citation numbers measure the pipeline contract on known templates, not model quality on messy scans. There is no LLM judge, no real sanctions, authority or bank integration, no fraud model and no money movement.
- The demo is local only. The approval flow, scopes and audit are real code, but deployment, identity provider and TLS are not production-grade. Latency is measured on one machine, not a capacity benchmark.

These are demo limits, not hidden gaps. The [production path](docs/production-path.md) lists what changes for a real deployment and in what order. The full evaluation caveats are in the [evaluation report](docs/evals/agent-eval.md).

## Docs

**Design and operations**

- [Case study](docs/case-study.md): the problem, the build, the results and what went wrong.
- [Gateway guide](docs/gateway.md): tools, scopes, approval tokens, idempotency and audit.
- [Agent and console guide](docs/agent.md): the eight stages, interrupt/resume and the reviewer UI.
- [Production path](docs/production-path.md): what is missing for a real deployment.
- [MCP registry metadata](docs/mcp-registry.md): the draft manifest and why it is not published.
- [Architecture decisions](docs/adr/)
- [Domain glossary](CONTEXT.md)

**Project**

- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Changelog](CHANGELOG.md)

**Evidence**

- [Evaluation report](docs/evals/agent-eval.md)
- [Demo script](docs/demo-script.md): the internal shot list for the walkthrough.

**Learning and contributing**

- [Learning map](docs/learning/agent-mcp.md): reading order, concepts and exercises.
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [MCP registry metadata](docs/mcp-registry.md): draft, not published.
- [Changelog](CHANGELOG.md)
- [Agent working notes](AGENTS.md) · [coding standards](CODING_STANDARDS.md)

## Contributing

Issues and pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md),
[AGENTS.md](AGENTS.md) and [CODING_STANDARDS.md](CODING_STANDARDS.md) first. The short version:

- All carriers, people, documents and bank details are synthetic. Do not add real data.
- Unit tests run offline. Never call a model or a network service from a unit test.
- Run `make test`, `make lint` and `make eval` before opening a pull request.
- Keep a change inside its ticket and do not change a decision recorded in an ADR without saying why.

For a security report, read [SECURITY.md](SECURITY.md) first and use GitHub's private
vulnerability reporting.

The GitHub Actions workflow runs the linter, the offline test suite and the evaluation gates
on every pull request.

## Licence

Project code is MIT (see [`LICENSE`](LICENSE)). The vendored `htmx.min.js` is htmx 2.0.4 under the BSD 0-Clause License; see [`src/forge_agent_mcp/console/static/HTMX-LICENSE`](src/forge_agent_mcp/console/static/HTMX-LICENSE).
