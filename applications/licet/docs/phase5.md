# Phase 5 — goal-based semantic planner

## Status: closed 2026-09-22 — code-complete, one accepted live-tooling limitation

Phase 5 is signed off as code-complete. The goal schema/parser, planner state
machine, closed action vocabulary with pre/postconditions, policy and
precondition gates, immutable constraints, approval pauses, replanning, budgets,
loop/progress detection, traces, metrics, the model boundary, and the 30-case
deterministic planner set are implemented and regression-locked (927 tests).

The live stack was wired and run: the planner drives the real Phase 2 lookup →
Phase 3 read/reason → Phase 4 selection/preflight path and stops safely where the
live tool layer refuses, with **no mutation attempted**. The live exit-condition
leg (ten consecutive flagship runs) is therefore **not** demonstrated and is
recorded as an accepted environment/tooling limitation — the same posture Phase 4
took for its live booking leg. Item-by-item status is in
`docs/phase5/checklist_audit.md`.

Implemented as `licet/phase5`, with the user-requested primary implementation
scope. This is an additional semantic coordinator; the original Phase 1 browser
planner remains available for its existing callers and tests.

## Runtime structure

`parse_goal` → immutable `Goal` → `GoalPlanner` → policy/preconditions →
`LicetCapabilities` → existing Phase 2/3/4 component → fresh observation → revised
short plan. An optional `ModelSelector` chooses only among the currently valid
semantic steps. It cannot supply browser actions, modify constraints, grant
approval, or declare success.

The model interface uses a closed function schema and runtime validation. It
follows the [official function-calling guidance](https://developers.openai.com/api/docs/guides/function-calling):
structured arguments constrain the output shape; application code still validates
and executes the proposed operation. Existing configured provider retries/fallback
remain in `licet.agent.model`; an explicit secondary selector client is also
supported. Obvious single-step decisions skip the model entirely.

## Checklist implementation

| Checklist area | Implementation |
|---|---|
| Goal, objective, target, dates, restrictions, autonomy, vague/conflicting inputs | `goals.py`, frozen `Goal` with immutable string tuples |
| Planner inputs | Goal, World, browser-state summary, completed/failed steps, allowed/confirmation action sets |
| Semantic vocabulary | Closed `Action` enum; no arbitrary tools, payments, signatures, or application submission |
| Plans/dependencies | Short revision horizons, at most eight steps, dependency validation; no speculative DOM sequence |
| Observe/select/policy/execute/verify/replan | `GoalPlanner`; new plan after every capability result |
| Preconditions | Verified record/snapshot, exact operation, target/date/constraint preservation, same-record evidence, eligibility/preflight |
| Postconditions and success | `established(World, Goal)`; every fact bound to the verified record, the snapshot it was read from, the question asked and a sound interpretation — the same gates the mutation path enforces; source-bound inspection re-read, observed status/date/ID and explicit goal predicates |
| Partial/blocked/approval/failure statuses | `Status` plus specific `Error` and remaining-goal report |
| Budgets | Shared semantic step budget, per-call timeouts, repeated state/action limit, no-progress limit |
| Run memory | Completed/failed/current steps, plans, action-state pairs, attempted mutations; no long-term user memory |
| Replanning under change | Fresh capability observations, stale/foreign proposal rejection, changed approval proposals require new approval |
| Targeted retrieval | Phase 3 needed_sections mapped to specific semantic reads; no mandatory every-tab tour |
| External dependencies | Explicit gates, missing phone/input, review/inspector/document/signature/payment dependencies; reported rather than invented completion |
| Persistent constraints | Immutable Goal, checked before each mutation; unsupported timing/restriction language requests clarification |
| Approval pauses | Concrete proposal token includes run/record/snapshot/action/constraints/availability/cost; explicit approve/deny; single-use within planner instance |
| Uncertain mutation | Ledger before dispatch, then read-only verification; never replay on timeout or missing result |
| Trace | Semantic JSONL separate from browser logs; reasons, before/after fingerprints, progress, remaining goal and policy check |
| Phase 3 integration | Existing targeted retrieval and `understand`; injected reasoning callback supported |
| Phase 4 integration | Existing exact action selector, policy and `InspectionActionExecutor`; calendar and submission logic remain there |
| Model adapter | Runtime-validated choose_step; rejects prose success, unknown tool, extra fields, multiple calls and ineligible actions |
| Tests/evaluation | Planner scenarios, model/adversarial tests, real Phase 2–4 integration with fake I/O, repeatable flagship evaluation |

### Interpretation boundaries

A goal such as “get ready for next inspection” maps to verified identity, an
identified inspection target, and an independently observed scheduled inspection.
The report lists those exact predicates. It does not claim that physical work is
complete, that municipal approval was granted, or that a schedule alone proves
all readiness requirements.

A failed inspection is not sufficient authority to book another. Selection uses
the existing Phase 4 prerequisite/evidence gates. Explicit user targets are
converted to target intent only; eligibility and prerequisites still need portal
support. A fee balance without a gate is not treated as an inspection blocker.
A known fee gate or unknown action cost cannot be bypassed by a no-spend goal.

The parser deliberately supports a bounded subset: recognized inspection
commands, broader safe-progress wording, known prohibition clauses, next week,
weekdays, after a weekday, and before/by dates. It preserves the full original
objective and restrictions. Additional exclusions, times of day and unsupported
literal-date forms stop for clarification rather than silently broadening.
Programmatic Goals can provide exact normalized ISO date bounds. “Fix my permit”
alone permits information gathering, not an arbitrary mutation.

## Capability wiring

Construct `LicetCapabilities` with:

- `lookup`: existing `LookupRunner` using the configured dispatcher.
- `retrieval`: existing `Phase3RetrievalRunner`. Successful discovery binds its
  record reference to the independently verified Phase 2 cap IDs.
- `selection_context(world)`: read-only provider returning the existing
  `SelectionContext` from the current verified snapshot.
- `preflight(world)`: read-only provider returning `Preflight` with exact record,
  snapshot and operation fingerprint; observed eligibility, dates, cost,
  signature requirement, missing fields, external gates and supplied contact data.
- `portal`: existing Phase 4 portal implementation. Both synchronous scripted
  portals and the async Accela surface are supported. The worker bridge sends
  async browser calls back to their owning event loop; no duplicated wizard code.
- Optional `reasoner`, defaulting to existing Phase 3 deterministic `understand`.

Eligibility, cost and signature defaults are unknown. A missing preflight
observation must not be replaced with “eligible”, zero dollars, or no signature.
The selection/preflight providers are intentional trusted observation interfaces,
not model-generated permissions. They must be supplied by the portal integration
with fresh evidence. Phase 5 does not invent a new Accela read surface for values
that the current portal does not expose.

A timed-out Phase 4 worker may still be completing its single attempted action.
While it remains active, the adapter refuses overlapping portal work. The planner
reports unverified instead of launching another submission. Later reconciliation
must be a read, not a new run that blindly retries the mutation.

## API and CLI

```python
from licet.phase5 import GoalPlanner, LicetCapabilities, parse_goal

capabilities = LicetCapabilities(
    lookup=lookup_runner,
    retrieval=retrieval_runner,
    selection_context=read_selection_context,
    preflight=read_action_preflight,
    portal=inspection_portal,
)
planner = GoalPlanner(capabilities, max_steps=20)
run = await planner.run(parse_goal(user_goal, reference=local_date))
report = run.report()
```

For NEEDS_APPROVAL, display the concrete proposal and pass a real user decision:

```python
resumed = await planner.resume(run, token=run.approval_token, approved=True)
```

This consumes the paused approval and refreshes preflight. A changed proposal
pauses again. Silence has no corresponding code path. Approval storage is
run-local, not a durable multi-process authorization service. Do not deserialize
untrusted World/Run objects as verified observations or approval grants.

The CLI takes an explicit local capability factory; it does not silently create
or authenticate a portal session. Example offline demo:

```sh
.venv/bin/python scripts/phase5_run.py \
  'Get permit P-1 ready for its next inspection without spending money.' \
  --factory licet.eval.phase5_fixtures:ScriptedCapabilities
```

A real factory returns a wired LicetCapabilities instance. Add `--model` to use a
configured model for nontrivial semantic choices; no model/API calls are needed
for deterministic tests. The CLI never auto-approves a pause.

## Adversarial review

the adversarial review’s planner attack is recorded in `docs/phase5/adversarial_review.md`
with counterexample evidence in `docs/phase5/adversarial_evidence.json`. The
headline finding: the completion predicate was weaker than the execution
predicate, so a stale, self-contradicting, unfinished or off-question
interpretation could report `SUCCESS` with the record unread. `established`
now applies the gates `mutation_denial` already applied, every fact is bound to
its record and snapshot, and a mutation is only counted as verified when this
run issued it. Replay:

```sh
.venv/bin/python scripts/phase5_adversarial_replay.py
.venv/bin/python scripts/phase5_adversarial_replay.py \
  --json docs/phase5/adversarial_evidence.json
```

## Portal-state review

the portal integration’s Phase 5 assignment — planner failures that are actually **state
extraction** — is recorded in `docs/phase5/portal_state_review.md` with
replay evidence in `docs/phase5/portal_state_evidence.json`. Headline: ACA's
`MM/DD/YYYY` dates reached Phase 3's ISO-only attempt-ordering raw, so an
honestly rendered fail→pass history read as "order unknown" — the completion
gate went false and a correct planner looped targeted reads until
`PLAN_LOOP_DETECTED`. Extraction now normalizes portal dates, keeps outcome
words rendered in the status column, rejects legend lines, degrades a
self-disputing section to partial coverage, and preserves fee payment-status
wording. The review also closes the adversarial handoff below: the
deterministic rule engine **does** emit `blocks_answer=True` (four classes),
so that gate is reachable from real portal data, not only injected reasoning.
Replay:

```sh
.venv/bin/python scripts/phase5_portal_state_replay.py
.venv/bin/python scripts/phase5_portal_state_replay.py \
  --json docs/phase5/portal_state_evidence.json
```

## Validation and metrics

```sh
.venv/bin/python -m pytest tests/test_phase5* -q
.venv/bin/python scripts/phase5_eval.py --runs 10 \
  --output docs/phase5_scripted_evidence.json
```

`licet/eval/phase5_fixtures.py` holds the checklist's 30 deterministic planner
cases as data — 5 simple completion, 5 replanning, 5 partial completion, 5
constraint handling, 5 external blockers, 5 loop/error recovery. Each case binds
a goal and a scripted capability to the expected status, planner error, exact
semantic-action trace, remaining-success predicates and metric assertions.
`tests/test_phase5_scenarios.py` drives every case through the production
`GoalPlanner` and fails on any drift in status, trace, remaining goal or declared
metric. Where no mutation is attempted the violation rate is left unmeasured
(`None`) rather than reported as a zero, and the loop/churn cases assert that a
changing snapshot label is not mistaken for progress.

The scripted flagship completes ten consecutive runs: seven semantic steps each,
no duplicate attempts, and no out-of-plan reads. The integration test additionally
uses the real Phase 2 lookup runner, Phase 3 retriever/interpreter, Phase 4 selector
and executor, with fake browser/portal I/O and explicit eligibility/preflight
observations. It does not mock a final “goal complete” assertion.

Metrics include completion/partial rates, golden-path next-step accuracy,
replanning after failures, constraint-check coverage, duplicate attempts, loops,
semantic steps, useful-step efficiency, unnecessary reads relative to supplied
golden labels, and the `mutations_attempted` / `mutations_verified` ledger so a
submission can be reconciled without reading the trace. Unmeasured rates are null rather than fabricated zeros. Constraint
metrics cover recorded semantic policy checks, not arbitrary natural-language
restrictions or an independently measured browser violation rate.

### Live acceptance run

`licet/eval/phase5_live.py` opens one authenticated Solari session and wires the
real Phase 2 `LookupRunner`, Phase 3 `Phase3RetrievalRunner`, and the Phase 4
`AccelaInspectionPortal` into `LicetCapabilities`. Its `selection_context` and
`preflight` providers are fresh portal reads: eligibility is whether the
scheduling wizard offers the type, availability is the calendar's own active
days, and **scheduling cost and signature are left unknown** — so a no-spend goal
cannot be bypassed. `scripts/ni_phase5_acceptance.py` is the exit-gate harness:
plan-only unless `--execute`, a fresh session per attempt, and it never resumes an
approval.

Live 2026-09-22, goal *"Get permit BLD26-00469 ready for its next inspection;
schedule the Rough inspection."*:

| Step | Result |
|---|---|
| login (SSO) | authenticated |
| `FIND_PERMIT` | ok — Phase 2 independently verified the record |
| `READ_PERMIT_STATE` | ok |
| `DETERMINE_BLOCKERS` | ok — Phase 3 interpreted structured state |
| `READ_INSPECTIONS` | refused by the live tool layer (`not_actionable`) → `BLOCKED` |

Outcome: `BLOCKED` / `EXTERNAL_DEPENDENCY`, `mutations_attempted=0`,
`remaining_goal=['inspection_scheduled','next_inspection_identified']`.
Report: `docs/phase5/live_evidence.json`. (One of four attempts instead failed at
`FIND_PERMIT` with `action_outcome_unknown` — see below.)

Two live limitations surfaced and are recorded, not papered over:

- **Intermittent tool verification.** The Phase 2 search click returns
  `action_outcome_unknown` intermittently: navigation succeeds (a detail URL is
  reached) but the post-click verification cannot confirm it.
  `scripts/ni_lookup_validation.py --probe record` reproduced it and then passed
  on retry; `--probe address` failed on the same session. The harness mitigates by
  running each attempt in a fresh session.
- **No actionable `Inspections` section on this record.** Phase 3's targeted
  retrieval clicks the `Inspections` label, which the live page reports as
  matched-but-hidden (`not_actionable`). The record detail's sections are
  `Record Info | Payments | Attachments` (verified live by
  `scripts/ni_section_clickthrough.py`, 14/14); there is no clickable
  `Inspections` link. This is Phase 3's live surface, not the planner's: the
  planner stops rather than fabricating the missing section.

Because the inspection read cannot complete on this environment, the run cannot
reach selection/preflight/availability. Mapping the Phase 3 live inspections path
is what replaces this limitation with a full end-to-end live leg; the planner,
in that environment, remains bounded by the same gates (unknown cost and no
calendar capacity still stop a mutation). The unmapped reschedule/cancel
operations remain unavailable and are never improvised through the scheduling
wizard.
