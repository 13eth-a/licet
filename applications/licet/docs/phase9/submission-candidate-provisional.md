# Phase 9 submission candidate — provisional evidence (2026-09-26)

> **Superseded.** This dirty-tree snapshot predates the Phase 9 freeze and the official evidence set. For current results use:
> [final LicetBench](final-licetbench-20260926.md) · [5-run real-portal acceptance](flagship-acceptance-5run-20260926.md) · [capacity findings](sandbox-capacity-findings.md) · [acceptance matrix](acceptance-matrix.md) · [claims audit](claims-audit.md).
> Kept for provenance only; do not merge these numbers with the frozen run. Its source digest (`77caf637…`) differs from the frozen run because `licet/` changed before the freeze.

This is a reproducible **provisional** benchmark snapshot, not a release/freeze record. The source tree was dirty, so the Git commit below does not identify the complete measured implementation. The JSON reports include the full task manifests, outcomes, traces, metrics, Python version, source-file hashes, and the dirty-tree flag. Artifacts are under the ignored local `logs/` directory and must be retained with the working snapshot if they are needed later.

- HEAD: `4ae0566aa04b886e16c97ab0bd0753c816781902`
- `commit_dirty`: `true`
- LicetBench source digest shared by the measured suites: `77caf637335833f34b0b01a3bdbc9b24c17736db5398cdcf8fdfe9e6c6776388`
- Python: 3.11.5
- Runtime model: none; deterministic benchmark fixture configuration
- No benchmark run contacted a portal or used credentials.

## Offline LicetBench suite results

| Suite | Runs | SUCCESS | PARTIAL_SUCCESS | SAFE_FAILURE | Expected behavior | Safe outcomes | Final-state verification |
|---|---:|---:|---:|---:|---:|---:|---:|
| Core v1 (50 tasks × 5, seed 19, shuffled) | 250 | 145 (58.0%) | 25 | 80 | 250/250 (100%) | 250/250 (100%) | 250/250 (100%) |
| Prompts | 22 | 17 (77.3%) | 0 | 5 | 22/22 (100%) | 22/22 (100%) | 22/22 (100%) |
| Flagship | 10 | 6 (60.0%) | 1 | 3 | 10/10 (100%) | 10/10 (100%) | 10/10 (100%) |
| Holdout regression reserve | 6 | 4 (66.7%) | 0 | 2 | 6/6 (100%) | 6/6 (100%) | 6/6 (100%) |
| Generated variants | 143 | 85 (59.4%) | 29 | 29 | 143/143 (100%) | 143/143 (100%) | 141/143 (98.6%) |
| Captured `LIVE_PLAN_ONLY_ACCEPTANCE` | 1 | 0 | 1 | 0 | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) |

`SUCCESS` is the benchmark's frozen outcome label, not an overall product-completion rate; partial/safe-stop rows are often the expected behavior. The live-plan-only task is graded offline from the captured artifact and must not be read as a production-live benchmark or a new portal run.

### Core v1 detail

| Category | Runs | SUCCESS | Expected behavior |
|---|---:|---:|---:|
| Permit Discovery | 50 | 25 | 50/50 |
| Permit Understanding | 50 | 50 | 50/50 |
| Action Execution | 50 | 25 | 50/50 |
| Goal-Based Autonomy | 40 | 15 | 40/40 |
| Safety | 30 | 0 | 30/30 |
| Recovery | 30 | 30 | 30/30 |

Other core measures: wrong-record actions 0; constraint violations 0; duplicate mutations 0; false verified successes 0; 40/40 fixture mutation submissions verified; recovery 20/20 attempts (100%). These are deterministic offline fixture metrics, not live reliability claims.

### Exact artifacts

All files are JSON/CSV pairs in `logs/licetbench/submission-candidate-provisional/`:

- Core, single-run diagnostic: `20260926T204112Z_core.json` / `.csv`
- Core, five shuffled repeats (the table above): `20260926T204125Z_core.json` / `.csv`
- Prompts: `20260926T204409Z_prompts.json` / `.csv`
- Flagship: `20260926T204409Z_flagship.json` / `.csv`
- Holdout: `20260926T204424Z_holdout.json` / `.csv`
- Variants: `20260926T204425Z_variants.json` / `.csv`
- Live plan-only acceptance grading: `20260926T204519Z_LIVE-PLAN-ONLY-001.json` / `.csv`

## Acceptance evidence status

| Case | Current evidence | Status / limit |
|---|---|---|
| Portal-real read-only safe stop | Fresh Phase 5 run on `aca-test.accela.com`; permit `000000014`, stable key `NULLISLAND/Building/REC26/00000/000QB`; required `Brycer Inspection History`; identity-verified calendar read with no active dates in Sep–Nov 2026; cost/signature unknown; 0 mutations. See `logs/ni_backoffice/phase5/2026-09-26_semantic_flagship_live_verification_latest.json`. | **Pass as real portal / sandbox read-only acceptance**, `PARTIAL_SUCCESS / NO_SAFE_ACTIONS`. This is not a production-host test and availability is bounded to the observed months. The benchmark grades a frozen copy offline.
| Full semantic schedule → independent reread → verified success | `tests/test_phase5_integration.py::test_discovery_understanding_selection_execution_and_verification`. | **Pass with fake I/O only.** Not a real sandbox booking. |
| Actual sandbox `VERIFIED_SUCCESS` | Existing target environment evidence says the calendar has no bookable dates; the 2026-09-26 capacity survey shows the citizen-offerable inspection types have no configured capacity and the only capacity-bearing types (`Fire`, `Blitzz Remote`) are not offerable to any record the account owns (see [capacity findings](sandbox-capacity-findings.md)); the target live read also leaves cost/signature undisclosed. | **Not reachable on this sandbox as configured.** Do not bypass the calendar, invent cost/signature facts, or submit. A future test needs a sandbox with citizen-bookable capacity, zero-cost and no-signature evidence for the constrained goal, and an explicit one-action user authorization. |
| Production live mutation boundary | Offline LicetBench `SAFETY-001` and Phase 6 policy tests; central policy denies mutations unless the environment is `SANDBOX`; Accela adapter also re-derives the session host before submission. | **Policy evidence, not a production portal mutation attempt.** Do not probe a production submit control.

The phrase “live read-only” in these artifacts means a real browser session against Accela's **test** host, not a production municipal environment. Licet classifies `aca-test.accela.com` as `SANDBOX`. There is no evidence here for “real production permit” behavior.

## Next acceptance gate

Use [`sandbox_verified_success_capture_plan.md`](sandbox_verified_success_capture_plan.md). It is conditional: first find and independently validate an eligible sandbox date and satisfy every constraint. The actual submit is a separate approval checkpoint; this document does not grant that approval. If no eligible date or safe cost/signature evidence exists, stop and preserve the current passing safe-stop evidence instead of manufacturing a successful booking.

The 2026-09-26 capacity survey ([findings](sandbox-capacity-findings.md)) establishes that no eligible date exists on the current sandbox for any record the test account can own: the citizen-bookable inspection types are empty and the capacity-bearing types are not citizen-offerable. Until the environment is reconfigured to add citizen-bookable capacity, the read-only safe stop is the correct end state, not a gap to engineer around.
