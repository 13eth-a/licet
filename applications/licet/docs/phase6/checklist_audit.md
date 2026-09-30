# Phase 6 checklist audit

Audited 2026-09-23 against the Phase 6 checklist, after the policy engine
(Luna), the portal-boundary map (GLM), the adversarial review (DeepSeek V4.1
Flash) and the architecture review (GPT-6 Astra). Method: for each checklist
item, name the code that implements it and the test that locks it; anything that
cannot be exercised on this environment is marked **environment-limited** with
the measurement that proves it, not left implied.

Status key: **Done** (implemented + regression-locked) · **Done (close-out)**
(implemented as part of this audit) · **Env-limited** (blocked on portal/tooling
this sandbox cannot produce) · **Residual** (named, owned, not hidden).

Phase 6 test files and size:

| File | Cases | Scope |
|---|---|---|
| `tests/test_phase6_policy.py` | 34 | policy engine, environment, constraints, confirmation, identity, ledger |
| `tests/test_phase6_adversarial.py` | 132 | hostile planner/model/caller threat model, end-to-end environment rows |
| `tests/test_phase6_portal_boundary.py` | 23 | mutation-boundary map parity, appointment identity |
| `tests/test_phase6_completion.py` | 37 | **close-out:** conflict, stops, provenance, metrics |
| `tests/test_phase6_integration.py` | 2 | **close-out:** flagship Phase 2–6 task, sandbox + live |

## Close-out additions (this audit)

Four checklist items had no executable representation; they now do.

| Checklist item | Implementation | Test |
|---|---|---|
| Detect contradictory instructions → `CONSTRAINT_CONFLICT` | `licet.safety.policy.detect_constraint_conflict`; `licet.phase5.goals.parse_goal` sets `clarification = CONSTRAINT_CONFLICT` | `test_phase6_completion.py::test_detect_constraint_conflict`, `::test_conflicting_goal_is_reported_and_not_autonomous`; `test_phase5.py::test_conflicting_goal_stops_before_browser` |
| Create safety stop conditions (11) | `licet.safety.stops.SafetyStopCondition`, `stop_for_decision`, `stop_for_result`, `unexpected_payment_screen` | `test_phase6_completion.py` §2 — one case per condition |
| Label external observations (TRUSTED/UNTRUSTED) | `licet.safety.sources` — `Trust`, `SourceKind`, `Observation`, `trusted_text`, `is_authoritative` | `test_phase6_completion.py` §3 |
| Track safety metrics (seven zero-targets) | `licet.safety.metrics.SafetyMetrics`; fed by `PolicyEngine(metrics=…)` and `InspectionActionExecutor(metrics=…)` | `test_phase6_completion.py` §4 |
| Run full Phase 2–6 integration (flagship + live) | `tests/test_phase6_integration.py` | the file |

`CONSTRAINT_CONFLICT` deliberately changes one Phase 5 scenario: `CH03`'s goal
*"Cancel … but do not cancel anything"* is a contradictory instruction, so it
now halts before browser access as the checklist requires rather than proceeding
and refusing at the mutation. Prohibited-cancellation without a contradiction
remains covered by `CH05`.

## Central policy engine

| Item | Status | Evidence |
|---|---|---|
| Every semantic action goes through policy before execution | Done | `InspectionActionExecutor.__init__` always builds a `PolicyEngine`; `LicetCapabilities` always owns one; `guard.authorize` is the primitive choke point |
| Planner cannot call mutation tools directly | Done | `GoalPlanner` emits `Action` values only; the dispatcher is the sole planner→browser path |
| Browser executor cannot bypass policy | Done | `workflow.py` decision + `accela_portal.py` refuses a live host + `guard.py` environment rule |
| Unknown actions default to DENY | Done | `PolicyEngine.decide` → `UNKNOWN_ACTION_RISK`; `risk_levels.classify` defaults to confirmation-required |
| Deterministic policy logic | Done | no model call anywhere in `licet/safety/`; `test_phase6_adversarial.py::test_the_policy_layer_is_never_weaker_than_the_dispatcher_guard` |

## Environment modes

| Item | Status | Evidence |
|---|---|---|
| `Environment` SANDBOX / LIVE_READ_ONLY / UNKNOWN | Done | `policy.py` |
| Detect sandbox/test environment | Done | `environment_from_url`/`detect_environment`; host-based only |
| Detect live municipal portal | Done | same |
| Treat unknown conservatively | Done | `UNKNOWN_ENVIRONMENT` denies every mutation |
| Never infer sandbox from test-looking data | Done | `test_phase6_policy.py::test_environment_is_host_based_not_data_based`; `test_phase6_adversarial.py::test_a_non_sandbox_host_is_never_classified_as_sandbox` |
| Hard-block live mutations (schedule/reschedule/cancel/pay/submit/upload/edit) | Done | `action.mutates_state and environment is not SANDBOX → denylive`; parametrized `test_live_portal_blocks_every_supported_mutation` |
| Live reads/search/availability allowed | Done | `test_phase6_policy.py::test_live_read_allowed` |
| Live blocking is not prompt-based | Done | it is a structural rule in `decide`, `guard`, and the adapter |

## Action risk levels & vocabulary

| Item | Status | Evidence |
|---|---|---|
| `ActionRisk` READ_ONLY/REVERSIBLE/CONSEQUENTIAL/PROHIBITED | Done | `policy.py` |
| Classify every supported action | Done | `ACTION_RISKS`; every `phase5.Action` value and every `risk_levels.KNOWN_ACTIONS` entry mapped |
| `PolicyDecision` schema | Done | `allowed`, `requires_confirmation`, `risk_level`, `reason`, `violated_constraint`, `verdict`, `confirmation` |
| Insert policy between planner and executor | Done | `GoalPlanner` check → `InspectionActionExecutor` → adapter |
| Closed action vocabulary | Done | `Action` enum; aliases normalize at the boundary |
| Reject invented planner actions | Done | `OVERRIDE_HOLD`/`DELETE_RECORD`/`SKIP_PAYMENT` → `UNKNOWN_ACTION_RISK` |

## Constraints

| Item | Status | Evidence |
|---|---|---|
| `UserConstraints` value object | Done | immutable, normalized booleans |
| Parse the six common phrases | Done | `from_text`; `CONSTRAINT_PARITY` in `test_phase6_adversarial.py` |
| Constraints persist for the whole run | Done | frozen `UserConstraints` + immutable Phase 5 `Goal`; `test_persistent_constraints_are_deeply_immutable` |
| Planner output cannot overwrite them | Done | `GoalPlanner._drive` re-digests the goal after every step and raises on change |
| Browser errors cannot reset them | Done | constraints are parsed once; the guard re-reads trusted text only |
| Detect contradictory instructions → `CONSTRAINT_CONFLICT` | Done (close-out) | see close-out table |

## Confirmation

| Item | Status | Evidence |
|---|---|---|
| `ConfirmationRequest` scope fields | Done | operation, permit, target, inspection id, record key, both date edges, amount |
| Confirmation authorizes exactly one action | Done | `matches` compares nullable fields exactly; consumed in a trusted registry |
| Approval expires / replanning does not reuse it | Done | 10-minute expiry; `confirmation_for` rebuilds per proposal |
| Never treat silence as approval | Done | `GoalPlanner.resume` requires the exact token |
| Never treat broad approval as permanent | Done | `test_broad_user_approval_does_not_unlock_consequential_actions` |
| Confirmation tests (block/allow/wrong-target/second-use/stale) | Done | `test_phase6_policy.py` + `test_phase6_adversarial.py` §P3 |

## Identity, inputs, stale state, idempotency

| Item | Status | Evidence |
|---|---|---|
| Missing-input handling → `MISSING_REQUIRED_INPUT` | Done | `required_inputs` checked independently of `verify_record` |
| Re-verify permit identity before every mutation | Done | `verify_identity`; `RECORD_IDENTITY_UNVERIFIED` |
| Verify target inspection (type/id/date/association) | Done | `same_inspection_type` shares `phase4.matching`; assertion fields must be observed |
| Verify date constraints | Done | `date_window_start/end` bound on action + approval; executor date selector |
| Stale-state protection | Done | fresh read per mutation; planner clears `verified_inspection`; identity re-checked |
| Mutation idempotency + states | Done | `MutationLedger`, `MutationState`; duplicate/unknown never replayed |
| Do not convert `UNKNOWN_RESULT` to success | Done | `UNKNOWN_RESULT_REQUIRES_RECONCILIATION` |
| Verify every mutation independently | Done | executor re-reads and `_matches`; policy `_matches` requires the named record/appointment |
| Capture before/after state | Done | `MutationAudit`, `InspectionActionResult.before/after` |
| Require replanning after mutation | Done | `_drive` forces VERIFY_STATE, `test_phase5*` no-replay cases |
| Maximum mutations per run | Done | `MutationLedger(max_mutations=2)` |
| Idempotency tests (exists/ timeout either side / duplicate / browser retry) | Done | `test_phase6_adversarial.py` §idempotency |

## Safety stops, untrusted content, audit, panel

| Item | Status | Evidence |
|---|---|---|
| 11 safety stop conditions | Done (close-out) | `licet/safety/stops.py` |
| Portal content treated as untrusted data | Done | `sources.Observation`; portal text never parsed as instruction |
| Prompt-injection-like portal text ignored | Done | `test_portal_instruction_text_is_untrusted_and_denied_as_an_action` |
| Label external observations (TRUSTED/UNTRUSTED) | Done (close-out) | `licet/safety/sources.py` |
| Safety audit logging (run_id…post_action_state) | Done | `SafetyAuditEvent`/`SafetyAuditLog`; swapped chapter `record_execution` |
| Log blocked actions | Done | `SafetyAuditLog.blocked()`; `test_blocked_actions_are_audited_without_an_execution_event` |
| Visible debug safety panel | Done | `safety_panel()`; `test_the_safety_panel_states_the_decision` |
| Track safety metrics (seven zero-targets) | Done (close-out) | `SafetyMetrics` |

## Tests

| Item | Status | Evidence |
|---|---|---|
| Environment / constraint / confirmation / identity / idempotency / adversarial tests | Done | see file table |
| Adversarial user tests (coercion, invented input, forever-approval, click-through, skip-check) | Done | `test_phase6_adversarial.py::test_coercive_user_language_does_not_disable_the_gate` and neighbours |
| Adversarial portal tests (instruction comment, suspicious filename, misleading labels, informational-looking confirm, unexpected payment) | Done | instruction/unexpected-payment covered; filename/label handling is `dispatcher.resolve_action` + guard |
| At least 30 Phase 6 tests | Done | 228 Phase 6 cases |
| Full Phase 2–6 integration: sandbox flagship | Done (close-out) | `test_flagship_task_completes_in_a_sandbox_without_paying` |
| Same task against live mode | Done (close-out) | `test_flagship_task_on_a_live_portal_prepares_but_does_not_execute` |

## Exit condition

> Licet's planner can propose arbitrary supported actions, but an independent
> deterministic policy layer controls whether they may execute. Sandbox mutations
> are permitted within explicit boundaries, live portals are read-only, user
> constraints persist throughout the run, and every mutation is identity-checked
> and independently verified. **Assume the planner behaves badly: can it still
> cause an unauthorized mutation? No.**

**Met.** Every mutation entry point holds a policy engine, the environment gate
is structural rather than prompt-based, and the adversarial suite drives a
hostile planner/caller into each route without producing an unauthorized
submission.

## Residuals (named, owned, not hidden)

Mirrors `docs/phase6/adversarial_review.md` and
`docs/phase6/astra_review_response.md`; this audit does not re-close them.

1. **Cross-process idempotency** — the `MutationLedger` is run-local. Owner: Luna.
2. **Observed existing appointment date not bound to the approval** — binding it
   would require reading the appointment before asking the human. Owner: Luna/GLM.
3. **Capability factory not handed the user instruction** — the guard and Phase 5
   `Goal` enforce it; a regression keeps the two readings in agreement. Owner: Luna.
4. **`parse_goal`/selection do not establish the cancellation appointment** — the
   boundary refuses an id-less targeted mutation; the portal-data half is closed
   (`parse_inspection_row_controls`). Owner: GLM/Luna.
5. **Payment/attestation screens unmapped** — attestation is mapped and
   PROHIBITED; payment/upload controls have never rendered here, so the phrase
   guard is the enforced boundary. **Env-limited.** Owner: GLM.
6. **Primitive approval is page-scoped, not argument-scoped** — binds action +
   URL + record + flow step, not the exact control. Owner: Luna.

## Verification

```text
python -m compileall -q licet tests scripts          clean
python -m pytest -q                                  1166 passed
  tests/test_phase6_policy.py            34
  tests/test_phase6_adversarial.py      132
  tests/test_phase6_portal_boundary.py   23
  tests/test_phase6_completion.py        37
  tests/test_phase6_integration.py        2
```

No browser, model API, credential or live portal was used: every case runs
against the deterministic layers and scripted doubles, and the only mutations
performed anywhere in this work were against portal doubles.
