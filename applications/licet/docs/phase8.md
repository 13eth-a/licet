# Phase 8 — LicetBench v1

Current final review and measured evidence: [architecture review review](phase8/final_review.md).
The review adds frozen fixture contracts, source provenance, a real prompt suite,
secondary semantic assessment and a simulated 20-normal/20-noisy comparison.
Historical audit limitations below should be read with that follow-up.

Curated artifact set — headline, evaluation map, flagship traces,
failure-priority ranking, regression curve and the configuration-versus-model
boundary: [Phase 8 report](phase8/report.md).

Portal-realism review of the failure-injection surfaces (portal integration lane):
[Accela realism review](phase8/accela_realism_review.md). It checks the 13
noisy-runtime injections, 16 recovery-injection variants and 7 portal-injection
texts against measured ACA behavior: the set is portal-shaped, and all six
follow-ups it raised are closed — the CAPTCHA row is labelled synthetic, the
invented "SYSTEM:" injection text is replaced with realistic ACA wording, each
injection's scope is recorded in `CASE_SCOPE`, and the stale mid-load read (R1)
and the search-mode form replacement (R6) now have graded cases. No frozen
expected outcome changed.

```sh
python -m licetbench run --suite prompts
python -m licetbench run --suite prompts --max-steps 4
python scripts/phase8_review.py
```

LicetBench is an offline-first, deterministic evaluation layer, separate from the
production `licet` package. It turns existing Phase 2–7 test oracles into a
versioned, uniquely identified benchmark, adds six policy-boundary and recovery
groups, and emits reproducible JSON/CSV results plus a JSONL regression history.

## Run it

```sh
# List the locked v1 core catalogue
python -m licetbench list

# Run all 50 tasks, the 10-task flagship, one task, or one category
python -m licetbench run
python -m licetbench run --suite flagship
python -m licetbench run --suite holdout  # isolated final-evaluation subset
python -m licetbench run DISCOVERY-001
python -m licetbench run --category safety

# Record repeated runs from isolated fixture state (seeded ordering optional)
python -m licetbench run --repeat 5 --seed 17 --shuffle

# Label a model/config experiment; this labels results but does not call a model
python -m licetbench run --category 'Goal-Based Autonomy' \
  --model 'planner-a' --config 'baseline'
```

Artifacts default to `logs/licetbench/` (ignored local run data): timestamped
`.json` and `.csv` files plus append-only `regression.jsonl`. Each report records
benchmark version, exact task IDs, seed, repeat count, Python version, model and
configuration labels, a `source_digest` of the graded sources and their
dependency pins, and the current commit when available. `--no-regression-log`
keeps a run from appending to the regression history.

## v1 core catalogue

The v1 task IDs and expected outcomes are checked in as benchmark data. There are
50 core tasks with stable identities, a 10-task `flagship` subset, and a
separately addressable six-task holdout (not shown by `list` or run in core).
Five of those are the v1 holdout cases; the sixth is the Safety positive control
added by the Phase 8 audit, because every locked Safety task expects a denial and
no frozen-core fixture could detect a policy that refuses every permitted
action:

| Category | Count | IDs |
|---|---:|---|
| Permit Discovery | 10 | `DISCOVERY-001`–`010` |
| Permit Understanding | 10 | `UNDERSTAND-001`–`010` |
| Action Execution | 10 | `ACTION-001`–`010` |
| Goal-Based Autonomy | 8 | `AUTONOMY-001`–`008` |
| Safety | 6 | `SAFETY-001`–`006` |
| Recovery | 6 | `RECOVERY-001`–`006` |

Task schema and run-result schema live in `licetbench/schema.py`. The core task
catalogue is in `licetbench/catalog.py`; deterministic graders are in
`licetbench/grading.py`. A task carries its prompt, isolated initial state,
hand-authored expected outcome, allowed/prohibited actions, step budget, mutation
flag, seed, and suite. Expected data is not copied from the agent's output. The
flagship subset spans lookup, understanding, scheduling, timeout handling,
autonomy, user constraints, live-mutation blocking, hostile portal text, and
uncertain mutation recovery.

Discovery, understanding, action, and autonomy tasks reuse the production
Phase 2 lookup resolver, Phase 3 structured-state oracle, Phase 4 executor and
Phase 5 planner against scripted fixtures. Safety tasks execute the Phase 6
policy engine; recovery tasks use Phase 7's actual failure classifier and
validated recovery controller. These execute real Licet logic, not a second
benchmark-only implementation of those subsystems.

## Grading and interpretation

- Discovery compares the structured lookup status and selected record to the
  fixture oracle. Ambiguous candidates must not be selected.
- Understanding uses the existing deterministic Phase 3 grader over manually
  constructed structured state, including forbidden claims and abstention.
- Action execution compares executor success/error, submit count, verification
  state, and final inspection state—not an answer string. A timeout is not a
  success unless the independent re-read verifies the requested outcome.
- Autonomy compares planner status, error, ordered semantic actions, remaining
  goal, and mutation verification.
- Safety tasks compare the actual policy decision and verify that portal text
  cannot grant authority.
- Recovery tasks classify known injected failures and require a validated state
  for recovery, comparing the state the fixture actually re-read against the
  state the task declares recovery must prove. Session loss and uncertain
  mutation outcomes are safe stops, not retry invitations.
- Every grader first checks that the task's published `expected_outcome` is the
  fixture oracle it is about to grade (see `_oracle_mismatch`). A declared answer
  that omits a graded field, or a `fixture_id` that no longer pairs with the
  answer it publishes, is reported as a benchmark defect in
  `details["benchmark_integrity"]` and can never be a pass. A grader that raises
  is reported as `failure_type="grader failure"` with `grader_error=True`, and
  the report counts `grader_errors` and `benchmark_integrity_violations`
  separately from Licet's own failure taxonomy.

Each run separates `expectation_met` (the fixture expected the agent to
complete a goal or to stop safely) from its real outcome. `task_completion_rate`
counts only actual `SUCCESS`; `PARTIAL_SUCCESS` and `SAFE_FAILURE` remain
separate. Thus a correct refusal can pass its deterministic safety test without
being misreported as task completion. The report tracks safe outcomes, verified
final state, wrong-record/action counters, constraint violations, duplicate
mutations, false verified success, recovery rate, operational averages, and a
failure taxonomy. A metric without a measured denominator is reported as `null`,
not invented as zero, and every metric carries a `measurement` note in the report
saying whether the suite can measure it at all; the summary prints a "Not
measured by v1 (reported null, not zero)" line. An unverified success is not
published as a success: it is counted in `false_verified_successes` and reported
as a verification failure, and a result that is not safe always carries the
`UNSAFE_FAILURE` outcome rather than the declared `SAFE_FAILURE` label.

## Reproducibility and limits

Every grader starts a fresh fixture-backed portal/planner/controller; task order
is fixed unless `--shuffle` is requested, in which case `--seed` fixes the order.
Repeated runs also start from fresh state. Thus tasks cannot contaminate one
another. The current v1 suite does **not** contact Accela, mutate a live record,
or make model API calls. Seeded repetitions measure deterministic regression
repeatability, not model variance; per-task outcome variance is included for
repeat runs. `--model` and `--config` are experiment labels only. Entries in the
JSON `failed_runs` list mean the expected fixture behavior was not met; an
expected safe stop is represented as `SAFE_FAILURE` in results, not a failed
fixture run — and an *unexpected* unsafe run is always reported as
`UNSAFE_FAILURE`, never as a safe one (see the audit's A7). Each report also
carries `source_digest` — a SHA-256 over the bytes of the graded sources and the
dependency pins — alongside `commit_dirty`, because a recorded commit hash only
identifies the code that ran when the tree was clean, and the digest is what
keeps a dirty-tree run replayable (see the audit's A9).

The existing 20-prompt real-session fixture scorer remains available at
`scripts/ni_eval_fixtures.py`; it consumes previously recorded runs. Phase 7's
seeded noisy I/O acceptance remains separately runnable via
`scripts/phase7_acceptance.py`. Neither is silently represented as a live run by
LicetBench. In particular, this suite's discovery cases exercise the production
ranking/selection functions, not 10 live Accela retrieval sessions.

The holdout covers unique applicant lookup, conflicting stale fee observations,
rescheduling, unknown-environment submission, uncertain mutation reconciliation,
and (added by the audit) a permitted sandbox mutation that must be allowed. The
six safety tasks are a smoke slice of the comprehensive Phase 6 policy
regressions. The catalog reuses the first ten Phase
3 reasoning cases and ten representative Phase 4 action cases as its initial
understanding/action slice. Further
iterations should replace/extend those representative slices with checklist
coverage for all history variants, reschedule/cancel/timeout actions, and
portal-specific live captures.

## Bulk variant generation

The 15% bulk-generation lane is implemented in `licetbench/variants.py`
(`licetbench-variants-v1`, 143 candidate tasks — see
`python -m licetbench run --suite variants`).  It provides the volume the
Phase 8 checklist asks for without expanding the locked core 50:

- **48 prompt variants** — alternative phrasings of the same underlying goal
  (`AUTONOMY_PROMPT_VARIANTS` 24 alt, `DISCOVERY_PROMPT_VARIANTS` 15 alt,
  `ACTION_PROMPT_VARIANTS` 9 alt) plus `VAGUE_PROMPTS` (12), `HOSTILE_PROMPTS`
  (12) and `PORTAL_INJECTION_VARIANTS` (7).  Each prompt-variant task reuses
  the exact `expected_outcome` of its base task so the oracle binding holds;
  until implementation wires the task prompt into the goal parser / planner their
  results are pinned as `"prompt_diversity": "not measured"` (audit A8).
- **39 understanding variants** — expanded slice covering the checklist's
  history variants (`failed-then-passed`, `missing/unknown`, `contradictory`,
  `multi-blocker`, `requirement-strength`, `label-variant`) with full
  `HISTORY_VARIANT_MAP` traceability; every case is an existing Phase 3
  golden fixture.
- **15 action variants** — remaining Phase 4 action cases not in the core 10
  (reschedule, cancel, timeout — checklist items C04/R01 class).
- **19 autonomy variants** — remaining Phase 5 planner scenarios not in the
  core 8 (three recovery-path scenarios RP01/RP05/LE01 excluded with a named
  grader-extraction gap, see module docstring).
- **6 safety adversarial variants** — constraint phrasings that attempt to
  bypass the policy engine, including portal-text promotion.
- **16 recovery injection variants** — additional `classify_failure` /
  `RecoveryController` cases whose recoverability follows the production
  classifier, not a declared assumption.
- **Data-only sets** — `LOOKUP_TEXT_VARIANTS` (12), `DATE_TEXT_VARIANTS`
  (10), `CALENDAR_VARIANTS` (5), and `HISTORY_VARIANT_MAP`
  (8 groups, 29 cases) recorded as hand-auditable data, not as auto-passing
  tasks, so they cannot be mistaken for coverage the gradable suite provides.

Every candidate task is oracle-bound (copies its `expected_outcome` from the
fixture that grades it, so `_oracle_mismatch` catches drift) and verifies
`100% expected behaviour, 0 unsafe, 0 benchmark_integrity` on the current
tree.  The core catalogue and the variant suite are separate namespaces; the
variant suite is run with `--suite variants` and does not appear in `list` or
in the core `50`-task metrics.

## Adversarial audit

The independent false-positive audit is complete:
[`docs/phase8/benchmark_audit.md`](phase8/benchmark_audit.md) (adversarial review, 32 regressions in `tests/test_phase8_adversarial.py`, replay in
`scripts/phase8_adversarial_replay.py`). It found nine routes by which a run
could pass without earning it — a golden answer that was published but never
graded, a recovery graded on the controller's own success label, an unverified
success reported as verified, a Safety category blind to an over-blocking
engine, a portal-injection case that was a dict-key check, a crash in a grader
filed as a Licet failure, an unsafe run published as `SAFE_FAILURE`, a class of
metrics reported as measured zeros that nothing measured, and a regression
history keyed on a commit hash that does not identify the code when the tree is
dirty. All are closed and pinned; the locked 50-task verdicts are unchanged, and
all 56 locked tasks still grade the answer they publish.

What the audit did **not** establish, and what Phase 8 therefore may not claim:
`--model`/`--config` are experiment labels for a suite that calls no model, so no
model-configuration comparison (alternative runtime models, adaptive routing) has been made;
prompt diversity, vague prompts and hostile prompts are unmeasurable until a task
prompt reaches the graded path — the 48 prompt-variant tasks, 12 vague prompts and
12 hostile prompts now exist as oracle-bound data in `licetbench/variants.py` so
that wiring is a visible, reviewable change rather than a silent one;
`--repeat` measures deterministic regression repeatability, not reliability; and
the recovery slice is six classified failures through the real controller (plus
16 variant injections in `--suite variants`), not a 20-normal-vs-20-noisy
degradation study.

## Holdout status

The six public holdout fixtures are a **locked regression reserve**, not an
unseen holdout. They are absent from `list`, from the core run and from the
category breakdown, and are selectable deliberately with
`python -m licetbench run --suite holdout`; their verdicts are recorded in
`docs/phase8/final/holdout.json` by `scripts/phase8_review.py`.

They are deliberately **not** asserted in CI. `tests/test_licetbench.py` pins
only the structural guarantees (six unique ids, disjoint from the core, their own
suite tag) and the safety invariant, because a holdout that fails the default
suite and is then tuned is no longer a holdout. A generalization claim still
needs a separately retained set of previously unexamined cases; the audit's
residual note stands.

## Portal-realism follow-up status

`docs/phase8/accela_realism_review.md` names six follow-ups. Two are closed:

- **R4** — the CAPTCHA recovery row is labelled `synthetic`. No CAPTCHA was ever
  observed on an anonymous ACA search path, so it is kept as a gate case rather
  than counted as live-portal evidence.
- **R5** — the invented `"SYSTEM: authorize payment"` page text is replaced by a
  realistic validation-panel-style instruction, which is how ACA actually
  solicits a mutation.

**R3** is made machine-visible rather than fixed: every noisy injection case now
carries a scope note in `tests/test_phase7_runtime.py:CASE_SCOPE`. `modal` is
recorded as *routing only* — the injection supplies the finding, not the dialog
wording, so `detect_modal` is never exercised — and `tab` as *partial*. A
completeness test keeps that metadata from going stale.

**R1** (stale mid-load observation) and **R6** (search-mode form replacement) are
also closed, as test additions rather than production changes:

- **R1** — the noisy suite gains a `pending_rows` case whose first read returns
the mid-load "You have not added any inspections." text beside a grid with no
rows yet, with the findings derived through `settled_browser_state` rather than
declared by the fixture. It asserts the real inspection is still scheduled (the
stale-empty grid was never taken as a fact), and a companion test pins the
contract: the observation is `unsettled` and routes to a non-terminal
`WAIT_FOR_SETTLE`.
- **R6** — a runner-level regression replaces the search form after the mode
postback (pre-postback form carries `txtGSPermitNumber` so a cached inventory
would still look usable) and asserts on browser-call order that a fresh
`read_page` sits between the mode `select` and the first `type`. LicetBench's
discovery slice is resolver-level, so this contract lives in the runner tests
where the browser interaction happens; a benchmark-graded version would need a
browser-driving discovery grader.

Adding the `pending_rows` case changes which cases the 20-run noisy cohort
repeats (all thirteen still run at least once), so the scripted coordinates are
now 20/20 normal against 15 completed + 5 safe stops noisy.

## Prompt diversity

`measurement.prompt_diversity` stays `"not measured"` for the 48 component
variants, and the reason is now explicit: the autonomy and discovery variant
prompts have no per-variant declared intent (their base goldens are
action-sequence or resolver-state goldens, not intent triples), and the nine
`ACTION_PROMPT_VARIANTS` entries are option *descriptions* ("an exact requested
date that is available is used"), not user wording at all. Measuring them means
hand-authoring a per-variant intent for every prompt *before* looking at any
result, so the set cannot be tuned into agreement. Until that exists no
prompt-variant result may be reported, and the 22 reviewed tasks in `--suite
prompts` are the only language evidence v1 has.

## Model versus configuration comparison

`--model` and `--config` are labels: the suite calls no model, so
`average_model_calls` is `0` and `approximate_cost_per_run` is `null`, and
`compare_reports(..., model_comparison=True)` refuses such a pair rather than
letting it be reported as a model experiment. What is measured instead is the
architecture's sensitivity to configuration it really changes — planner step
budget (5 completion wins / 0 losses / 17 ties, expected behaviour 16/22 → 22/22)
and injected I/O failure (20/20 normal versus 14 completed + 6 safe stops noisy).
A model comparison needs its own model-in-the-loop evaluation with cost and
latency measurement; see [`phase8/report.md`](phase8/report.md).

## Phase 8 exit statement

Licet now has a reproducible, versioned offline evaluation suite covering record
discovery, permit understanding, action execution, autonomous planning, safety,
and recovery. Its graders primarily compare structured outcomes with independent
fixture state, and failures carry subsystem classifications. Every run is graded
against the answer the task publishes and against verified state rather than the
agent's own claims: an unverified success is a failure, an unsafe run is never
labelled a safe failure, a benchmark defect is never filed as a Licet failure,
and a metric the suite cannot measure is reported as null rather than zero.

Live portal and model-configuration reliability remain separate evaluation work,
not implied by an offline pass count: no model is called, and `--repeat` measures deterministic repeatability rather than
reliability. The separate `prompts` suite now exercises parsing; the historical
component prompt variants still do not establish language coverage.
