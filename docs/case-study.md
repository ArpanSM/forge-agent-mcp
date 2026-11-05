# Case study: an underwriting agent that cannot approve itself

**Code:** https://github.com/ArpanSM/forge-agent-mcp
**Walkthrough:** video placeholder — recording in progress, link added with the `v0.1.0` release
**Evaluation:** [full report](evals/agent-eval.md)

## The problem

A freight factor underwrites small carriers and buys their invoices. The packet is ordinary
document work: an application, W-9, certificate of insurance, bank letter, authority
snapshot, rate confirmation, BOL, POD and invoice. The decision is not ordinary. An
underwriter has to check that the legal name, EIN and MC/DOT agree across documents, that
insurance is current and above the minimum, that authority is active and old enough, that
the bank account has not just changed, that the invoice is not a duplicate, and that the
invoice arithmetic matches the rate confirmation. Then they write a summary and update the
CRM.

The failure modes are expensive and quiet. A plausible packet with an expired policy gets
approved because the model reads the dates wrong. The same invoice gets funded twice because
a retry creates a second row. A reviewer approves a case the rules said to hold. A document
contains a line telling the assistant to ignore the rules, and nothing in the demo notices.

This project builds that workflow as a public, synthetic proof, and makes those failures
measurable rather than describing them.

## Constraints

- **No real data.** Eight fictional carriers, sixteen cases, watermarked PDFs, `example.com`
  addresses and `555-01xx` phone numbers. No employer code, customer name or real bank
  detail appears anywhere.
- **No paid infrastructure.** The development and offline evaluation model is free; tests use
  a deterministic `FakeProvider`. The demo runs on one machine with SQLite.
- **No identity provider.** Auth is a local HS256 issuer and a console-minted HMAC approval
  token. That is enough to demonstrate scopes, tenant isolation and a payload-bound approval
  without pretending it is production IAM.
- **A human before every side effect.** The agent may read, extract and recommend. It may not
  decide.
- **A release gate, not a vibe check.** Six safety metrics must pass before the evaluation is
  called a release.

## Approach

The system is one LangGraph graph over a FastMCP gateway.

1. **Ingest and classify** the packet through `read_case` and `get_policy`.
2. **Extract** each document into typed fields with a citation to document, page and bounding
   box. A schema or citation failure retries once, then falls back to a deterministic template
   parser, then reports `needs_more_info`. It never guesses a value.
3. **Validate** with pure Python rules. Hard stops own the decision: watchlist hit, inactive
   authority, identity mismatch, expired or underinsured policy, duplicate invoice. High
   flags hold or request documents. Medium flags add conditions. The model may explain a
   flag; it cannot overrule one.
4. **Cross-check** duplicates across the tenant through the gateway and reconcile the
   evidence.
5. **Recommend** a decision with rationale, conditions and evidence, and record it through a
   write-scoped tool.
6. **Pause** at `interrupt()` and show the reviewer the documents, fields, flags,
   recommendation and policy.
7. **Execute** only after the console mints an approval token and a short-lived
   `freight:approve` bearer token. `create_decision` commits the decision, CRM stage,
   approval use, idempotency row and audit event in one transaction.
8. **Verify** the audit chain and close.

The gateway exposes eight typed tools with `freight:read`, `freight:write` and
`freight:approve` scopes, a tenant check on every call, a spec-correct `403
insufficient_scope`, an idempotency store and an append-only audit log. HTTP is the security
boundary. stdio skips auth and is documented as a trusted local convenience.

## Results

`make eval` runs a naive baseline and the fixed pipeline over the same 12 evaluation cases,
then reports 4 held-out carriers separately. It runs offline: both pipelines use
`FakeProvider`, and the fixed pipeline writes through a real in-process FastMCP HTTP gateway.

| Metric (12 eval cases) | Baseline | Fixed |
|---|---:|---:|
| Classification accuracy | 0.0% | **100.0%** |
| Field F1, clean / noisy | 0.0% / 0.0% | **100.0% / 100.0%** |
| Citation accuracy | 0.0% | **100.0%** |
| Hard-stop recall | 0.0% | **100.0%** |
| False approves on hard stops | 100.0% | **0.0%** |
| Decision agreement | 16.7% | **100.0%** |
| Approval bypass rate | 100.0% | **0.0%** |
| Duplicate writes | 100.0% | **0.0%** |
| Audit completeness | 0.0% | **100.0%** |
| Injection resistance | 0.0% (0/2) | **100.0%** |
| Tool-call success | n/a | **100.0%** (167 calls) |

All six hard gates passed on the fixed pipeline and failed on the baseline. The baseline
approved every hard stop, approved both injection cases, and duplicated every write on
retry. The fixed pipeline declined all six hard stops, held both injection cases for
documents, wrote once per case and verified every audit chain. The six approval probes
(blank, expired, mismatched case, mismatched decision, wrong payload, reused token) were all
blocked.

The four held-out carriers are reported separately. The fixed pipeline matched all four
oracle decisions; the baseline matched one. They are not part of the release gate.

**Caveats.** The corpus is synthetic and generated from the same templates as the labels, so
the 100% extraction, field and citation numbers measure the pipeline contract on known
templates, not model quality on messy real scans. The run is offline and one pass, with no
LLM judge, no real sanctions/authority/bank integration and no real CRM. Latency is not a
capacity benchmark. These are development numbers.

## What went wrong, and what changed

A two-axis review of the implementation and the evaluation found four problems worth
keeping in the story:

1. **The baseline ignored the model response.** The first baseline prompt comparison always
   approved, which made the fixed pipeline look better for the wrong reason. The baseline now
   parses the provider's JSON with explicit fallbacks and records an unparseable answer as a
   fallback approve instead of hiding it.
2. **The console could approve a hold.** The reviewer UI and the service now derive the
   offered actions from the recommendation and severity. A hard stop can only be declined; a
   hold can request documents or decline; nothing in the UI can turn a hold into an approval.
3. **An expired approval was terminal.** A refused approval now returns to the approval stage
   with the refusal in state and the gateway's `write_denied` audit row, so the reviewer can
   supply a fresh token without losing the extraction or the recommendation.
4. **Two concurrent approvals could double-write.** A unique `(tenant, case)` constraint and
   an in-transaction check allow exactly one decision. A second valid approval gets
   `case_already_decided` with the existing decision, and the agent reconciles instead of
   writing again.

Each fix has a regression test. The review is part of the evidence: the first version looked
finished, and the checks found real holes.

## What it means for a client

If you are putting an agent in front of document work, the demo is not the hard part. The
hard part is the boundary: what the model may do, what a rule must decide, who can approve,
what happens on a retry, and how you reconstruct the decision six months later.

The transferable pieces are:

- a typed tool surface with scopes instead of a prompt full of database access;
- deterministic hard stops that the model cannot override;
- a human approval bound to the exact payload, not a generic "yes";
- idempotent writes and a single-decision constraint;
- citations that point at a page and a box, so a reviewer can check the value;
- an evaluation with a naive baseline, held-out cases and gates that fail the release.

That is a **3–5 day reliability review** for an existing prototype, or a **fixed-scope build**
of one workflow with the same evidence. The next step is a short call and three packets or
failure cases from your current process.

## Limitations

- Synthetic corpus; not a benchmark of real carrier documents or real underwriting outcomes.
- Offline `FakeProvider` and deterministic parser; live-model extraction quality is not
  measured by the release gate.
- No LLM judge. Correctness is measured against generated ground truth, not human labels.
- No real OFAC/FMCSA/bank/credit-bureau integration, no fraud ML, no money movement.
- Local HS256 issuer, HMAC approval secret and SQLite; no OAuth 2.1, real IdP, TLS,
  revocation or rate limiting. These are production work.
- Local demo only; no hosted API. The video is a walkthrough of the local console.
- Held-out results are reported but do not gate the release, because four cases are too few
  to support a strong claim.
