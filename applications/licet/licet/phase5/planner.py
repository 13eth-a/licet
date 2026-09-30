"""Bounded semantic planning over existing capabilities, never DOM operations."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict, replace
import json
from pathlib import Path
from typing import Protocol

from licet.phase4.policy import decide_action_policy
from licet.safety.policy import ConfirmationRequest, PolicyEngine, ProposedAction, RecordIdentity
from licet.phase5.state import (
    Action, Error, Goal, MUTATIONS, Observation, Plan, PlanStep, READS, Run,
    Status, World, digest, established, operation_key,
)
from licet.phase7.portal import PortalState, identity_from_world, route_recovery
from licet.phase7.recovery import RecoveryController
from licet.phase7.runtime import recover_read, context_for, fingerprint
from licet.phase7.semantic import decide_semantic_recovery, RecoveryChoice


class Capabilities(Protocol):
    async def perform(self, action: Action, goal: Goal, world: World, *,
                      confirmed: bool = False,
                      approval: ConfirmationRequest | None = None) -> Observation: ...


class Selector(Protocol):
    async def choose(self, run: Run, options: tuple[Action, ...]) -> tuple[Action, str]: ...


_OPERATION = {"schedule": Action.SCHEDULE_INSPECTION, "reschedule": Action.RESCHEDULE_INSPECTION,
              "cancel": Action.CANCEL_INSPECTION}


def next_actions(run: Run) -> tuple[Action, ...]:
    w, goal = run.world, run.goal
    if not w.permit_verified:
        return (Action.FIND_PERMIT,)
    if w.result is not None and w.verified_inspection is None:
        return (Action.VERIFY_STATE,)
    if run.attempted_mutations and not w.verified_inspection:
        return (Action.VERIFY_STATE,)
    if w.permit is None:
        return (Action.READ_PERMIT_STATE,)
    if w.reasoning is None:
        return (Action.DETERMINE_BLOCKERS,)
    if w.reasoning.contradictions:
        return (Action.STOP,)
    sections = [item.get("section") for item in w.reasoning.needed_sections]
    needed = tuple(a for a, section in READS.items() if section in sections)
    if needed:
        return needed
    if w.missing_information or w.dependencies:
        return (Action.STOP,)
    if w.proposal is None:
        return (Action.DETERMINE_NEXT_INSPECTION,) if w.selection is None else (Action.STOP,)
    if not w.availability_checked:
        return (Action.CHECK_INSPECTION_AVAILABILITY,)
    # Inspection identification and preflight are read-only. A non-autonomous
    # goal may gather those facts, but must stop before any schedule/reschedule/
    # cancel action is proposed.
    if not goal.autonomous:
        return (Action.STOP,)
    if not w.eligibility_verified or (goal.operation != "cancel" and not w.available_dates):
        return (Action.STOP,)
    return (_OPERATION[goal.operation],)


def _observed_no_availability_reason(run: Run) -> str | None:
    """Explain an empty calendar only when identity-bound calendar evidence exists."""
    availability = run.world.preflight_details.get("availability", {})
    if (not run.world.availability_checked or run.world.available_dates
            or availability.get("calendar_read") is not True
            or availability.get("identity_verified") is not True
            or availability.get("availability_status") not in {"none_in_observed_calendar", "none_matching_constraints"}):
        return None
    months = [str(item.get("month")) for item in availability.get("calendar_months", [])
              if item.get("month")]
    observed = f" ({', '.join(months)})" if months else ""
    description = "No active inspection dates were available" if availability.get("availability_status") == "none_in_observed_calendar" else "No future inspection dates matched the requested constraints"
    reason = f"{description} in the observed calendar window{observed}."
    if availability.get("search_stop") == "search_limit":
        reason += f" Search stopped at the {availability.get('search_limit_windows')} calendar-window limit; later availability is unknown."
    return reason


def make_plan(run: Run) -> Plan:
    actions = next_actions(run)
    steps = [PlanStep(f"r{len(run.plans)}-{i}", a,
                      "Obtain the missing prerequisite or verify the requested outcome") for i, a in enumerate(actions)]
    # A short horizon: parallel relevant reads followed by interpretation. The
    # next revision replaces this horizon as soon as new evidence arrives.
    if actions and all(a in READS for a in actions):
        steps.append(PlanStep(f"r{len(run.plans)}-reason", Action.DETERMINE_BLOCKERS,
                              "Reassess fresh structured state", tuple(s.id for s in steps)))
    plan = Plan(run.goal, steps, revision=len(run.plans))
    plan.validate()
    return plan


def confirmation_for(run: Run) -> ConfirmationRequest | None:
    """The scoped, single-use approval the run's pending action needs.

    Built from the run's own verified proposal, so the object a human approves
    names the exact permit, inspection type, existing appointment and record the
    executor will present at commit time. A later replan produces a different
    proposal and therefore a different object; the previous one cannot satisfy
    it, which is what makes "approval for one action" enforceable rather than a
    convention between the planner and the executor.
    """
    proposal = run.world.proposal
    if proposal is None:
        return None
    name = proposal.inspection_type or proposal.existing_inspection_id or "the record"
    return ConfirmationRequest(
        action_type=proposal.action_type,
        permit_id=proposal.permit_id or "",
        target=proposal.inspection_type or proposal.existing_inspection_id or "",
        consequence=f"{proposal.action_type.replace('_', ' ')} {name}" +
                    (f" for permit {proposal.permit_id}" if proposal.permit_id else ""),
        inspection_id=proposal.existing_inspection_id,
        record_key=proposal.record_key,
        date_window_start=proposal.date_window_start,
        date_window_end=proposal.date_window_end,
    )


def approval_key(run: Run) -> str:
    return digest({"run_id": run.run_id, "goal": asdict(run.goal), "record": run.world.record_key,
                   "snapshot": run.world.snapshot_id, "action": run.world.proposal.as_dict(),
                   "dates": sorted(run.world.available_dates), "cost": run.world.cost,
                   "signature": run.world.signature_required})


def mutation_denial(run: Run, action: Action) -> str | None:
    w, goal = run.world, run.goal
    if not goal.autonomous or goal.clarification:
        return "autonomous execution is not established or the goal needs clarification"
    p = w.proposal
    if not p or not w.permit_verified or not w.record_key or not w.snapshot_id or p.permit_id != w.permit_id:
        return "verified permit and inspection proposal are required"
    if action != _OPERATION.get(goal.operation) or p.action_type.removesuffix("_inspection") != goal.operation:
        return "operation conflicts with the original goal"
    if goal.operation in goal.prohibited_actions:
        return "the requested action is prohibited by the original constraints"
    if goal.inspection_type:
        from licet.phase4.matching import match_inspection_type
        if not match_inspection_type(goal.inspection_type, [p.inspection_type or ""]):
            return "selected inspection differs from the user target"
    if any(getattr(p, key) != getattr(goal, key) for key in ("date_window_start", "date_window_end", "preferred_date")):
        return "proposal changed the user's date constraints"
    if not set(goal.constraints) <= set(p.constraints):
        return "proposal dropped a user constraint"
    if p.record_key != w.record_key or p.snapshot_id != w.snapshot_id or not p.evidence_ids:
        return "proposal is stale or lacks record-bound evidence"
    if not w.eligibility_verified or not w.availability_checked or w.preflight_fingerprint != operation_key(w):
        return "fresh eligibility and availability preflight is required"
    if (w.missing_information or w.dependencies or not w.reasoning or w.reasoning.contradictions
            or w.reasoning.snapshot_id != w.snapshot_id or w.reasoning.record_key != w.record_key
            or w.reasoning.answerability != "answered" or w.reasoning.needed_sections
            or any(u.blocks_answer for u in w.reasoning.uncertainties)):
        return "an unresolved prerequisite or external dependency prevents execution"
    if "payment" in goal.prohibited_actions and (w.cost is None or w.cost != 0):
        return "no-spend constraint: zero cost has not been established"
    if "signature" in goal.prohibited_actions and w.signature_required is not False:
        return "no-signature constraint: absence of a signature requirement has not been established"
    return None


class GoalPlanner:
    def __init__(self, capabilities: Capabilities, *, selector: Selector | None = None,
                 max_steps: int = 20, timeout_seconds: float = 120, max_failures: int = 2,
                 allowed_actions: frozenset[Action] | None = None,
                 confirmation_required: frozenset[Action] = frozenset({Action.CANCEL_INSPECTION}),
                 trace_path: Path | None = None, policy_engine: PolicyEngine | None = None,
                 recovery: RecoveryController | None = None):
        if max_steps < 1 or max_failures < 1 or timeout_seconds <= 0:
            raise ValueError("planner budgets must be positive")
        self.capabilities, self.selector = capabilities, selector
        self.max_steps, self.timeout_seconds, self.max_failures = max_steps, timeout_seconds, max_failures
        self.allowed = frozenset(allowed_actions if allowed_actions is not None else Action)
        self.confirmations = frozenset(confirmation_required)
        self.trace_path = trace_path
        self.policy_engine = policy_engine
        self.recovery = recovery or RecoveryController()
        self._consumed_approvals = set()
        self._active = False

    async def run(self, goal: Goal, *, world: World | None = None) -> Run:
        if self._active:
            raise RuntimeError("one planner run may access the portal at a time")
        self._replan_pending = False
        self.recovery.begin_run()
        return await self._exclusive(Run(goal, deepcopy(world) if world else World()))

    async def resume(self, run: Run, *, token: str, approved: bool) -> Run:
        # Resumption is explicit, bound to this exact paused proposal; silence
        # has no path here. Grants are consumed at the first mutation attempt.
        if run.status != Status.NEEDS_APPROVAL or not token or token != run.approval_token or token in self._consumed_approvals:
            raise ValueError("approval does not match the pending proposal")
        self._consumed_approvals.add(token)
        resumed = deepcopy(run)
        run.approval_token = None  # consume the pause; it cannot be resumed twice
        run.status = Status.BLOCKED
        if not approved:
            resumed.approval_denied = True
            return self._stop(resumed, Error.NO_SAFE_ACTIONS, "user denied the proposed action")
        resumed.status = Status.RUNNING
        resumed.world.availability_checked = False
        return await self._exclusive(resumed, grant=token)

    async def _exclusive(self, run, grant=None):
        if self._active:
            raise RuntimeError("one planner run may access the portal at a time")
        self._active = True
        try:
            return await self._drive(run, grant)
        finally:
            run.recovery_report = self.recovery.report()
            self._active = False

    def _stop(self, run, error, reason, *, failed=False):
        run.error, run.reason = error, reason
        progress = established(run.world, run.goal) - {"permit_verified"}
        run.status = Status.FAILED if failed else Status.PARTIAL_SUCCESS if progress else Status.BLOCKED
        if run.plans:
            run.plans[-1].status = run.status
        return run

    async def _drive(self, run: Run, grant: str | None = None) -> Run:
        original_goal = digest(asdict(run.goal))
        while True:
            if run.goal.permit_id and run.world.permit_verified:
                from licet.lookup import record_numbers_match
                if not record_numbers_match(run.goal.permit_id, run.world.permit_id or ""):
                    return self._stop(run, Error.PRECONDITION_NOT_MET, "verified record differs from the requested permit", failed=True)
            if set(run.goal.success_conditions) <= established(run.world, run.goal):
                run.status, run.reason = Status.SUCCESS, "all declared success conditions independently established"
                if run.plans:
                    run.plans[-1].status = run.status
                return run
            if run.goal.clarification:
                return self._stop(run, Error.GOAL_UNSATISFIABLE, run.goal.clarification)
            if run.semantic_steps >= self.max_steps:
                return self._stop(run, Error.STEP_BUDGET_EXCEEDED, "semantic step budget exhausted")
            if run.no_progress >= self.max_failures or self.recovery.replan_required():
                decision = decide_semantic_recovery(context_for(run, self.recovery, repeated_state=True))
                self._trace(run, {"event": "SEMANTIC_RECOVERY", "decision": decision.choice.value, "reason": decision.reason})
                if decision.choice != RecoveryChoice.REPLAN or not self.recovery.allow_replan():
                    return self._stop(run, Error.PLAN_LOOP_DETECTED, "repeated actions produced no new information; recovery budget exhausted")
                self._replan_pending = True
                run.no_progress = 0
                self.recovery.progress.consecutive_no_progress = 0
            if run.world.reasoning and run.world.reasoning.contradictions and not any(t.get("event") == "CONFLICT_REFRESH" for t in run.trace):
                self._trace(run, {"event": "CONFLICT_REFRESH", "sections": ["overview", "history"]})
                run.pending_confirmation = None
                run.approval_token = None
                grant = None
                for read_action in (Action.READ_PERMIT_STATE, Action.READ_HISTORY):
                    refreshed = await recover_read(self, run, read_action,
                        Observation(run.world, False, "refresh conflicting section", retryable=True))
                    if not refreshed.success:
                        return self._stop(run, Error.EXTERNAL_DEPENDENCY, refreshed.message)
                    run.world = refreshed.world
                continue
            if run.world.permit and run.goal.autonomous and not run.attempted_mutations and (run.world.permit.status_normalized or "").casefold() in {"expired", "revoked", "void", "withdrawn"}:
                decision = decide_semantic_recovery(context_for(run, self.recovery))
                if decision.choice == RecoveryChoice.STOP:
                    return self._stop(run, Error.EXTERNAL_DEPENDENCY, decision.reason)
            plan = make_plan(run)
            run.plans.append(plan)
            ready = tuple(step.action for step in plan.steps if not step.dependencies)
            action, reason = ready[0], plan.steps[0].reason
            if self.selector and len(ready) > 1:
                try:
                    async with asyncio.timeout(self.timeout_seconds):
                        action, reason = await self.selector.choose(deepcopy(run), ready)
                    if action not in ready:
                        raise ValueError("model selected a step with unmet dependencies or outside the plan")
                except Exception as exc:
                    decision = decide_semantic_recovery(context_for(run, self.recovery, invalid_plan=True))
                    self._trace(run, {"event": "SEMANTIC_RECOVERY", "decision": decision.choice.value, "reason": str(exc)})
                    if decision.choice != RecoveryChoice.REPLAN or not self.recovery.allow_replan():
                        return self._stop(run, Error.NO_VALID_PLAN, f"invalid semantic decision: {exc}", failed=True)
                    self._replan_pending = True
                    # Discard the invalid selection. Rebuild the closed plan on
                    # the next iteration; no executor ever receives the bad verb.
                    continue
            if action == Action.STOP:
                dependencies = "; ".join(d.description for d in run.world.dependencies)
                missing = "; ".join(run.world.missing_information)
                selection_reason = run.world.selection.reason if run.world.selection else ""
                no_availability = _observed_no_availability_reason(run)
                return self._stop(run, Error.EXTERNAL_DEPENDENCY if dependencies else Error.NO_SAFE_ACTIONS,
                                  dependencies or missing or no_availability or selection_reason or "no supported safe action remains")
            run.model_fallbacks = getattr(self.selector, "fallbacks", 0)
            if action not in self.allowed:
                return self._stop(run, Error.NO_SAFE_ACTIONS, f"{action.value} is not an allowed capability")
            confirmed, approval = False, None
            if action in MUTATIONS:
                denial = mutation_denial(run, action)
                if denial:
                    return self._stop(run, Error.PRECONDITION_NOT_MET, denial)
                if self.policy_engine is not None:
                    proposal = run.world.proposal
                    central = ProposedAction(
                        action_type=action.value, permit_id=run.world.permit_id,
                        target=(proposal.inspection_type if proposal else None),
                        inspection_type=(proposal.inspection_type if proposal else None),
                        inspection_id=(proposal.existing_inspection_id if proposal else None),
                        existing_date=(run.world.verified_inspection.scheduled_date
                                       if run.world.verified_inspection else None),
                        date_window_start=(proposal.date_window_start if proposal else None),
                        date_window_end=(proposal.date_window_end if proposal else None),
                        record_key=run.world.record_key,
                    )
                    observed = (RecordIdentity(
                        permit_id=run.world.verified_inspection.permit_id,
                        record_key=run.world.verified_inspection.record_key,
                        inspection_type=run.world.verified_inspection.inspection_type,
                        inspection_id=run.world.verified_inspection.inspection_id,
                        existing_date=run.world.verified_inspection.scheduled_date,
                        status=run.world.verified_inspection.status,
                    ) if run.world.verified_inspection else None)
                    central_decision = self.policy_engine.decide(
                        central, observed_identity=observed,
                    )
                    if central_decision.denied:
                        return self._stop(run, Error.NO_SAFE_ACTIONS, central_decision.reason)
                key = operation_key(run.world)
                if key in run.attempted_mutations:
                    return self._stop(run, Error.NO_SAFE_ACTIONS, "this mutation was already attempted; reconcile rather than replay")
                ticket = approval_key(run)
                confirmed = grant == ticket
                policy = decide_action_policy(run.world.proposal, confirmed=confirmed)
                needs_confirmation = action in self.confirmations or policy.requires_confirmation
                if needs_confirmation and not confirmed:
                    run.status, run.approval_token = Status.NEEDS_APPROVAL, ticket
                    # Issue the approval now, against this exact proposal, and
                    # keep it for the resumption. Nothing downstream may invent
                    # one, and it is consumed by the policy layer on execution.
                    run.pending_confirmation = confirmation_for(run)
                    plan.status = run.status
                    run.reason = "explicit approval required for the current record, action, constraints and availability"
                    self._trace(run, {"action": Action.REQUEST_USER_APPROVAL.value, "reason": run.reason,
                                      "proposal": run.world.proposal.as_dict(), "approval_token": ticket})
                    return run
                if not policy.allowed:
                    return self._stop(run, Error.NO_SAFE_ACTIONS, policy.reason)
                # The human's grant becomes the scoped approval object, and the
                # run's copy is dropped so nothing can present it twice.
                approval = run.pending_confirmation if confirmed else None
                run.pending_confirmation = None
                if not self.recovery.mutation_started(key):
                    return self._stop(run, Error.NO_SAFE_ACTIONS, "duplicate mutation blocked by recovery")
                run.attempted_mutations.add(key)
                run.approval_token, grant = None, None
            before = run.world.fingerprint()
            pair = digest([action.value, before])
            # The loop key is the *settled* page identity (adversarial review handoff):
            # active_section is written by the Phase 3 retrieval runner from
            # the settled flow step, never by a live render token, and a
            # postback wizard's step change updates it even though the URL
            # does not move.
            if self.recovery.loop_observed(
                action.value,
                identity_from_world(run.world),
                str(run.world.record_key or run.world.permit_id or "unknown"),
            ):
                return self._stop(run, Error.PLAN_LOOP_DETECTED,
                                  "repeated semantic action/page/permit state")
            if run.seen.get(pair, 0) >= self.max_failures:
                return self._stop(run, Error.PLAN_LOOP_DETECTED, "repeated semantic action/state pair")
            run.seen[pair] = run.seen.get(pair, 0) + 1
            run.semantic_steps += 1
            step = next(s for s in plan.steps if s.action == action)
            run.current_step, step.status = step.id, "running"
            prior_world = deepcopy(run.world)
            previous_key = run.world.record_key
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    observation = await self.capabilities.perform(
                        action, run.goal, deepcopy(run.world), confirmed=confirmed, approval=approval,
                    )
                if digest(asdict(run.goal)) != original_goal:
                    raise ValueError("immutable goal constraints changed")
                if previous_key and observation.world.record_key != previous_key:
                    raise ValueError("capability switched records")
                if observation.world.permit and observation.world.permit.record_key != observation.world.record_key:
                    raise ValueError("permit state belongs to another record")
                run.world = observation.world
            except Exception as exc:
                failure = self.recovery.classify(
                    exc, operation=action.value, mutation=action in MUTATIONS,
                )
                self._trace(run, {"event": "FAILURE", "failure": failure.as_dict()})
                observation = Observation(
                    run.world, False, f"{type(exc).__name__}: {exc}",
                    uncertain=action in MUTATIONS,
                    retryable=failure.recoverable and action not in MUTATIONS,
                )
            if action not in MUTATIONS and not observation.success:
                prior_world.browser_state = observation.world.browser_state
                run.world = prior_world
                observation = replace(observation, world=prior_world)
                observation = await recover_read(self, run, action, observation)
                if observation.success:
                    run.world = observation.world
            if observation.success and run.world.permit_verified and action not in MUTATIONS:
                self.recovery.checkpoint("verified_state", {"record_key": run.world.record_key,
                    "completed_steps": list(run.completed_steps), "world_fingerprint": run.world.fingerprint()}, fingerprint(run.world))
            after = run.world.fingerprint()
            progress = before != after
            progress_kind = self.recovery.progress_update(
                information={after},
                state=after,
                goal=established(run.world, run.goal),
            )
            run.no_progress = 0 if progress else run.no_progress + 1
            if progress and observation.success and getattr(self, "_replan_pending", False):
                self.recovery.stats.replan_successes += 1
                self._replan_pending = False
            run.useful_steps += int(progress and observation.success)
            step.status = "completed" if observation.success else "failed"
            (run.completed_steps if observation.success else run.failed_steps).append(step.id)
            self._trace(run, {"step": step.id, "action": action.value, "reason": reason,
                              "success": observation.success, "uncertain": observation.uncertain,
                              "message": observation.message, "before": before, "after": after,
                              "progress": progress, "progress_kind": progress_kind.value,
                              "policy_checked": action in MUTATIONS,
                              "mutation_key": operation_key(run.world) if action in MUTATIONS else None,
                              "remaining_goal": sorted(set(run.goal.success_conditions) - established(run.world, run.goal))})
            if action in MUTATIONS:
                # Regardless of provider success, the next step independently
                # re-reads. An uncertain action is never retried automatically.
                run.world.verified_inspection = None
                run.no_progress = 0
            elif action == Action.VERIFY_STATE:
                if not observation.success or not run.world.verified_inspection:
                    self.recovery.mutation_reconciled(operation_key(run.world), occurred=None)
                    return self._stop(run, Error.PRECONDITION_NOT_MET, "action outcome remains unverified; no replay")
                # A mutation this run actually issued is now independently
                # confirmed. The key is only ever added for an attempted action,
                # so a pre-existing appointment observed on a read is not
                # recorded as something Licet did.
                if operation_key(run.world) in run.attempted_mutations:
                    if set(run.goal.success_conditions) <= established(run.world, run.goal):
                        run.verified_mutations.add(operation_key(run.world))
                        recorder = getattr(self.capabilities, "record_verified_mutation", None)
                        if recorder is not None:
                            recorder(run.world)
                        self.recovery.mutation_reconciled(operation_key(run.world), occurred=True)
                    else:
                        self.recovery.mutation_reconciled(operation_key(run.world), occurred=None)
                if not set(run.goal.success_conditions) <= established(run.world, run.goal):
                    return self._stop(run, Error.PRECONDITION_NOT_MET, "observed final state does not satisfy the goal")
            elif not observation.success and not observation.retryable:
                return self._stop(run, Error.PLAN_LOOP_DETECTED if observation.message.startswith("recovery budget exhausted:") else Error.EXTERNAL_DEPENDENCY, observation.message or "capability could not proceed")

    def _trace(self, run, item):
        run.trace.append(item)
        if self.trace_path:
            self.trace_path.parent.mkdir(parents=True, exist_ok=True)
            with self.trace_path.open("a") as output:
                output.write(json.dumps(item, default=str) + "\n")
