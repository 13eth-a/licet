"""Adapters to Phase 2 discovery, Phase 3 retrieval/reasoning, Phase 4 execution.

Portal-specific eligibility/cost/availability observations are explicit injected
read providers: absent evidence stops execution rather than inventing defaults.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field as dataclass_field, replace
import inspect
from typing import Any

from licet.agent.state import AgentState
from licet.lookup import LookupStatus, PermitLookupRequest, parse_lookup_request
from licet.phase3.reasoning import understand
from licet.phase3.state import NextActionCandidate, PermitState
from licet.phase4.actions import InspectionAction
from licet.phase4.selection import SelectionStatus, select_inspection_action
from licet.phase4.workflow import InspectionActionExecutor
from licet.phase5.state import Action, ExternalDependency, MUTATIONS, Observation, READS, digest, operation_key
from licet.safety.policy import Environment, PolicyEngine, ProposedAction, RecordIdentity, detect_environment


@dataclass(frozen=True)
class Preflight:
    record_key: str
    snapshot_id: str
    operation_fingerprint: str
    eligible: bool = False
    available_dates: tuple[str, ...] = ()
    cost: float | None = None
    signature_required: bool | None = None
    missing_information: tuple[str, ...] = ()
    dependencies: tuple[ExternalDependency, ...] = ()
    required_inputs: tuple[tuple[str, str], ...] = ()
    details: dict[str, Any] = dataclass_field(default_factory=dict)


async def _call(function, *args, **kwargs):
    value = function(*args, **kwargs)
    return await value if inspect.isawaitable(value) else value


class LicetCapabilities:
    def __init__(self, *, lookup, retrieval, selection_context, preflight, portal,
                 reasoner=understand, agent_state: AgentState | None = None,
                 policy_engine: PolicyEngine | None = None, environment: Environment | None = None,
                 constraints=None, run_id: str = "run", user_goal: str = "", mutation_journal=None):
        self.lookup, self.retrieval = lookup, retrieval
        self.selection_context, self.preflight = selection_context, preflight
        self.portal, self.reasoner = portal, reasoner
        detected = environment if environment is not None else detect_environment(portal)
        # Explicit Phase 6 configuration is authoritative, and the safety layer
        # is never optional: an injected engine wins, otherwise the portal's own
        # detected environment is used. A portal with no identity at all yields
        # an UNKNOWN engine, which refuses every mutation — dropping the engine
        # for unclassifiable portals made "unknown" the one environment where
        # the whole Phase 6 boundary was switched off.
        self.policy_engine = policy_engine or PolicyEngine(
            environment=detected, constraints=constraints, run_id=run_id,
            user_goal=user_goal or getattr(agent_state, "goal", "Phase 5"),
        )
        self.agent_state = agent_state or AgentState(goal="Phase 5")
        self.mutation_journal = mutation_journal
        self._inputs = {}
        self.audits = []
        self._busy = False
        self._origin_page = getattr(getattr(retrieval, "dispatcher", None), "client", None)
        self._origin_page = getattr(self._origin_page, "page", None)

    def browser_action_count(self):
        states = {id(state): state for state in (self.agent_state, getattr(self.retrieval, "_scratch", None)) if state is not None}
        return sum(state.step_count for state in states.values())

    def record_verified_mutation(self, world):
        if self.mutation_journal is not None:
            self.mutation_journal.verified(world.record_key, operation_key(world))

    async def recover_read(self, strategy, action, goal, world):
        """Execute only known read/navigation routes, then independently read.

        Modal recovery abandons the obstructed page for the verified record;
        it never presses an unknown Close/OK/Continue control. If a native
        modal prevents navigation the bounded controller stops safely.
        """
        from licet.browser.dispatcher import ToolCall
        from licet.phase7.portal import PortalState, route_recovery
        allowed = {"WAIT_FOR_SETTLE", "RECOVER_FROM_HOME", "RETURN_TO_RECORD",
                   "RETURN_TO_ORIGIN_TAB", "CLOSE_INFORMATIONAL_MODAL", "DISMISS_OR_RETURN"}
        if self._busy or strategy not in allowed:
            return Observation(world, False, "unsupported or busy recovery route")
        current = route_recovery(PortalState.from_observation(world.browser_state))
        if current and current.terminal:
            return Observation(world, False, current.reason)
        dispatcher = self.retrieval.dispatcher
        if strategy == "RETURN_TO_ORIGIN_TAB":
            if self._origin_page is None:
                return Observation(world, False, "original browser context is unavailable")
            dispatcher.client.page = self._origin_page
        if strategy == "WAIT_FOR_SETTLE":
            result = await dispatcher.execute(ToolCall("wait", {"until_absent": "Loading...", "seconds": 1}), self.agent_state)
        else:
            url = self.retrieval._detail_url()
            if not url:
                return Observation(world, False, "no verified record anchor")
            result = await dispatcher.execute(ToolCall("navigate", {"url": url}), self.agent_state)
        if not result.get("success"):
            return Observation(world, False, "known-state recovery navigation/wait failed")
        # Do not reuse the failed page position. Ordinary retrieval establishes
        # identity from fresh content at the bound record URL.
        self.retrieval._current_url = None
        world.browser_state = {}
        return await self.perform(action, goal, world)

    async def perform(self, action, goal, world, *, confirmed=False, approval=None):
        if self._busy:
            return Observation(world, False, "prior capability still running; do not overlap portal operations")
        if action == Action.FIND_PERMIT:
            if not (goal.permit_id or goal.lookup_text):
                return Observation(world, False, "specify a permit number or address")
            query = PermitLookupRequest(record_number=goal.permit_id) if goal.permit_id else parse_lookup_request(goal.lookup_text)
            result = await self.lookup.run(goal.objective, query, self.agent_state)
            if result.status != LookupStatus.FOUND or not result.identity_verified or not result.permit or not result.permit.ref:
                return Observation(world, False, result.message or "permit identity not verified")
            world.permit_id = result.permit.permit_id
            world.record_key = result.permit.ref.as_key()
            world.permit_verified = True
            world.snapshot_id = digest(result.permit.model_dump(mode="json"))
            # Bind the existing read-only retriever to the verified Phase 2 ref.
            ref = result.permit.ref
            self.retrieval.record_ref = {"capID1": ref.cap_id1, "capID2": ref.cap_id2,
                "capID3": ref.cap_id3, "module": ref.module, "agency_code": ref.agency_code}
            world.browser_state = {"url": ref.detail_url()}
            return Observation(world, message="Phase 2 independently verified the permit")
        if action in READS:
            state = world.permit or PermitState(record_number=world.permit_id, record_key=world.record_key)
            # The retrieval runner records the *settled* page identity here
            # (Phase 7): the planner's loop key and the recovery routes read
            # where the page actually settled, not the pre-navigation URL.
            self.retrieval.browser_state = world.browser_state
            outcome = await self.retrieval.retrieve_missing_sections(state, [{"section": READS[action]}])
            if state.record_key != world.record_key or state.rejected_observations:
                return Observation(world, False, "foreign record observation refused")
            world.permit = state
            world.snapshot_id = digest(state.to_dict())
            world.reasoning = world.selection = world.proposal = None
            world.availability_checked = False
            if outcome.sections_failed:
                return Observation(world, False, f"section unavailable: {READS[action]}", retryable=True)
            return Observation(world, message=f"read {READS[action]}")
        if action == Action.DETERMINE_BLOCKERS:
            world.reasoning = await _call(self.reasoner, world.permit, goal.objective, snapshot_id=world.snapshot_id)
            if world.reasoning.record_key != world.record_key:
                return Observation(world, False, "reasoning addressed another record")
            # An unpaid amount alone is not a gate. Only explicit confirmed gates
            # relevant to inspection/current unknown scope stop this workflow.
            world.dependencies = tuple(ExternalDependency(b.type, b.description, tuple(b.evidence_ids))
                for b in world.reasoning.blockers if b.classification == "confirmed_gate"
                and (not b.affects_stage or "inspect" in b.affects_stage.lower()))
            if "permit_approved" in goal.success_conditions and world.permit.status_normalized != "APPROVED":
                world.dependencies += (ExternalDependency("municipality_review", "municipality approval is required"),)
            return Observation(world, message="Phase 3 interpreted structured state")
        if action == Action.DETERMINE_NEXT_INSPECTION:
            context = await _call(self.selection_context, deepcopy(world))
            if context.record_key != world.record_key or context.snapshot_id != world.snapshot_id:
                return Observation(world, False, "selection observations are stale or address another record")
            reasoning = deepcopy(world.reasoning)
            request = None
            if not goal.inspection_type and context.catalog_complete:
                required_options = [option for option in context.options
                                    if option.required is True and option.requirement_evidence_ids]
                if required_options:
                    # The live wizard's explicit marker and complete catalog
                    # supply requirement evidence not carried by Phase 3's
                    # summary-page retrieval. Replace only requirement-shaped
                    # candidates with these portal-backed candidates; all other
                    # plausible actions remain competitors and can cause an
                    # ambiguity stop. Never infer from offered types.
                    reasoning.next_actions = [
                        candidate for candidate in reasoning.next_actions
                        if not candidate.action.strip().casefold().startswith("complete required inspection:")
                    ]
                    reasoning.next_actions.extend(
                        NextActionCandidate(
                            f"Complete required inspection: {option.name}",
                            f"The scheduling wizard marks {option.name} as (required).",
                            0.75, requirement_strength="likely",
                            evidence_ids=list(option.requirement_evidence_ids),
                        )
                        for option in required_options
                    )
            if goal.inspection_type:
                request = InspectionAction(goal.operation, world.permit_id, goal.inspection_type,
                    goal.preferred_date, goal.date_window_start, goal.date_window_end,
                    goal.existing_inspection_id, list(goal.constraints))
                # Explicit user choice establishes target intent, not eligibility.
                # Portal evidence and all selection gates still apply below.
                from licet.phase4.matching import match_inspection_type
                name = match_inspection_type(goal.inspection_type, [o.name for o in context.options])
                option = next((o for o in context.options if o.name == name), None)
                if option:
                    verb = {"schedule": "Request", "reschedule": "Reschedule", "cancel": "Cancel"}[goal.operation]
                    reasoning.next_actions = [NextActionCandidate(f"{verb} inspection: {goal.inspection_type}",
                        "explicit user target; independently check prerequisites", 1.0,
                        requirement_strength="likely", evidence_ids=list(option.evidence_ids))]
            world.selection = select_inspection_action(reasoning, permit_id=world.permit_id, context=context, requested_action=request)
            if world.selection.action:
                world.proposal = replace(world.selection.action, preferred_date=goal.preferred_date,
                    date_window_start=goal.date_window_start, date_window_end=goal.date_window_end,
                    constraints=list(goal.constraints), record_key=world.record_key, snapshot_id=world.snapshot_id,
                    evidence_ids=world.selection.evidence_ids, requires_confirmation=world.selection.requires_confirmation)
            elif world.selection.status == SelectionStatus.ALREADY_SCHEDULED and goal.inspection_type:
                world.proposal = replace(request, record_key=world.record_key, snapshot_id=world.snapshot_id)
                # Independent verification, never declaring success from cached rows.
                return await self.perform(Action.VERIFY_STATE, goal, world)
            return Observation(world, message=world.selection.reason)
        if action == Action.CHECK_INSPECTION_AVAILABILITY:
            check = await _call(self.preflight, deepcopy(world))
            if (check.record_key != world.record_key or check.snapshot_id != world.snapshot_id
                    or check.operation_fingerprint != operation_key(world)):
                return Observation(world, False, "preflight does not match the selected record/snapshot/action")
            world.availability_checked, world.eligibility_verified = True, check.eligible
            world.preflight_fingerprint = check.operation_fingerprint
            world.available_dates, world.cost = check.available_dates, check.cost
            world.signature_required = check.signature_required
            world.dependencies, world.missing_information = check.dependencies, check.missing_information
            world.preflight_details = deepcopy(check.details)
            self._inputs = dict(check.required_inputs)
            return Observation(world, message="observed eligibility, availability and action prerequisites")
        if action in MUTATIONS:
            # The existing Phase 4 executor remains the sole mutation workflow.
            # The async portal bridge keeps browser objects on their owning loop.
            loop = asyncio.get_running_loop()
            portal = self.portal
            journal = self.mutation_journal
            mutation_key = operation_key(world)
            class Bridge:
                def read_inspection_state(self, *args):
                    if hasattr(portal, "read_inspection_state_async"):
                        return asyncio.run_coroutine_threadsafe(portal.read_inspection_state_async(*args), loop).result()
                    return portal.read_inspection_state(*args)
                def submit_inspection_action(self, *args, **kwargs):
                    if journal is not None and not journal.reserve(world.record_key, mutation_key):
                        raise RuntimeError("unresolved or completed mutation journal entry; no replay")
                    if hasattr(portal, "submit_inspection_action_async"):
                        return asyncio.run_coroutine_threadsafe(portal.submit_inspection_action_async(*args, **kwargs), loop).result()
                    return portal.submit_inspection_action(*args, **kwargs)
            executor = InspectionActionExecutor(Bridge(), policy_engine=self.policy_engine)
            self._busy = True
            worker = asyncio.create_task(asyncio.to_thread(executor.execute, world.proposal,
                eligible_types=[world.proposal.inspection_type], available_dates=world.available_dates,
                required_inputs=self._inputs, confirmed=confirmed, approval=approval))
            def finished(task):
                self._busy = False
                self.audits.extend(executor.audits)
                if not task.cancelled():
                    task.exception()  # consume late failure; never replay a timed-out mutation
            worker.add_done_callback(finished)
            world.result = await asyncio.shield(worker)
            return Observation(world, world.result.success and world.result.verified,
                world.result.error or "Phase 4 executed and checked the mutation", uncertain=not world.result.verified)
        if action == Action.VERIFY_STATE:
            if not world.proposal:
                return Observation(world, False, "no concrete inspection target to verify")
            args = (world.permit_id, world.proposal.inspection_type, world.proposal.existing_inspection_id)
            if hasattr(self.portal, "read_inspection_state_async"):
                observed = await self.portal.read_inspection_state_async(*args)
            else:
                observed = await asyncio.to_thread(self.portal.read_inspection_state, *args)
            if observed.record_key != world.record_key or observed.permit_id != world.permit_id:
                return Observation(world, False, "post-action record identity is unverified or mismatched")
            world.verified_inspection = observed
            return Observation(world, message="independently re-read the target inspection")
        return Observation(world, False, "unsupported semantic capability")
