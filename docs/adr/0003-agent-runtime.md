# ADR 0003: Agent runtime and human-in-the-loop state

- **Status:** Accepted
- **Date:** 2026-11-05
- **Ticket:** [#2](https://github.com/ArpanSM/forge-agent-mcp/issues/2), spec [#1](https://github.com/ArpanSM/forge-agent-mcp/issues/1)

## Context

An approval can arrive minutes after the recommendation, and the process may restart in
between. The workflow also needs an explicit state model that the console and the eval
harness can share.

## Decision

- **LangGraph 1.2.19** drives one eight-stage graph: intake/classify → extract → validate →
  cross-check → summarise/recommend → human approval → execute via MCP → audit/close.
- **A SQLite checkpointer** persists graph state, interrupt/resume and restart recovery.
- Application SQLite tables own cases, documents, extractions, flags, recommendations,
  decisions, approvals, audit and idempotency. The checkpointer is not the audit store.
- The console reads a pending interrupt, mints the approval token and resumes with
  `Command(resume=...)`. An expired token forces a fresh review.
- The graph calls typed MCP tools deterministically; the model does not choose arbitrary
  tools.

## Consequences

- A pending approval survives a restart without re-running extraction.
- State and side effects are separate: graph resume can never by itself duplicate a write.
- The eight stages are implementation behind `run_case(case_id) -> CaseResult`, not eight
  separate public seams.
