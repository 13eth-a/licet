# Final LicetBench — official submission run (2026-09-26)

Offline, deterministic LicetBench v1 core suite. No model calls, no portal
contact. One official run.

## Provenance

| Field | Value |
|---|---|
| Benchmark | `licetbench-v1` |
| Suite / protocol | core, 50 tasks × 5 repeats = 250 runs, shuffled, seed 19 |
| Model / config | `fixture` / `deterministic` (no model is invoked) |
| Python | 3.11 |
| Revision | `9a6a424810afcf887cd6507a7517b2b5378b5701` (Phase 9 freeze) |
| `commit_dirty` | **false** |
| Source digest (`licet` + `licetbench`) | `379537e165181a5bb707cbb524ef57be822b82a4b3be4c2ba0015f9e5bfc6410` |

The run is **commit-identified**: it executed on the frozen revision with a clean
tree. The source digest differs from the earlier provisional run (`77caf637…`)
because `licet/` changed before the freeze. A pre-freeze validation run produced
identical numbers.

## Headline

| Metric | Value |
|---|---:|
| Tasks / runs | 50 / 250 |
| Completed (`SUCCESS` label) | 145 (58.0%) |
| Expected behavior met | 250 / 250 (100%) |
| Safe outcomes | 100% |
| Final-state verification | 100% |
| Partial success | 25 |
| Safe failures | 80 |
| **Unsafe failures** | **0** |
| Grader errors / integrity violations | 0 / 0 |
| Mutation submissions verified | 40 / 40 (100%) |
| Recovery success | 20 / 20 (100%) |

`SUCCESS` is the benchmark's frozen outcome label, **not** an overall
product-completion rate. In this suite many correct outcomes are `SAFE_FAILURE`
or `PARTIAL_SUCCESS` by design — e.g. all 30 Safety runs are correct refusals,
which is why that category shows 0 completions but 30/30 expected behavior.

## Category breakdown

| Category | Runs | Completed | Partial | Safe failures | Expected behavior |
|---|---:|---:|---:|---:|---:|
| Permit Understanding | 50 | 50 | 0 | 0 | 50/50 |
| Recovery | 30 | 30 | 0 | 0 | 30/30 |
| **Safety** | 30 | 0 | 0 | 30 | 30/30 |
| Permit Discovery | 50 | 25 | 0 | 25 | 50/50 |
| Action Execution | 50 | 25 | 0 | 25 | 50/50 |
| Goal-Based Autonomy | 40 | 15 | 25 | 0 | 40/40 |

## Safety zero-targets (all 0)

`live_mutations=0`, `wrong_record_mutations=0`, `wrong_inspection_mutations=0`,
`constraint_violations=0`, `unconfirmed_risky_mutations=0`,
`duplicate_mutations=0`, `false_verified_successes=0`.

## Scope and limits

- Offline deterministic fixtures. This establishes regression and design
  coverage, **not** live, model, or broad municipal reliability.
- 40 fixture mutation submissions were independently reverified (100%); the
  dated capacity survey found no citizen-reachable capacity for an account-owned
  record in the surveyed sandbox configuration — see [capacity findings](sandbox-capacity-findings.md)
  and the [5-run acceptance](flagship-acceptance-5run-20260926.md). This does not
  establish that availability cannot later change.
- The 5 repeats measure repeatability of deterministic fixtures, not live stability.

## Artifact

- `logs/licetbench/final-phase9-frozen/20260927T011252Z_core.json` (ignored local
  artifact) — SHA-256 `70aa22c62743c60b3c81c78c2d01985e6b94bf664aeec8c9030d18bc5d183ea2`.
- Sibling `20260927T011252Z_core.csv`.
- Screenshot capture: [`screenshots/02_licetbench_summary.txt`](screenshots/02_licetbench_summary.txt).
