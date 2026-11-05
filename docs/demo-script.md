# Demo script

**Recorded walkthrough:** https://github.com/ArpanSM/forge-agent-mcp/releases/download/v0.1.0/forge-agent-mcp-demo.mp4


Two recordings:

1. a **60–90 second** portfolio walkthrough for the README and Upwork card;
2. a **5-minute** technical walkthrough for a client or hiring reviewer.

The short video is a highlight. The long one covers the negative paths: decline, request
documents, the scoped `403`, and the full audit chain. Record the console, not slides. The
console is the product.

## Before recording

```bash
cd forge-agent-mcp
make install
make console          # gateway + agent + console; both demo cases wait for approval
```

- Open `http://127.0.0.1:8000` and keep the browser at 100% or 110% zoom.
- Use a clean browser profile: no personal tabs, bookmarks or notifications.
- Terminal font at 16–18 px. Hide the shell prompt path if it contains a client name.
- Reset the demo before a clean take. `make console` resets the demo databases unless
  `--keep` is passed; stop it and start it again for a fresh run.
- The corpus is synthetic and watermarked. Never add a real packet, screenshot or metric.
- Record at 1080p or 1440p. Capture system audio only if the room is quiet; otherwise record
  the voiceover separately and add captions.

The short video does not need the terminal. The long video records the terminal segment
first, because `make gateway-demo` needs port `8765` and the console stack uses it when free.

## 60–90 second video (target 80 seconds)

**Title card (2 s):** "A freight underwriting agent that cannot approve itself."

**Caption throughout:** "Synthetic corpus · no real carrier data · local demo."

| Time | Screen | Action | Voiceover |
|---:|---|---|---|
| 0:00–0:08 | Console queue | Show the two pending cases and the flag counts. | "Freight factors review packets of PDFs to onboard carriers and fund invoices. This is a synthetic review queue: one clean onboarding case and one invoice with arithmetic and POD mismatches." |
| 0:08–0:22 | Ridgeline case detail | Scroll the document list, then the cited fields. Hover one citation. | "The agent classified five documents and extracted the fields the rules need. Every value carries a document, page, bounding box and the verbatim quote, so the reviewer can check it instead of trusting it." |
| 0:22–0:35 | Flags and recommendation | Point at the severity bar and the recommendation panel. | "Deterministic validators own the flags and the decision. The model can explain a flag, but it cannot overrule a hard stop. This packet is clean, so the recommendation is approve." |
| 0:35–0:50 | Decision panel | Click **Approve**, submit. Then show the committed decision and audit. | "The console mints an approval token bound to this case, decision and payload. The agent never holds the signing key. The gateway commits the decision, CRM stage, idempotency row and audit event in one transaction." |
| 0:50–1:02 | Audit trail | Scroll the audit events; point at `write_committed` and the verified chain. | "The audit chain names the reviewer, the approval token id and the payload hash. A retry with the same key replays the result; a changed payload is refused." |
| 1:02–1:15 | Northgate case | Open the invoice case, show the two high flags and recommendation. | "The invoice case is different. The total does not match the rate confirmation and the POD weight differs from the BOL, so the recommendation is request documents, not approve." |
| 1:15–1:20 | Title card | End card with repo URL and eval headline. | "Twelve evaluation cases, four held out, six hard gates. The fixed pipeline passed all six; the naive baseline failed all six. Code and measured report are in the repository." |

**End card:** `github.com/ArpanSM/forge-agent-mcp` · "Synthetic proof, not a production
service."

If the take runs long, cut the Northgate section to five seconds and keep the audit. Do not
cut the citation or the approval. Those are the point.

## 5-minute walkthrough (target 4:45–5:15)

Record in this order: terminal segment, console segment, evaluation segment. Cut them
together.

### 0:00–0:25 — The problem

**Screen:** README architecture diagram or the console queue.

**Say:** "A freight factor has to decide whether to onboard a carrier and whether to buy an
invoice. The evidence arrives as an application, W-9, insurance certificate, bank letter,
authority snapshot, rate confirmation, BOL, POD and invoice. The work is slow, inconsistent
and hard to audit. A chat assistant is not enough: it needs typed tools, scopes, a human
before any write, and a replay-safe audit trail."

### 0:25–0:50 — Architecture

**Screen:** the Mermaid diagram in the README, scrolled to the console → agent → gateway →
SQLite path.

**Say:** "The reviewer console starts and resumes a LangGraph agent. The agent calls eight
typed MCP tools over HTTP. The gateway owns scopes, tenant checks, the approval token,
idempotency and the audit log. The console is the only component that mints the approval
token. The agent holds read and write scopes, not approve."

### 0:50–1:20 — Queue and case detail

**Screen:** `http://127.0.0.1:8000`, then Ridgeline onboarding.

**Action:** Show the queue, the pending-approval count, the onboarding/invoice groups, and
open `case_onb_ridgeline`.

**Say:** "This is the local demo tenant. Both cases are already at the approval interrupt.
The queue reads case state from the gateway and the pending decision from the agent's
checkpoint. Ridgeline is the clean onboarding packet."

### 1:20–2:00 — Documents and cited fields

**Screen:** documents card, then extracted fields.

**Action:** Point at a document hash and page count, then a field citation: document id,
page, bbox and quote.

**Say:** "Each document is classified and hashed. Each extracted field points to a document,
page and bounding box, with the source quote. A citation is accepted only if the quote is on
that page and the box covers the words. If extraction fails twice, the field becomes
`needs_more_info`; it never guesses."

### 2:00–2:30 — Flags and recommendation

**Screen:** risk flags and recommendation panel.

**Action:** Show the severity bar (zero flags on Ridgeline), then open Northgate to show two
high flags.

**Say:** "The rule engine runs deterministic validators: identity, insurance, authority, bank
changes, duplicates, invoice arithmetic, POD and required documents. Hard stops always
decline. Missing documents always request documents. Two high flags hold the case. The
recommendation is a proposal; the human still decides."

### 2:30–3:10 — Approve, commit and audit

**Screen:** Ridgeline decision panel, then audit trail.

**Action:** Choose **Approve**, keep the default approver, submit. Show the committed decision
and `Audit chain verified`. Scroll to `write_committed`; point at approver, `jti`, payload
hash.

**Say:** "The console mints two things: an HMAC approval token bound to this case, decision,
rationale and conditions, and a short-lived `freight:approve` bearer token for the one call.
The raw tokens live in a transient vault, not the checkpoint. The gateway verifies signature,
expiry, tenant, case, decision and payload hash, then marks the token single-use and commits
the decision, CRM stage, idempotency row and audit event atomically."

### 3:10–3:40 — Request documents and decline

**Screen:** Northgate invoice, then Cedar Pine onboarding.

**Action:** On Northgate, choose **Request documents**, submit, show `awaiting_documents` and
the audit. Then open Cedar Pine, show the hard stop and point out that only **Decline** is
available.

**Say:** "Request documents records a non-funding decision and moves the CRM stage to
`awaiting_documents`; it is not an approval. Cedar Pine has an expired, underinsured policy.
The UI only offers decline, and the service enforces the same list. A hold cannot be approved
from either layer."

### 3:40–4:15 — Gateway scopes and the 403

**Screen:** terminal. Run `make gateway-demo` (record this segment before the console if port
8765 is busy).

**Action:** Let it list tools, read a case with a read token, then attempt a write with the
read token. Pause on the `403` and the `WWW-Authenticate` line.

**Say:** "This is the MCP gateway outside the console. The read token lists tools and reads a
case. The same token calling a write tool gets a real `403 insufficient_scope` with a
`WWW-Authenticate` challenge, not a `200` with an error body. The tenant claim is checked on
every call, and a cross-tenant case returns `403 tenant_mismatch`. Invalid or expired tokens
get `401`."

Optional: run `make write-demo` to show blank approval refused, commit, replay, conflict and
the reused-token refusal. If you include it, cut the Northgate segment to 20 seconds.

### 4:15–4:50 — Evaluation

**Screen:** `docs/evals/agent-eval.md`, scrolled to the baseline/fixed table and hard gates.
Or run `make eval` and show the terminal gate summary, then open the report.

**Say:** "The evaluation runs the same 12 cases through a naive baseline and the fixed
pipeline, then reports 4 held-out carriers separately. The baseline approves every hard stop,
approves both injection cases and duplicates every retry. The fixed pipeline reaches 100%
hard-stop recall, zero false approves, zero approval bypasses, zero duplicate writes, 100%
audit completeness and 100% injection resistance. All six hard gates pass; the baseline fails
all six. The run is offline, the corpus is synthetic, and the 100% extraction numbers measure
the pipeline contract, not production model quality."

### 4:50–5:05 — Limits and close

**Screen:** README limits or end card.

**Say:** "This is a local synthetic proof. No real carrier data, no real compliance claim, no
hosted API. The next step for a client is a short reliability review on their packets, or a
fixed-scope build of one workflow with the same evidence. Code, case study and learning map
are linked below."

**End card:** repo URL, case study URL, eval report URL, "synthetic · local · measured."

## Caption file

Keep captions short and literal. Do not caption "production-ready", "compliant" or
"real-time". Use "synthetic corpus", "local demo", "offline evaluation" and "six hard gates".
The video description should repeat the caveat: the corpus is synthetic and the numbers are
development numbers, not a production benchmark.

## B-roll and screenshots

The four stills in [`screenshots/`](screenshots/) can be used as thumbnails or cutaways:

- `queue.png` — the two pending cases and flag counts.
- `case-detail.png` — documents, cited fields, flags and recommendation.
- `decision-audit.png` — a committed decision with the verified audit chain.
- `eval-table.png` — the measured baseline/fixed table and the six gates.

If a still does not match the recording, retake it instead of shipping a broken image.
