# ADR 0001: Model provider

- **Status:** Accepted
- **Date:** 2026-11-05
- **Ticket:** [#2](https://github.com/ArpanSM/forge-agent-mcp/issues/2), spec [#1](https://github.com/ArpanSM/forge-agent-mcp/issues/1)

## Context

The underwriting pipeline needs structured extraction and explanations, but the demo must
cost $0 and unit tests must run offline. Free-model variance is the main unknown.

## Decision

- **OrcaRouter `tencent/hy4-preview-free` with `reasoning_effort=none` is the primary
  provider.**
- **Local LFM3-1.7B is the offline fallback.** Both are reached through one
  OpenAI-compatible adapter configured by base URL, model and key.
- **Tests use `FakeProvider`.** Unit tests never call the network.
- If the primary is rate-limited or fails schema validation twice, the case becomes
  `needs_more_info`. It does not fall back to an unvalidated guess.
- Fallback is only for availability and transport errors. A structured-schema failure is
  retried once on the same provider; it never silently switches providers.
- The rule engine and gateway are model-independent; model choice is configuration.

## Consequences

- The same adapter supports both providers and a future paid model.
- A live smoke test must be opt-in and skipped when no key or endpoint is configured.
- Extraction quality is model-dependent; deterministic validators own every hard stop.
