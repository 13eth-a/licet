# Phase 5 checklist audit

Audited 2026-09-22 against the Phase 5 checklist, after the planner, the
deterministic scenario set, the adversarial review and the portal-state review.
Method: for each checklist item, name the code that implements it and the test
that locks it; anything that cannot be exercised on this environment is marked
**environment-limited** with the measurement that proves it, not left implied.

Status key: **Done** (implemented + regression-locked) · **Scripted**
(implemented and locked against scripted capabilities; the live leg needs an
environment that can complete Phase 3's inspection read) · **Env-limited**
(blocked on portal/tooling this sandbox cannot produce) · **Open** (not started).

## Headline

The planner machinery — goal schema, parsing, semantic vocabulary, preconditions,
postconditions, dependency handling, the observe/plan/policy/execute/verify/replan
loop, budgets, loop/progress detection, immutable constraints, approval pauses,
external-dependency handling, traces and metrics — is complete and
regression-locked (927 tests; `tests/test_phase5*` = 147). The 30-case
deterministic planner set (`licet/eval/phase5_fixtures.planner_scenarios`) is the
checklist's own test split as data, executed through the production `GoalPlanner`.

The **live stack was wired and run** (`licet/eval/phase5_live.py`,
`scripts/ni_phase5_acceptance.py`): login authenticated, `FIND_PERMIT` verified
the record through the real Phase 2 stack, `READ_PERMIT_STATE` and
`DETERMINE_BLOCKERS` ran, and the run stopped safely at `READ_INSPECTIONS` when the
live tool layer refused the section click (`not_actionable`) — **no mutation
attempted**. The one unfulfilled exit-condition leg is *ten consecutive live
flagship runs*; it is blocked by the live environment, not by the planner.

**Decision (2026-09-22): Phase 5 is signed off as code-complete with that one
accepted environment/tooling limitation.** The measured gap is (a) intermittent
live tool verification and (b) Phase 3's live `Inspections` section surface — not
planner logic, schemas, gates, budgets, constraints or tests.

## Live acceptance, executed and recorded (2026-09-22)

`scripts/ni_phase5_acceptance.py --attempts 4` (fresh session per attempt),
goal *"Get permit BLD26-00469 ready for its next inspection; schedule the Rough
inspection."*

| Step | Result |
|---|---|
| login (SSO iframe) | authenticated |
| `FIND_PERMIT` | ok — Phase 2 independently verified the record |
| `READ_PERMIT_STATE` | ok |
| `DETERMINE_BLOCKERS` | ok — Phase 3 interpreted structured state |
| `READ_INSPECTIONS` | refused (`not_actionable`) → `BLOCKED` / `EXTERNAL_DEPENDENCY` |

`mutations_attempted=0`. Report: `docs/phase5/live_evidence.json`.

The two live limitations, with the measurement that proves each:

| Limitation | Measurement |
|---|---|
| Intermittent post-click verification | `scripts/ni_lookup_validation.py --probe record` failed with `action_outcome_unknown` then passed on retry; `--probe address` failed on the same session. One of four Phase 5 attempts failed at `FIND_PERMIT` the same way. Navigation succeeds (a detail URL is reached); only the verification cannot confirm it. |
| No actionable `Inspections` section | `scripts/ni_section_clickthrough.py` passes 14/14 live and lists the record's sections as `Record Info \| Payments \| Attachments` — there is no clickable `Inspections` link; the click is reported matched-but-hidden (`not_actionable`). |

## Checklist

| Item | Status | Evidence / reason |
|---|---|---|
| Define the planner input | Done | `Run`/`World` in `licet/phase5/state.py`: goal, permit state, constraints, allowed/confirmation action sets, browser-state summary, completed/failed/current steps, prior failures |
| Create a goal schema | Done | frozen `Goal` (`state.py`); `asdict` normalization; immutable string tuples enforced in `__post_init__` |
| Parse natural-language goals: objective, constraints, dates, prohibited actions, autonomy, vagueness | Done | `goals.py` `parse_goal`; `tests/test_phase5.py` date/restriction/vague cases |
| Support broader requests ("get ready", "do everything possible", "get it moving", "as close to approval", "handle the outstanding issue") | Done | `broad` vocabulary in `parse_goal`; broad-goal cases in `test_phase5_adversarial.py` and `test_phase5.py` |
| Define planner output (`Plan`, `PlanStep`) | Done | `Plan`, `PlanStep` (`state.py`) with `Plan.validate()` |
| Generate an initial plan | Done | `planner.make_plan`; `next_actions` drives the horizon |
| Keep plans short (3–8 steps, semantic level) | Done | `Plan.validate()` rejects >8 steps; semantic vocabulary only; scripted flagship is 7 steps |
| Separate planner actions from browser actions | Done | closed `Action` enum; no DOM verbs; browser work stays in `Capabilities` |
| Planner action vocabulary (closed set) | Done | `Action` in `state.py`; model can only choose from currently valid steps |
| Avoid unrestricted free-form tools | Done | `GoalPlanner.allowed` (`frozenset(Action)`); unknown/extra model fields rejected (`model.py`) |
| Preconditions on actions | Done | `planner.next_actions` + `mutation_denial`; `CHECK_INSPECTION_AVAILABILITY` requires verified record/selection |
| Postconditions | Done | `established(World, Goal)` binds each fact to the verified record and snapshot; `verified_inspection` re-read |
| Dependency handling (no jumping to schedule) | Done | ordered gates in `next_actions`/`mutation_denial`; `Plan.validate()` enforces dependency order |
| Execution loop (observe → plan → policy → execute → verify → replan) | Done | `GoalPlanner._drive` |
| Replan after meaningful state change | Done | new `Plan` each iteration; stale/foreign proposal rejection |
| Plan revision | Done | plan revisions recorded; `test_phase5.py` replanning cases |
| Partial completion | Done | `Status.PARTIAL_SUCCESS`; `_stop` computes progress from `established` |
| Goal success conditions from state, not prose | Done | `success_conditions ⊆ established(world, goal)`; model prose cannot declare success |
| Failure conditions (missing info, no safe action, portal block, approval, impossible, repeated failure, max steps) | Done | `Error` enum + `_stop`; each mapped by a scenario in the 30-case set |
| Planner statuses (`RUNNING/SUCCESS/PARTIAL_SUCCESS/BLOCKED/NEEDS_APPROVAL/FAILED`) | Done | `Status` enum |
| Maximum step budget | Done | `max_steps`; scenario `LE02` locks `STEP_BUDGET_EXCEEDED` |
| Track completed actions (`completed_steps`, `failed_steps`, `current_step`, `remaining_goal`) | Done | `Run` fields + `Run.report()`; asserted per scenario |
| Prevent repeated semantic actions | Done | repeated (action, state-fingerprint) pair guard; scenarios `RP01`, `LE01`, `LE04` |
| Progress detection | Done | `World.fingerprint()` excludes snapshot labels; `no_progress` guard; `LE04` churn case |
| Information-gathering decisions (read only what is needed) | Done | `next_actions` uses `reasoning.needed_sections`; `test_only_relevant_sections_are_planned` |
| Avoid reading every tab by default | Done | “no out-of-plan reads” metric, `unnecessary_reads`; targeted reads only |
| Uncertainty-driven exploration | Done | planner reads the targeted section before deciding; portal-state review closes the loop-to-reads path |
| When the planner may infer (facts from state, labeled inference, low confidence no irreversible action, high-risk needs evidence) | Done | `FactKind`, `blocks_answer` gate, `mutation_denial` evidence requirements |
| Connect Phase 3 reasoning (`PermitState → ReasoningResult → Planner → action`) | Done | `LicetCapabilities.DETERMINE_BLOCKERS` → `understand`; `test_phase5_integration.py` uses the real Phase 3 stack |
| Connect Phase 4 execution (policy → execute → verify) | Done | `LicetCapabilities` MUTATIONS → `InspectionActionExecutor`; executor/policy unchanged |
| Do not duplicate Phase 4 logic | Done | planner carries no calendar/submission logic; the adapter owns the wizard |
| Policy gate before every mutation | Done | `decide_action_policy` + `mutation_denial` before dispatch; `policy_checked` in trace |
| Carry user constraints throughout the run | Done | immutable `Goal` re-digested after every step; changed goal raises |
| Immutable constraint state | Done | frozen `Goal`; `constraints`/`prohibited_actions` tuples; `test_persistent_constraints_are_deeply_immutable` |
| Approval pauses | Done | `Status.NEEDS_APPROVAL` + concrete `approval_key` token; `resume()` |
| Never treat silence as approval | Done | no auto-resume path; `resume` requires the exact token |
| Handle impossible goals (no fabrication) | Done | `GOAL_UNSATISFIABLE` / `EXTERNAL_DEPENDENCY` stop paths |
| External-dependency detection (inspector, municipality, document, payment, signature, third party) | Done | `ExternalDependency`; scenarios `EB01`–`EB05`, `PC01`–`PC05` |
| Represent external blockers | Done | `ExternalDependency` dataclass with `can_licet_resolve` |
| Planner memory within a run | Done | `Run` state; no repeated rediscovery (completed/failed steps, seen pairs) |
| No long-term user memory | Done | run-local only; nothing persisted across runs |
| Execution trace | Done | `_trace` JSONL separate from browser logs; asserted in `test_plan_revisions_and_trace_are_separate_from_browser_logs` |
| Store planner decisions separately from browser logs | Done | semantic trace vs dispatcher run log (live) / scripted trace |
| Reasoning summaries (not raw chain-of-thought) | Done | each trace entry carries `reason`, `before`/`after`, `progress`, `remaining_goal` |
| Planner-specific errors (`NO_VALID_PLAN`, `PLAN_LOOP_DETECTED`, `STEP_BUDGET_EXCEEDED`, `PRECONDITION_NOT_MET`, `GOAL_UNSATISFIABLE`, `EXTERNAL_DEPENDENCY`, `NO_SAFE_ACTIONS`, `REPLAN_FAILED`) | Done | `Error` enum; each reachable from a scenario or unit test |
| Deterministic planner tests first (hardcoded state → plan outputs, no browser) | Done | 30-case set + `tests/test_phase5_scenarios.py` |
| Multi-step scenarios 1–5 (simple, already complete, fee blocker, missing information, unexpected state) | Done | `SC01`/`SC02`, `PC01`, `test_missing_phone_requests_information_without_fabrication`, `RP04`/`LE05` |
| ≥30 Phase 5 planner tests | Done | 30-case scenario set; 147 across `tests/test_phase5*` |
| Test misleading user instructions | Done | `test_misleading_click_instruction_does_not_authorize_arbitrary_execution` |
| Test conflicting goals | Done | `test_conflicting_goal_stops_before_browser` |
| Test vague goals | Done | `test_vague_goal_only_gathers_information` |
| Track planner metrics (completion, partial, next-step, replanning, constraint, duplicate, loop, avg steps) | Done | `licet/eval/phase5.py` `planner_metrics`; declared per scenario |
| Measure unnecessary work | Done | `unnecessary_reads` (against golden labels); `semantic_actions` histogram |
| Plan efficiency metric | Done | `Run.report()["metrics"]["efficiency"]`; scripted flagship 6/7 = 0.857 |
| Run full integration tests (Phase 2–5 combined) | Done | `tests/test_phase5_integration.py` uses real Phase 2/3/4 with fake I/O |
| Run the flagship autonomous task repeatedly | Scripted | `scripts/phase5_eval.py --runs 10` = 10/10, identical correct outcome |
| Aim for 10 consecutive successful flagship runs | **Env-limited (live)** | scripted 10/10 passes; the *live* leg stops at Phase 3's `Inspections` section (`not_actionable`) — see headline. No live mutation was attempted |
| Do not build a multi-agent swarm | Done | one planner coordinating deterministic capabilities |
| Keep Phase 5 exit condition | **Env-limited (live)** | implemented and scripted-verified; live exercise reaches Phase 3 and stops safely |

## Test-case traceability

The checklist's suggested split maps 1:1 onto the data table in
`licet/eval/phase5_fixtures.planner_scenarios`, so a reviewer can read the
checklist against executable cases:

- simple goal completion → `SC01`–`SC05` (flagship, already scheduled, reschedule,
  explicit type, next-week window)
- replanning → `RP01`–`RP05` (failed read stops, ineligible, no dates, unexpected
  expiry, contradictory state)
- partial completion → `PC01`–`PC05` (fee, inspector, municipality, document, signature)
- constraint handling → `CH01`–`CH05` (no-spend, no-signature, no-cancel, date
  window, prohibited operation)
- external blockers → `EB01`–`EB05`
- loop/error recovery → `LE01`–`LE05` (repeated read, budget, no-replay mutation,
  churning snapshot, unexpected expiry)

## What is required to close the live leg

1. **Map the Phase 3 live `Inspections` section path.** The record detail exposes
   `Record Info | Payments | Attachments`; targeted retrieval must read the
   inspections content (row parse) from the live page instead of clicking a
   non-actionable `Inspections` label. This is Phase 3 work.
2. **Stabilise post-click verification** (`action_outcome_unknown` on the Phase 2
   search click). This is the live tool/client layer.
3. **Re-run** `scripts/ni_phase5_acceptance.py` to reach
   selection → preflight → availability. On this sandbox the honest terminal
   result is then still a safe partial stop (no calendar capacity; unknown
   scheduling cost), which the planner already handles without a mutation.

Items 1–3 are not blockers to closing Phase 5 here; they are the work an
environment with a complete live read path re-opens.

## Sign-off (2026-09-22)

The deterministic boundary is complete and regression-locked. The live leg was
executed as far as the live tool layer allows and stopped safely, with no
mutation attempted. Phase 5 is therefore closed as code-complete with one
accepted environment/tooling limitation, mirroring Phase 4's accepted live
booking limitation.
