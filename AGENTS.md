# Agent guide

This repository is a public, synthetic proof of a freight underwriting agent and an MCP
business-tool gateway. Read these before changing code:

- [CONTEXT.md](CONTEXT.md) — domain vocabulary and the synthetic world.
- [CODING_STANDARDS.md](CODING_STANDARDS.md) — Python, testing and data rules.
- [docs/agents/issue-tracker.md](docs/agents/issue-tracker.md) — where work is tracked.
- [docs/adr/](docs/adr/) — settled architecture decisions.

## Ground rules

- All carriers, people, documents, bank details and numbers are synthetic. No real PII,
  real keys or real customer data.
- Unit tests run offline. Never call a model or network service from a unit test.
- Build tickets in order and keep changes inside the ticket's scope.
- Use the public spec in [issue #1](https://github.com/ArpanSM/forge-agent-mcp/issues/1)
  as the source of truth; do not silently change a decision recorded in an ADR.

## Commands

```bash
make install   # editable install with dev dependencies
make corpus    # regenerate the synthetic corpus into data/corpus/
make test      # pytest
make lint      # ruff
make gateway   # FastMCP gateway over Streamable HTTP
make gateway-client # client demo against a running gateway
make gateway-demo   # in-process server + client demo
```
