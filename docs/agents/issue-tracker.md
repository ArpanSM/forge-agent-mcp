# Issue tracker

Work is tracked in GitHub Issues in the public repository:

- Repository: [`ArpanSM/forge-agent-mcp`](https://github.com/ArpanSM/forge-agent-mcp)
- Spec: [issue #1 — Spec: freight underwriting agent + MCP gateway](https://github.com/ArpanSM/forge-agent-mcp/issues/1)
- Implementation tickets: issues #2–#9, each a native GitHub sub-issue of #1.

## Labels

| Label | Meaning |
|---|---|
| `spec` | The public product and architecture contract. |
| `ready-for-agent` | Scoped and unblocked; an agent can implement it. |
| `area:corpus` | Scaffold and synthetic corpus. |
| `area:extraction` | Document classification and citation-backed extraction. |
| `area:agent` | Rule engine, LangGraph runtime and reviewer console. |
| `area:mcp` | MCP gateway, auth, tenant isolation, audit and idempotency. |
| `area:evals` | Evaluation harness, baseline and report. |
| `area:docs` | Proof assets and release materials. |

## Structure

- Use **native sub-issues** under the spec (#1). Do not duplicate the ticket body in prose
  documents.
- Record dependencies with GitHub's **native blocking edges**. An issue with an open
  blocker is not `ready-for-agent`.
- Link each implementation ticket back to the spec section it implements.
- Keep acceptance criteria as checkboxes in the ticket. Update them as work lands; do not
  rewrite the spec to match the implementation.
- PRs reference the ticket (`Closes #N` only when every acceptance criterion is met) and
  the spec (#1).

## Current blocking edges

| Issue | Blocked by | Blocks |
|---|---|---|
| #2 Scaffold and synthetic corpus | — | #3, #5 |
| #3 Extraction and citations | #2 | #4 |
| #4 Rule engine and recommendation | #3 | #7 |
| #5 MCP gateway, auth and audit | #2 | #6 |
| #6 Approval token and idempotent write | #5 | #7 |
| #7 LangGraph agent and console | #4, #6 | #8 |
| #8 Evaluation harness and report | #7 | #9 |
| #9 Proof assets and release gate | #8 | — |

## Agent workflow

1. Read the ticket, spec, `CONTEXT.md`, the relevant ADRs and the coding standards.
2. Work on a branch named for the ticket (for example `feat/scaffold-corpus`).
3. Commit coherent slices; push the branch and open a PR against `main`.
4. Do not merge. A separate review agent checks standards and spec compliance.
