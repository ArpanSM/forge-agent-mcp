# Contributing

Thanks for looking. This is a small project and the bar is a clean change with a test.

## Before you start

- Read [AGENTS.md](AGENTS.md) and [CODING_STANDARDS.md](CODING_STANDARDS.md).
- All carriers, people, documents, phone numbers and bank details are synthetic. Do not add
  real data, real customer names or real keys.
- Open an issue for anything larger than a typo or a one-line fix.

## Local checks

```bash
make install
make test
make lint
make eval
```

`make test` runs offline. The evaluation uses `FakeProvider`, so it also runs offline. CI runs
the same three checks on every pull request.

## Pull requests

- Keep one topic per pull request.
- Say what changed, why, and what you ran.
- Link the issue it closes.
- If you change a decision recorded in an ADR, say so in the PR and update the ADR.
- Do not commit generated data, reports or secrets. They are ignored by `.gitignore`.

A maintainer reviews the change before merge. Be kind in review; assume the other person is
trying to make the project better.
