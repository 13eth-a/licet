# Phase 7 — architecture review semantic recovery decisions

> Implementation follow-up: see [Phase 7 completion](completion.md) for the runtime fixes and current acceptance evidence. The findings below describe the earlier review snapshot.


Reviewed 2026-09-24. Role: selective semantic recovery/replanning specialist. implementation owns controller/runtime integration; adversarial review owns failure attacks; portal integration owns portal routes. No live portal or model calls were made.

## Delivered

`licet/phase7/semantic.py` provides `SemanticContext`, `SemanticDecision`, and `decide_semantic_recovery`. This is a deterministic, side-effect-free decision contract for structured evidence, with 18 tests in `tests/test_phase7_semantic.py`. It does not authorize browser actions or claim the currently running planner invokes it. Import directly from `licet.phase7.semantic`.

The caller supplies trusted original-goal requirements, independently verified findings and identity, freshness, conflicts, mutation status, and remaining controller budgets. Decisions return REPLAN, RETRY (reads only), or STOP, plus an outcome/reason and requested evidence sections. Arbitrary portal instructions are never interpreted as authorization. Source names in StateConflict must be mapped to the caller's closed read vocabulary; they are not executable tool names.

| Unexpected evidence | Semantic decision |
|---|---|
| Issued permit becomes Expired/Revoked/Void/Withdrawn during preparation | Stop the mutation plan, retain verified findings as partial success, explain lifecycle dependency. Do not silently replace scheduling with renewal/payment/submission. |
| Overview and history disagree | Refresh both once within read budgets. Compare field meaning and provenance, not timestamp alone. If still unresolved, stop consequential work. |
| Fees unavailable; goal asks what failed | Continue using verified inspections; fees remain unknown. |
| Fees unavailable and goal needs fee information | Bounded targeted read, then partial/blocked result if still unknown. |
| Scheduling chosen without established type | Reject proposal, spend replan budget, rebuild from verified preconditions. Never guess a type. |
| Repeated or oscillating state | Request bounded replan; a previously observed state is not new information. Stop when budget exhausted. |
| Submit timeout or redirect home after submission | Independent inspection read only. If reconciliation remains unknown, stop without replay. |
| Known identity is lost or changes | Stop; do not attribute cached findings/success to the new record. |
| Authentication, policy denial, required input | Stop with explicit reason; recovery does not weaken these requirements. |

Outcome semantics: SUCCESS requires fresh, nonconflicting, identity-verified goal evidence and no unresolved attempted mutation. PARTIAL_SUCCESS retains useful verified findings when the remaining goal is blocked; it never implies the mutation succeeded. BLOCKED means no useful verified goal work can be claimed. A technical FAILED status may be layered on by the caller for implementation errors; it must still retain earlier findings in the report. Missing goal-required sections cannot coexist with SUCCESS even if a caller asserts goal_satisfied.

## Decisions requested by adversarial review and portal integration

**A→B→A is not continuing semantic progress.** The transition can signal a world change requiring refresh, but revisiting B must not replenish retry/replan budgets or count as NEW_INFORMATION. Track cumulative evidence identity (record + section + semantic content/provenance), separate from current page state. Keep global step/recovery ceilings and settled identity loop detection. The current ProgressTracker compares only the immediately previous information set; it cannot establish this invariant alone.

**Heuristic classification is acceptable for diagnostics, not retry authorization.** A structured operation classification, explicit safe read callback, and mandatory post-recovery validator must determine whether recovery may execute. Mutation-like text correctly failing closed does not make every other text safe. Policy/authentication/identity/input codes should have explicit terminal precedence. Unknown error wording should gather evidence or stop.

**WAIT_FOR_SETTLE precedes semantic replanning.** An incomplete AJAX table is not evidence that inspections disappeared. Bound waiting/re-observation using the controller, require settled identity and complete required content, then evaluate the semantic state. Only changed verified evidence should invalidate and replace the plan. The current planner's unsettled route sets retryable=False and stops; it does not implement this wait.

**Home redirect during mutation remains reconciliation-first.** Do not retry the submit or clear its ledger entry. Recover navigation only through known safe paths, re-establish the exact permit, then independently inspect the operation result. Authentication failure returns AUTH_REQUIRED with the mutation outcome still unknown. A missing new row on one read does not prove an asynchronous request never committed.

## Missing work found in the presumed-complete roles

1. **implementation — runtime wiring remains incomplete.** GoalPlanner calls classification/loop/progress hooks, but not RecoveryController.recover, allow_replan, or replan_required. Invalid selector output still stops immediately. The semantic module delivered here is an explicit integration contract, not an assertion of runtime recovery. Wire it after structured state change/failure, consume controller budgets once per attempt, and dispatch only closed safe read strategies. Preserve immutable goal, constraints, approval invalidation, and mutation ledger across replans.
2. **implementation/portal integration — named portal routes are not executable recovery yet.** RECOVER_FROM_HOME, RETURN_TO_RECORD, RETURN_TO_ORIGIN_TAB, CLOSE_INFORMATIONAL_MODAL, and WAIT_FOR_SETTLE need bounded, validated implementations. Unknown or consequential modals must route to policy or stop. Do not infer that a route label proves recovery happened.
3. **implementation/adversarial review — controller validator is optional.** recover accepts any non-raising callback as success when validate is omitted, including a callback returning False. Require meaningful fresh-state validation on runtime paths; a callback completing is not recovery. This is especially important for wrong-page/record recovery.
4. **implementation/adversarial review — mutation reconciliation input must be tri-state and bounded.** mutation_reconciled branches on truthiness of occurred, so callers must not pass None as if absence were proven. Even a real False needs authoritative absence evidence and a retry ceiling; repeated reserve/reconcile-absent cycles must not create unlimited attempts. Existing Phase 4/5 refusal to blindly replay should remain until this is proven end to end.
5. **Whole-phase acceptance remains outstanding.** Existing replay examples exercise components. They are not evidence of 20+ noisy executions through the actual Phase 2–6 runtime, recovery strategies, and policy boundary. Preserve safe stops as valid outcomes, report false recovery/duplicates separately, and distinguish scripted from live evidence.

## Integration acceptance

Map structured observations to SemanticContext without trusting portal prose or a model's completion claim. REPLAN spends allow_replan; RETRY spends the existing read/global recovery budget and requires a fresh-state validator. Neither grants mutation authority. Clear stale proposals/availability/approvals on semantic change and re-run normal policy before any later mutation. Keep the attempted-mutation ledger intact. Log decision, evidence references, invalidated state, remaining goal, and budget consumption.

Run the 18 semantic regressions, existing recovery/portal/adversarial suites, and real-planner scenarios for expired permit, optional missing fees, conflicting state, invalid planner action, repeated state, and uncertain mutation. Phase 7 sign-off requires actual bounded recovery or an explicit safe stop in those integration runs, not only correct helper decisions.

Validation: full repository suite **1,245 passed in 23.46s**, including the **18 new semantic recovery tests**. `git diff --check` passed. These results establish local regression coverage, not live recovery acceptance.
