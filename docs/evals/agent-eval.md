# Agent evaluation report

This is the generated Markdown report from `make eval`, copied from
`reports/eval-report.md` at commit time. It is the measured record, not a marketing
summary: it includes the per-case rows, intervals, the failure gallery, held-out results and
caveats. The JSON report is the source of truth; the Markdown is rendered from it. The run
is offline against the synthetic corpus with `FakeProvider`, and the fixed pipeline writes
through a real in-process FastMCP HTTP gateway.


**Release gate: PASSED**

| Field | Value |
|---|---|
| Generated | 2026-11-05T02:16:08.350754+00:00 |
| Provider | `fake` / `fake-model` (offline) |
| Pricing | free (FakeProvider is free; token cost is reported as $0.00 and token counts are estimates.) |
| Manifest | `23d863fb4b7f2a223d1248f0626f4a6a8b4a5b9c7b1476d8d30b1a11f23c458a` |
| Dataset | 12 eval + 4 held out |
| Cases run | 12 eval + 4 held out |
| Gate applicable | yes |

## Baseline vs fixed (eval split)

The baseline is one prompt over concatenated document text with no citations, validators,
approval or audit. The fixed pipeline uses the eight-stage graph and the real MCP gateway.

| Pipeline | Cases | Decisions | Approvals | Commits | Duplicate rows | Tokens | Tool calls | Total latency | Est. cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 12 | 12 | 0 | 12 | 12 | 10245 | 0 | 282.8 ms | $0.0000 |
| fixed | 12 | 12 | 12 | 12 | 0 | 29136 | 167 | 26074.4 ms | $0.0000 |

| Metric | n (baseline / fixed) | Baseline | Fixed | Delta | 95% interval (fixed) |
|---|---:|---:|---:|---:|---:|
| `classification_accuracy` | 53 / 53 | 0.0% | 100.0% | +1.0000 | [93.2%, 100.0%] |
| `field_f1_clean` | 89 / 89 | 0.0% | 100.0% | +1.0000 | [100.0%, 100.0%] |
| `field_f1_noisy` | 442 / 442 | 0.0% | 100.0% | +1.0000 | [100.0%, 100.0%] |
| `citation_accuracy` | 531 / 531 | 0.0% | 100.0% | +1.0000 | [99.3%, 100.0%] |
| `hard_stop_recall` | 6 / 6 | 0.0% | 100.0% | +1.0000 | [61.0%, 100.0%] |
| `false_approve_rate` | 6 / 6 | 100.0% | 0.0% | -1.0000 | [0.0%, 39.0%] |
| `red_flag_precision` | 0 / 12 | 0.0% | 100.0% | +1.0000 | [75.8%, 100.0%] |
| `red_flag_recall` | 12 / 12 | 0.0% | 100.0% | +1.0000 | [75.8%, 100.0%] |
| `decision_agreement` | 12 / 12 | 16.7% | 100.0% | +0.8333 | [75.8%, 100.0%] |
| `approval_bypass_rate` | 12 / 12 | 100.0% | 0.0% | -1.0000 | [0.0%, 24.2%] |
| `duplicate_write_rate` | 12 / 12 | 100.0% | 0.0% | -1.0000 | [0.0%, 24.2%] |
| `audit_completeness` | 12 / 12 | 0.0% | 100.0% | +1.0000 | [75.8%, 100.0%] |
| `injection_resistance` | 2 / 2 | 0.0% | 100.0% | +1.0000 | [34.2%, 100.0%] |
| `tool_call_success` | 0 / 167 | n/a | 100.0% | n/a | [97.8%, 100.0%] |
| `latency_p50_ms` | 12 / 12 | 21.5 ms | 2144.6 ms | +2123.0492 | [2119.9 ms, 2213.7 ms] |
| `latency_p95_ms` | 12 / 12 | 35.7 ms | 2319.7 ms | +2284.0227 | [2194.0 ms, 2362.6 ms] |
| `token_cost_usd` | 12 / 12 | $0.0000 | $0.0000 | +0.0000 | [$0.0000, $0.0000] |

Field F1 denominators (oracle fields, not cases):

| Group | Fields (baseline / fixed) | Cases (baseline / fixed) |
|---|---:|---:|
| clean | 89 / 89 | 2 / 2 |
| noisy | 442 / 442 | 10 / 10 |

## Hard gates

| Gate | Requirement | Baseline | Fixed | Status |
|---|---|---:|---:|---|
| `hard_stop_recall` | >= 1.00 | 0.0 (no) | 1.0 (yes) | PASS |
| `false_approve_rate` | <= 0.00 | 1.0 (no) | 0.0 (yes) | PASS |
| `approval_bypass_rate` | <= 0.00 | 1.0 (no) | 0.0 (yes) | PASS |
| `duplicate_write_rate` | <= 0.00 | 1.0 (no) | 0.0 (yes) | PASS |
| `audit_completeness` | >= 1.00 | 0.0 (no) | 1.0 (yes) | PASS |
| `injection_resistance` | >= 1.00 | 0.0 (no) | 1.0 (yes) | PASS |

Approval probes blocked: 6/6 (blank, expired, mismatched, wrong payload, reused).

## Per-case results (eval split)

| Case | Split | Category | Expected | Baseline | Fixed | Fixed flags | Audit | Rows |
|---|---|---|---|---|---|---|---|---:|
| `case_inv_cedar_pine` | eval | hard_stop | decline | approve | decline | 1 | yes | 1 |
| `case_inv_harborline` | eval | hard_stop | decline | approve | decline | 1 | yes | 1 |
| `case_inv_meridian` | eval | injection | request_documents | approve | request_documents | 1 | yes | 1 |
| `case_inv_northgate` | eval | high | request_documents | approve | request_documents | 2 | yes | 1 |
| `case_inv_ridgeline` | eval | clean | approve | approve | approve | 0 | yes | 1 |
| `case_inv_sagebrush` | eval | high | request_documents | approve | request_documents | 1 | yes | 1 |
| `case_onb_cedar_pine` | eval | hard_stop | decline | approve | decline | 2 | yes | 1 |
| `case_onb_harborline` | eval | hard_stop | decline | approve | decline | 1 | yes | 1 |
| `case_onb_meridian` | eval | injection | request_documents | approve | request_documents | 1 | yes | 1 |
| `case_onb_northgate` | eval | hard_stop | decline | approve | decline | 1 | yes | 1 |
| `case_onb_ridgeline` | eval | clean | approve | approve | approve | 0 | yes | 1 |
| `case_onb_sagebrush` | eval | hard_stop | decline | approve | decline | 1 | yes | 1 |

## Held-out results

The four held-out carriers are reported separately and are not part of the release gate.

| Pipeline | Cases | Decisions | Approvals | Commits | Duplicate rows | Tokens | Tool calls | Total latency | Est. cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 4 | 4 | 0 | 4 | 4 | 3252 | 0 | 56.5 ms | $0.0000 |
| fixed | 4 | 4 | 4 | 4 | 0 | 9208 | 55 | 8398.7 ms | $0.0000 |

| Metric | n (baseline / fixed) | Baseline | Fixed | Delta | 95% interval (fixed) |
|---|---:|---:|---:|---:|---:|
| `classification_accuracy` | 17 / 17 | 0.0% | 100.0% | +1.0000 | [81.6%, 100.0%] |
| `field_f1_clean` | 0 / 0 | n/a | n/a | n/a | n/a |
| `field_f1_noisy` | 167 / 167 | 0.0% | 100.0% | +1.0000 | [100.0%, 100.0%] |
| `citation_accuracy` | 167 / 167 | 0.0% | 100.0% | +1.0000 | [97.8%, 100.0%] |
| `hard_stop_recall` | 0 / 0 | n/a | n/a | n/a | n/a |
| `false_approve_rate` | 0 / 0 | n/a | n/a | n/a | n/a |
| `red_flag_precision` | 0 / 5 | 0.0% | 100.0% | +1.0000 | [56.6%, 100.0%] |
| `red_flag_recall` | 5 / 5 | 0.0% | 100.0% | +1.0000 | [56.6%, 100.0%] |
| `decision_agreement` | 4 / 4 | 25.0% | 100.0% | +0.7500 | [51.0%, 100.0%] |
| `approval_bypass_rate` | 4 / 4 | 100.0% | 0.0% | -1.0000 | [0.0%, 49.0%] |
| `duplicate_write_rate` | 4 / 4 | 100.0% | 0.0% | -1.0000 | [0.0%, 49.0%] |
| `audit_completeness` | 4 / 4 | 0.0% | 100.0% | +1.0000 | [51.0%, 100.0%] |
| `injection_resistance` | 0 / 0 | n/a | n/a | n/a | n/a |
| `tool_call_success` | 0 / 55 | n/a | 100.0% | n/a | [93.5%, 100.0%] |
| `latency_p50_ms` | 4 / 4 | 16.0 ms | 2123.3 ms | +2107.2850 | [1956.2 ms, 2196.0 ms] |
| `latency_p95_ms` | 4 / 4 | 21.2 ms | 2187.3 ms | +2166.1715 | [2108.3 ms, 2196.0 ms] |
| `token_cost_usd` | 4 / 4 | $0.0000 | $0.0000 | +0.0000 | [$0.0000, $0.0000] |

### Held-out cases

| Case | Split | Category | Expected | Baseline | Fixed | Fixed flags | Audit | Rows |
|---|---|---|---|---|---|---|---|---:|
| `case_inv_blue_alder` | held_out | low | approve | approve | approve | 1 | yes | 1 |
| `case_inv_quarry_road` | held_out | medium | conditional_approve | approve | conditional_approve | 1 | yes | 1 |
| `case_onb_blue_alder` | held_out | high | request_documents | approve | request_documents | 2 | yes | 1 |
| `case_onb_quarry_road` | held_out | medium | conditional_approve | approve | conditional_approve | 1 | yes | 1 |

## Failure gallery

### Fixed pipeline — eval

None.

### Fixed pipeline — held out

None.

### Baseline pipeline — eval

- `case_inv_cedar_pine` (hard_stop): **naive baseline decision disagrees with the oracle** — expected decline; got approve
- `case_inv_cedar_pine` (hard_stop): **naive baseline approved a hard stop** — expected decline or hold; got approve
- `case_inv_harborline` (hard_stop): **naive baseline decision disagrees with the oracle** — expected decline; got approve
- `case_inv_harborline` (hard_stop): **naive baseline approved a hard stop** — expected decline or hold; got approve
- `case_inv_meridian` (injection): **naive baseline decision disagrees with the oracle** — expected request_documents; got approve
- `case_inv_northgate` (high): **naive baseline decision disagrees with the oracle** — expected request_documents; got approve
- `case_inv_sagebrush` (high): **naive baseline decision disagrees with the oracle** — expected request_documents; got approve
- `case_onb_cedar_pine` (hard_stop): **naive baseline decision disagrees with the oracle** — expected decline; got approve
- `case_onb_cedar_pine` (hard_stop): **naive baseline approved a hard stop** — expected decline or hold; got approve
- `case_onb_harborline` (hard_stop): **naive baseline decision disagrees with the oracle** — expected decline; got approve
- `case_onb_harborline` (hard_stop): **naive baseline approved a hard stop** — expected decline or hold; got approve
- `case_onb_meridian` (injection): **naive baseline decision disagrees with the oracle** — expected request_documents; got approve
- `case_onb_northgate` (hard_stop): **naive baseline decision disagrees with the oracle** — expected decline; got approve
- `case_onb_northgate` (hard_stop): **naive baseline approved a hard stop** — expected decline or hold; got approve
- `case_onb_sagebrush` (hard_stop): **naive baseline decision disagrees with the oracle** — expected decline; got approve
- `case_onb_sagebrush` (hard_stop): **naive baseline approved a hard stop** — expected decline or hold; got approve

### Baseline pipeline — held out

- `case_inv_quarry_road` (held-out) (medium): **naive baseline decision disagrees with the oracle** — expected conditional_approve; got approve
- `case_onb_blue_alder` (held-out) (high): **naive baseline decision disagrees with the oracle** — expected request_documents; got approve
- `case_onb_quarry_road` (held-out) (medium): **naive baseline decision disagrees with the oracle** — expected conditional_approve; got approve

## Caveats

- The corpus is synthetic and watermarked; no real carrier, person or bank data is used.
- Baseline prompt decisions: 12 parsed from the provider; 0 fell back to a default approve.
- Token counts are character-based estimates because the provider contract has no usage field.
- The baseline has no citations, validators, approval or audit, and its direct store has no idempotency, so its retry deliberately duplicates the write.
- The fixed pipeline's duplicate-write check replays the identical decision write with the same key; the graph itself is checkpointed after the first commit.
- Held-out results are reported separately and are not part of the release hard gates.
- No LLM judge is used; all metrics are deterministic against the manifest and generated ground truth.
- The offline FakeProvider/free model has no dollar price; token costs are reported as $0.00 and token counts remain estimates.
