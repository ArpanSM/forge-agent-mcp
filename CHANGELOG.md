# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses the tags in the
repository.

## Unreleased

### Changed

- The README is now a project page: outcome, proof, architecture, quickstart and limits. The
  client offer moved out of the README.
- The case study points at screenshots and the production path instead of ending with a sales
  pitch.
- Added a production-path note that lists the gap between the local demo and a real deployment.

### Added

- CI on GitHub Actions: lint, offline tests and the evaluation gates.
- A Docker demo stack and `compose.yaml`.
- An MCP registry metadata draft in `server.json` (not published).
- Contributor guide, security policy, changelog, issue templates and a pull-request template.

### Removed

- Public links to the demo videos while they are re-cut. The files stay in `demo-video/` and
  the release assets are unchanged.

## v0.1.1-demo - 2026-11-05

### Added

- Proof assets: screenshots, an evaluation summary and a narrated walkthrough.
- The six release gates and the approval probes in the evaluation report.

## v0.1.0 - 2026-11-05

### Added

- The synthetic freight corpus, extraction with citations and the deterministic rule engine.
- The FastMCP gateway with scopes, tenant checks, approval tokens, idempotency and an
  append-only audit log.
- The LangGraph case runner and the reviewer console.
- The evaluation harness, baseline and generated report.
