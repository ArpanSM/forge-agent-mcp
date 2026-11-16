# Security policy

## Supported versions

This is a demo, not a supported service. The `main` branch is the only version that receives
fixes.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private vulnerability
reporting: on the repository, open **Security** and choose **Report a vulnerability**.

Include:

- what you can do that you should not be able to do;
- the smallest steps to reproduce it;
- the commit or tag you tested;
- whether the issue affects the gateway, the console, the agent or the evaluation.

You should get an acknowledgement when I see it. This is a personal project, so there is no
bug bounty and no service-level promise.

## Scope

The interesting parts are the MCP gateway auth, the approval token, tenant isolation, the
idempotency path and the audit log. Reports about those are welcome.

The local HS256 issuer, the SQLite store, the missing TLS and the lack of rate limiting are
known demo limits, documented in [docs/production-path.md](docs/production-path.md). They are
not vulnerabilities in this slice.

Never include real customer data, real documents or real keys in a report. The repository uses
a synthetic corpus for a reason.
