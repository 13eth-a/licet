# Phase 5 adversarial planner review — DeepSeek V4.1 Flash

Reviewed 2026-09-22 against the working tree after Luna's planner/state machine
and the model selector. Scope, per the Phase 5 assignment: attack the planner
loop from the side — find loops, redundant actions, stale state, false success,
constraint loss, unmet preconditions, ignored external dependencies, and
`PARTIAL_SUCCESS` reported as full success — under the assumption that the
providers feeding the planner (`LicetCapabilities`, the injected `reasoner`, the
selection/preflight adapters) will eventually be wired to a live municipal
portal.

Method: read `licet/phase5/{state,planner,model,goals,capabilities}.py` and the
Phase 3/4 contracts it consumes, then construct the counterexample world for
each hazard and drive the **real** `GoalPlanner` with it. Every finding below was
reproduced live before it was fixed; each fix is locked by regressions in
`tests/test_phase5_adversarial.py` (41 cases), and every counterexample is
replayable with `scripts/phase5_adversarial_replay.py`.

Targets this review is measured against: constraint loss across replanning 0,
false success without verified state 0, precondition bypass 0, duplicate
mutation 0, hidden external dependency 0.

## Headline

The planner's **completion predicate was strictly weaker than its execution
predicate.** `established()` decided the two "understanding" facts —
`blockers_identified` and `next_inspection_identified` — from a single field
each, while `mutation_denial()` applied four additional gates to the *same two
objects* before allowing any mutation. Because the success check runs at the top
of `_drive` and returns immediately, both of the planner's own safety guards —
`next_actions()`' contradiction STOP and `mutation_denial()`'s reasoning gate —
were **unreachable** for every goal whose success conditions include those
facts, which is the default for `Goal` and for every vague or non-autonomous
parse. A stale or self-contradicting interpretation could therefore be reported
as `SUCCESS` with the record still unread.

## P1 — completion cheaper than execution (false success)

`established()` granted `blockers_identified` on
`reasoning.record_key == world.record_key and answerability == "answered"`. The
gates it omitted are exactly the ones the same object must pass to *act*:

| ID | Counterexample (measured against the pre-review predicate) | Why it is unsafe | Resolution |
|---|---|---|---|
| F1 | A reasoning result whose `snapshot_id` is an earlier snapshot still established `blockers_identified`. | The fact is bound to the record but not to the snapshot it was read from, so an interpretation of superseded state completes the goal. `mutation_denial` already refused to execute from a stale reasoning snapshot (`reasoning.snapshot_id != snapshot_id`). | `reasoning_is_sound()` requires `reasoning.snapshot_id == world.snapshot_id`. |
| F2 | `reasoning.contradictions` non-empty, `answerability == "answered"` → `blockers_identified`. | Two mutually exclusive readings were counted as an identified blocker. The planner's `next_actions()` has an explicit `if w.reasoning.contradictions: return (STOP,)` — the success check pre-empted it. | `reasoning_is_sound()` requires no contradictions. |
| F3 | `reasoning.needed_sections` non-empty, `answerability == "answered"` → `blockers_identified`, goal `SUCCESS`. | The planner declared completion while its own interpretation said a section was still needed; `next_actions()` would have read that section first. | `reasoning_is_sound()` requires no `needed_sections`. |
| F4 | A `needed_sections` entry with `blocks_answer=True` → `blockers_identified`. | A blocking uncertainty (missing premise, unresolved ordering, unavailable data) was silently treated as resolved. | `reasoning_is_sound()` requires no `blocks_answer` uncertainty. |
| F5 | `reasoning.question = "Is this permit approved yet?"` for the goal `"Fix my permit."` → `blockers_identified`. | An answer to a *different question* satisfied a goal fact. | `established(world, goal)` requires the interpretation's question to be the goal objective. |

End-to-end (real `GoalPlanner`, capability-supplied reasoning): all five
previously returned `Status.SUCCESS` with `remaining_goal == []`; they now stop
`BLOCKED` with the unsatisfied fact still listed and no mutation attempted.

**Reachability, stated precisely** (this repository's reviews name latent
gaps rather than implying they are all live): with the shipped deterministic
`understand()`, `answerability == "answered"` cannot coexist with contradictions
(they force `"conflicting"`) and the rule engine today never sets
`blocks_answer=True`, so F2/F3/F4 are *latent* against the current interpreter.
F1 and F5 are reachable the moment the documented injected reasoner is used
(`LicetCapabilities(reasoner=...)`; `licet/phase3/model_reasoning.py` is the
model-level stage already carried for this). The defect is that the planner
trusted one field of an object it otherwise refuses to trust — the same
reasoning model's `record_key` was already checked. A completion gate may not be
weaker than the execution gate on the same evidence.

## P2 — an unbound proposal satisfied "next inspection identified"

| ID | Counterexample | Why it is unsafe | Resolution |
|---|---|---|---|
| F6 | `world.proposal` with `record_key="another/record"` → `next_inspection_identified`. | The fact was granted from *any* proposal, while `mutation_denial` refused to execute it for exactly that reason. A goal of `("permit_verified", "next_inspection_identified")` completed on another record's inspection target. | `established()` requires `proposal.record_key == world.record_key and proposal.snapshot_id == world.snapshot_id`. |
| F7 | A proposal from a previous snapshot → `next_inspection_identified`. | Stale target counted as identified. | Same binding, plus `bound` is now shared by the scheduled/rescheduled/cancelled predicates. |

## P3 — approval unbound to the verified record

| ID | Counterexample | Why it is unsafe | Resolution |
|---|---|---|---|
| F8 | `world.permit.record_key="another/record"`, status `APPROVED` → `permit_approved`. | The only remaining fact that was *not* record-bound; a same-status record could satisfy an approval goal. | `established()` requires `permit.record_key == world.record_key`. |

## P4 — the "verified mutations" ledger was never written

`Run.verified_mutations` was declared in `state.py`, read by nothing, and absent
from `report()`; the planner recorded only `attempted_mutations`. An uncertain
submission had to be reconciled from the free-form trace, and the two counters
the checklist's duplicate/constraint metrics need were not published. Resolution:
`VERIFY_STATE` adds the operation key to `verified_mutations` **only when that
key is already in `attempted_mutations`**, so an appointment that merely existed
on the record is never attributed to Licet; `report()["metrics"]` now exposes
`mutations_attempted` and `mutations_verified`. The flagship now reports `(1, 1)`,
the already-scheduled path `(0, 0)`, and an unverified submission `(1, 0)`.

## P5 — two checklist goals were parsed as vague

"Get this permit moving." (the Phase 5 exit-condition example) and "Get me as
close to approval as possible." (named in *Support broader requests*) matched no
broad-request phrase, so `autonomous=False`: the planner gathered information
and stopped, and Phase 5 autonomy never engaged for two goals the checklist
requires. Resolution: `permit moving`, `as close to approval`, `as close as
possible`, `safe progress` added to the bounded broad-request vocabulary. Both
now parse `autonomous=True, vague=False` with the standard
`(permit_verified, next_inspection_identified, inspection_scheduled)` conditions;
autonomy still only selects among policy-gated, precondition-checked semantic
actions and grants no new authority.

## Verified-safe paths (attacked, no change needed)

- **Constraint preservation across replanning.** `Goal` is frozen with tuple
  fields rebuilt in `__post_init__`, the planner digests the goal before and
  after every capability call and refuses a changed goal, and `mutation_denial`
  re-derives every check from the current goal on every proposal. A provider
  that strips `constraints` (F-test) or widens the date window is refused with
  `PRECONDITION_NOT_MET` before any mutation, at any step.
- **No-spend / no-signature fail-closed.** An unknown cost or signature state
  never bypasses a `payment`/`signature` constraint: `None` is treated as
  unestablished, so the mutation is refused. Four combinations locked.
- **Loops and redundant actions.** A churning portal (new snapshot label every
  call — not progress, by design) stays inside `max_steps` and attempts at most
  one mutation; a read that never changes structured state is cut off by the
  action/state-pair and no-progress detectors.
- **No mutation replay.** `attempted_mutations` is keyed on the record plus the
  exact proposal and refuses a second attempt; after any mutation the next step
  is forced to be an independent re-read, and an uncertain submission is never
  replayed.
- **Approval binding.** The token covers run, goal, record, snapshot, action,
  proposed dates, cost and signature; resumption is explicit, consumed on first
  use, invalidated by a changed proposal, and denial is a normal path. Silence
  has no code path.
- **Model selector.** The model receives only the currently eligible semantic
  actions, a strict single-function schema, and untrusted portal state labelled
  as data; prose success, unknown tools, extra fields, multiple calls, unmet
  dependencies and ineligible actions are all rejected. A model denial was
  reproduced as *not* reaching the capability.
- **External dependencies and impossible goals.** A confirmed gate yields
  `PARTIAL_SUCCESS` + `EXTERNAL_DEPENDENCY` with no mutation; missing input
  requests it; contradictions stop before scheduling; `PARTIAL_SUCCESS` is never
  serialized as `SUCCESS`.
- **Policy agreement.** The planner's confirmation set and `decide_action_policy`
  agree on cancellation: not allowed, confirmation required, nothing reaches the
  portal on silence.

## Deliberately not changed (residual risk, named)

- **A vague goal reports `SUCCESS` for its own narrowed interpretation (owner:
  Luna/Astra).** `"Fix my permit."` completes as
  `(permit_verified, blockers_identified)` and the report carries
  `goal.vague=True`, so the narrowing is visible — but a UI that renders
  `SUCCESS` as "done" still overstates a request to *fix* the permit. Whether a
  vague goal should cap at `PARTIAL_SUCCESS` is a product/interpretation
  decision, not a safety fix, so it is recorded rather than changed.
- **Free-text constraints are carried, not enforced (owner: Luna/GLM).** Beyond
  payment/signature/operation, restrictions such as "do not contact the
  inspector" survive in `Goal.constraints` and are refused if a proposal drops
  them, but no component enforces their meaning. This is the Phase 4
  time/access-constraint residual, unchanged, and it now flows through Phase 5.
- **`blocks_answer` is unproducible by the shipped rule engine (owner:
  GLM/Astra).** The gate is enforced; only a model-level or injected reasoner can
  currently emit that state. Phase 3 raised this as a handoff and it remains
  open.
- **No cross-process idempotency (owner: Luna).** `attempted_mutations` is
  run-local. A process that dies between the commit click and the verification
  re-read is protected only by the portal's read gate on the next run. Phase 4
  recorded this as a Phase 5 concern; Phase 5 is single-run, so it is still open.
- **The objective binding is a reasoner contract.** A reasoner that does not echo
  the question it was asked now fails closed (`BLOCKED`, goal unmet) instead of
  completing. The shipped `understand()` echoes it; a custom reasoner must too.
- **Approval grants remain run-local.** No durable multi-process authorization
  service, as documented.
- **The report still carries no structured uncertain-submission record beyond
  `mutations_attempted`/`mutations_verified` and the trace.** Reconciliation is
  possible (the mutation key and the failed re-read are both in the trace) but
  not one field. Left alone rather than inventing a second ledger contract.

## Evidence

```
counterexamples (legacy = pre-review predicate, current = reviewed tree)
  F1..F5  legacy=GRANTS  current=REFUSES   blockers from unsound reasoning
  F6..F7  legacy=GRANTS  current=REFUSES   unbound proposal
  F8      legacy=GRANTS  current=REFUSES   unbound approval
  C1      legacy=GRANTS  current=GRANTS    positive control

end to end (real GoalPlanner)
  E1..E5  BLOCKED, mutations=0   (were SUCCESS with remaining_goal == [])
  E6      SUCCESS, mutations=1   flagship still completes once
```

- `tests/test_phase5_adversarial.py` — 41 regressions: the five reasoning gates
  and a positive control, unbound proposal/approval (predicate and end to end),
  constraint and date-window stripping, no-spend/no-signature across replans,
  frozen-constraint identity across every observation, prohibited operation after
  replanning, single mutation attempt, ledger attribution (issued vs
  pre-existing, verified vs unverified), churn bounded by the budget, repeated
  reads cut off, ineligible/date-less refusals, external dependency reporting,
  `PARTIAL_SUCCESS` never published as `SUCCESS`, global step budget, and
  planner/policy agreement.
- Full suite: **879 passed** (838 pre-existing + 41 new). Scripted acceptance
  unchanged and re-run: **10/10** runs, 7 semantic steps each, 0 duplicate
  attempts, 0 unnecessary reads, efficiency 0.857
  (`docs/phase5_scripted_evidence.json`). This is deterministic planner
  acceptance, **not** ten live mutations.
- `scripts/phase5_adversarial_replay.py` re-derives every counterexample and the
  end-to-end statuses; `--json docs/phase5/adversarial_evidence.json` records the
  result. The legacy column is the pre-review predicate reproduced verbatim in
  the script, because the pre-review Phase 5 runtime is not committed.

## Handoff

- **Luna**: decide the vague-goal status boundary; if the objective binding ever
  needs to accept a normalizing reasoner, widen it explicitly at the reasoner
  contract rather than in `established()`. The Phase 4 cross-process idempotency
  gap is now also Phase 5's.
- **GLM**: the `blocks_answer` uncertainty class is enforced but unproducible by
  the deterministic rule engine; the model-level interpreter is the only emitter.
  Phase 5 will accept it as soon as it is wired.
- **Astra**: the completion predicate is now the execution gate; nothing in
  `established()` may be relaxed without re-running the replay script. New
  goal-level facts (beyond the seven in `Goal.__post_init__`) must arrive with a
  record/snapshot-bound predicate here.

## Files touched

| File | Change |
|---|---|
| `licet/phase5/state.py` | `reasoning_is_sound()`; `established(world, goal)` binds every fact to record, snapshot, question and sound interpretation; `report()` publishes mutation counts. |
| `licet/phase5/planner.py` | Passes the goal to the completion predicate; records a verified mutation only for an action this run issued; files the mutation ledger. |
| `licet/phase5/goals.py` | Bounded broad-request vocabulary extended with the two checklist goals that fell through. |
| `tests/test_phase5_adversarial.py` | 41 regressions. |
| `scripts/phase5_adversarial_replay.py` | Counterexample + end-to-end replay, optional JSON evidence. |
| `docs/phase5/adversarial_evidence.json` | Replay output. |
| `docs/phase5_scripted_evidence.json` | Regenerated for the extended report metrics. |
