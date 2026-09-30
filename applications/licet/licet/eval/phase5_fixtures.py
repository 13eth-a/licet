"""Scripted Phase 5 capability fixtures; no live browser or model calls."""
from __future__ import annotations

from dataclasses import replace
from datetime import date

from licet.phase3.state import PermitState, ReasoningResult
from licet.phase4.actions import InspectionAction, InspectionActionResult, InspectionSnapshot, ActionVerificationState
from licet.phase4.selection import ActionSelection, SelectionStatus
from licet.phase5 import Action, Error, Goal, Observation, Status, ExternalDependency
from licet.phase5.planner import GoalPlanner
from licet.phase5.state import World, operation_key

KEY = "NULLISLAND/Building/REC26/00000/00014"


def parse_goal(text: str, *, reference: date | None = None) -> Goal:
    from licet.phase5.goals import parse_goal as _parse
    return _parse(text, reference=reference)


def goal(**kwargs):
    defaults = dict(objective="get ready for next inspection", permit_id="P-1", autonomous=True,
        success_conditions=("permit_verified", "next_inspection_identified", "inspection_scheduled"))
    return Goal(**(defaults | kwargs))


def ready_world(g=None):
    g = g or goal()
    p = InspectionAction(g.operation, "P-1", "Rough Electrical", g.preferred_date,
        g.date_window_start, g.date_window_end, g.existing_inspection_id, list(g.constraints),
        KEY, "s1", ("e1",))
    return World(
        "P-1",
        KEY,
        "s1",
        True,
        PermitState(record_number="P-1", record_key=KEY, status_normalized="ISSUED"),
        ReasoningResult(KEY, "s1", g.objective, "answered"),
        ActionSelection(p, "supported", SelectionStatus.SELECTED, KEY, "s1", ("e1",)),
        p,
    )


class ScriptedCapabilities:
    def __init__(self, *, failure=None, blocker=None, missing=None, uncertain=False, verify=True):
        self.calls = []
        self.failure, self.blocker, self.missing = failure, blocker, missing
        self.uncertain, self.verify = uncertain, verify
        self.seen_goals = []

    async def perform(self, action, goal, world, *, confirmed=False, approval=None):
        self.calls.append(action)
        self.seen_goals.append(goal)
        if action == self.failure:
            return Observation(world, False, "portal unavailable", retryable=True)
        if action == Action.FIND_PERMIT:
            world.permit_id, world.record_key, world.snapshot_id, world.permit_verified = "P-1", KEY, "s1", True
        elif action in {Action.READ_PERMIT_STATE, Action.READ_INSPECTIONS, Action.READ_FEES}:
            world.permit = PermitState(record_key=KEY, record_number="P-1", status_normalized="ISSUED")
            world.reasoning = None
        elif action == Action.DETERMINE_BLOCKERS:
            world.reasoning = ReasoningResult(KEY, "s1", goal.objective, "answered")
        elif action == Action.DETERMINE_NEXT_INSPECTION:
            chosen = ready_world(goal)
            world.selection, world.proposal = chosen.selection, chosen.proposal
        elif action == Action.CHECK_INSPECTION_AVAILABILITY:
            world.available_dates = ("2026-09-24",)
            world.availability_checked = world.eligibility_verified = True
            world.preflight_fingerprint = operation_key(world)
            world.cost, world.signature_required = 0, False
            if self.blocker:
                world.dependencies = (self.blocker,)
            if self.missing:
                world.missing_information = (self.missing,)
        elif action in {Action.SCHEDULE_INSPECTION, Action.RESCHEDULE_INSPECTION, Action.CANCEL_INSPECTION}:
            if self.uncertain:
                raise TimeoutError("submit response lost")
            world.result = InspectionActionResult(True, goal.operation, "Rough Electrical", "2026-09-24",
                previous_date="2026-09-23", verified=True, verification_state=ActionVerificationState.VERIFIED_SUCCESS)
        elif action == Action.VERIFY_STATE:
            if not self.verify:
                return Observation(world, False, "no matching inspection")
            world.verified_inspection = InspectionSnapshot("P-1", goal.existing_inspection_id or "i1", "Rough Electrical",
                "Cancelled" if goal.operation == "cancel" else "Scheduled", "2026-09-24", record_key=KEY)
        return Observation(world, message="observed")


# ============================================================================
# Fixture addition: the checklist's 30-scenario deterministic planner set,
# as reusable data plus a library-level replay function. The scenario builder is
# intentionally small; each scenario is resolved through the production planner
# with a scripted capability, not asserted against an ad-hoc mock. That makes the
# set evidence about the *current* planner, not a narrative assumption.
# ============================================================================

class _ScenarioGoal:
    """Readable scenario header; the real goal is built by ``_scenario_goal_to_planner_goal``."""

    def __init__(self, text, constraints=(), prohibited=(), autonomous=True,
                 success_conditions=None, operation="schedule", inspection_type=None,
                 existing_inspection_id=None, date=None, date_window=None,
                 date_window_start=None, date_window_end=None,
                 clarification=None, vague=None):
        self.date_window_start = date_window_start
        self.date_window_end = date_window_end
        self.text = text
        self.constraints = constraints
        self.prohibited = prohibited
        self.autonomous = autonomous
        self.success_conditions = success_conditions
        self.operation = operation
        self.inspection_type = inspection_type
        self.existing_inspection_id = existing_inspection_id
        self.date = date
        self.date_window = date_window
        self.clarification = clarification
        self.vague = vague


class PlanScenario:
    """One deterministic planner scenario that the eval can replay and the suite can parametrize.

    Scenarios are plain data: readable by humans, replayable offline, and closed by a single
    outcome assertion plus metric assertions. The id prefix is the checklist group:
    ``SC`` simple completion, ``RP`` replanning, ``PC`` partial completion,
    ``CH`` constraint handling, ``EB`` external blockers, ``LE`` loop/error recovery.
    ``planner_kwargs`` lets one scenario exercise a bounded ``GoalPlanner`` (for example
    the shared step budget) without inventing a separate runner.
    """

    def __init__(self, id, name, goal, capability_factory=None, expected_status=Status.SUCCESS,
                 expected_error=None, expected_actions=None, expected_remaining=(),
                 assert_metrics=None, planner_kwargs=None):
        self.id = id
        self.name = name
        self.goal = goal
        self.capability_factory = capability_factory
        self.expected_status = expected_status
        self.expected_error = expected_error
        self.expected_actions = expected_actions
        self.expected_remaining = expected_remaining
        self.assert_metrics = assert_metrics or {}
        self.planner_kwargs = planner_kwargs or {}

    def resolved_goal(self):
        return _scenario_goal_to_planner_goal(self.goal)

    def resolve_capability(self):
        return (self.capability_factory() if self.capability_factory
                else ScriptedCapabilities())

    def run(self, **planner_kwargs):
        return _run_planner(self.resolve_capability(), self.resolved_goal(),
                            **(self.planner_kwargs | planner_kwargs))


def _scenario_goal_to_planner_goal(spec):
    parsed = parse_goal(spec.text)
    if not spec.autonomous:
        parsed = replace(parsed, autonomous=False)
    if spec.constraints:
        current = set(parsed.constraints)
        current.update(spec.constraints)
        parsed = replace(parsed, constraints=tuple(sorted(current)))
    if spec.prohibited:
        current = set(parsed.prohibited_actions)
        current.update(spec.prohibited)
        parsed = replace(parsed, prohibited_actions=tuple(sorted(current)))
    if spec.success_conditions:
        parsed = replace(parsed, success_conditions=tuple(spec.success_conditions))
    if spec.operation and spec.operation != parsed.operation:
        parsed = replace(parsed, operation=spec.operation)
    if spec.inspection_type and spec.inspection_type != parsed.inspection_type:
        parsed = replace(parsed, inspection_type=spec.inspection_type)
    if spec.existing_inspection_id is not None:
        parsed = replace(parsed, existing_inspection_id=spec.existing_inspection_id)
    if spec.date is not None:
        parsed = replace(parsed, preferred_date=spec.date.isoformat())
    if spec.date_window is not None:
        start, end = spec.date_window
        parsed = replace(parsed,
            date_window_start=start.isoformat() if start else None,
            date_window_end=end.isoformat() if end else None)
    if spec.date_window_start is not None or spec.date_window_end is not None:
        parsed = replace(
            parsed,
            date_window_start=spec.date_window_start.isoformat() if isinstance(spec.date_window_start, date) else spec.date_window_start,
            date_window_end=spec.date_window_end.isoformat() if isinstance(spec.date_window_end, date) else spec.date_window_end,
        )
    if spec.clarification is not None:
        parsed = replace(parsed, clarification=spec.clarification)
    if spec.vague is not None:
        parsed = replace(parsed, vague=spec.vague)
    return parsed


def _run_planner(cap, g, **planner_kwargs):
    import asyncio
    return asyncio.run(GoalPlanner(cap, **planner_kwargs).run(g))


# --- small capability factories used by the 30-scenario set --------------------

def _ineligible_capability():
    class Cap(ScriptedCapabilities):
        async def perform(self, action, goal, world, **kwargs):
            obs = await super().perform(action, goal, world, **kwargs)
            if action == Action.CHECK_INSPECTION_AVAILABILITY:
                obs.world.eligibility_verified = False
            return obs
    return Cap()


def _no_dates_capability():
    class Cap(ScriptedCapabilities):
        async def perform(self, action, goal, world, **kwargs):
            obs = await super().perform(action, goal, world, **kwargs)
            if action == Action.CHECK_INSPECTION_AVAILABILITY:
                obs.world.available_dates = ()
            return obs
    return Cap()


def _expired_capability():
    class Cap(ScriptedCapabilities):
        async def perform(self, action, goal, world, **kwargs):
            obs = await super().perform(action, goal, world, **kwargs)
            if action == Action.CHECK_INSPECTION_AVAILABILITY:
                obs.world.permit.status_normalized = "EXPIRED"
                obs.world.eligibility_verified = False
                obs.world.dependencies = (
                    ExternalDependency("permit_expired", "Portal reports expired; renewal eligibility needs municipality review"),
                )
            return obs
    return Cap()


def _contradictory_capability():
    class Cap(ScriptedCapabilities):
        async def perform(self, action, goal, world, **kwargs):
            obs = await super().perform(action, goal, world, **kwargs)
            if action == Action.DETERMINE_BLOCKERS and obs.world.reasoning is not None:
                obs.world.reasoning.contradictions = ["inspection history disagrees"]
                obs.world.reasoning.answerability = "conflicting"
            return obs
    return Cap()


def _costly_capability(cost, *, signature=False):
    class Cap(ScriptedCapabilities):
        async def perform(self, action, goal, world, **kwargs):
            obs = await super().perform(action, goal, world, **kwargs)
            if action == Action.CHECK_INSPECTION_AVAILABILITY:
                obs.world.cost, obs.world.signature_required = cost, signature
            return obs
    return Cap()


def _widening_capability():
    class Cap(ScriptedCapabilities):
        async def perform(self, action, goal, world, **kwargs):
            obs = await super().perform(action, goal, world, **kwargs)
            if action == Action.DETERMINE_NEXT_INSPECTION:
                obs.world.proposal = replace(obs.world.proposal, date_window_end="2030-01-01")
            return obs
    return Cap()


def _double_submit_capability():
    class Cap(ScriptedCapabilities):
        def __init__(self):
            super().__init__()
            self._attempted = set()

        async def perform(self, action, goal, world, **kwargs):
            obs = await super().perform(action, goal, world, **kwargs)
            if action in {Action.SCHEDULE_INSPECTION, Action.RESCHEDULE_INSPECTION, Action.CANCEL_INSPECTION}:
                key = operation_key(world)
                if key in self._attempted:
                    raise RuntimeError("replay refused by fixture")
                self._attempted.add(key)
            return obs
    return Cap()


def _churning_capability():
    """A portal that hands out a fresh snapshot label on every read while the
    structured state never advances. The World fingerprint deliberately ignores
    snapshot labels, so this must read as *no progress* and stop as a loop
    instead of letting label churn reset the progress detector.
    """

    class Cap(ScriptedCapabilities):
        def __init__(self):
            super().__init__()
            self.sequence = 0

        async def perform(self, action, goal, world, **kwargs):
            self.calls.append(action)
            self.seen_goals.append(goal)
            self.sequence += 1
            if action == Action.FIND_PERMIT:
                world.permit_id, world.record_key, world.permit_verified = "P-1", KEY, True
            # Every subsequent read "succeeds" with a new snapshot label and no
            # new facts; the label change must not count as progress.
            world.snapshot_id = f"churn-{self.sequence}"
            if world.reasoning is not None:
                world.reasoning.snapshot_id = f"churn-{self.sequence}"
            return Observation(world, message="observed")

    return Cap()


def planner_scenarios():
    """The Phase 5 checklist's deterministic planner set, as data.

    5 simple goal completion
    5 replanning
    5 partial completion
    5 constraint handling
    5 external blockers
    5 loop/error recovery
    """
    S = []

    # ---- 5 simple goal completion ----

    S.append(PlanScenario(
        id="SC01", name="simple flagship completion",
        goal=_ScenarioGoal(text="Find the permit at 123 Main Street, figure out what is blocking it, and get it ready for its next inspection without paying any fees or signing anything."),
        expected_status=Status.SUCCESS,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_NEXT_INSPECTION", "CHECK_INSPECTION_AVAILABILITY", "SCHEDULE_INSPECTION", "VERIFY_STATE"],
        expected_remaining=(),
        assert_metrics=dict(goal_completion_rate=1.0, duplicate_action_rate=0.0, planner_loop_rate=0.0, mutations_attempted=1),
    ))

    S.append(PlanScenario(
        id="SC02", name="simple already scheduled completion",
        goal=_ScenarioGoal(text="Schedule Rough Electrical inspection for permit P-1."),
        capability_factory=lambda: ScriptedCapabilities(),
        expected_status=Status.SUCCESS,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_NEXT_INSPECTION", "CHECK_INSPECTION_AVAILABILITY", "SCHEDULE_INSPECTION", "VERIFY_STATE"],
        expected_remaining=(),
        assert_metrics=dict(goal_completion_rate=1.0, mutations_attempted=1),
    ))

    S.append(PlanScenario(
        id="SC03", name="simple reschedule completion",
        goal=_ScenarioGoal(
            text="Reschedule the Rough Electrical inspection for permit P-1.",
            operation="reschedule",
            existing_inspection_id="i1",
        ),
        expected_status=Status.SUCCESS,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_NEXT_INSPECTION", "CHECK_INSPECTION_AVAILABILITY", "RESCHEDULE_INSPECTION", "VERIFY_STATE"],
        expected_remaining=(),
        assert_metrics=dict(goal_completion_rate=1.0, mutations_attempted=1),
    ))

    S.append(PlanScenario(
        id="SC04", name="simple inspection type from explicit request",
        goal=_ScenarioGoal(
            text="Schedule the Rough Electrical inspection for permit P-1.",
            inspection_type="Rough Electrical",
        ),
        expected_status=Status.SUCCESS,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_NEXT_INSPECTION", "CHECK_INSPECTION_AVAILABILITY", "SCHEDULE_INSPECTION", "VERIFY_STATE"],
        expected_remaining=(),
        assert_metrics=dict(goal_completion_rate=1.0, mutations_attempted=1),
    ))

    S.append(PlanScenario(
        id="SC05", name="simple next-week scheduling",
        goal=_ScenarioGoal(
            text="Schedule Rough Electrical inspection next week for permit P-1.",
            date_window_start=date(2026, 9, 21),
            date_window_end=date(2026, 9, 27),
        ),
        expected_status=Status.SUCCESS,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_NEXT_INSPECTION", "CHECK_INSPECTION_AVAILABILITY", "SCHEDULE_INSPECTION", "VERIFY_STATE"],
        expected_remaining=(),
        assert_metrics=dict(goal_completion_rate=1.0, mutations_attempted=1),
    ))

    # ---- 5 replanning ----

    S.append(PlanScenario(
        id="RP01", name="replanning after failed read loops and stops",
        goal=_ScenarioGoal(text="Schedule Rough Electrical inspection for permit P-1."),
        capability_factory=lambda: ScriptedCapabilities(failure=Action.READ_PERMIT_STATE),
        expected_status=Status.BLOCKED,
        expected_error=Error.PLAN_LOOP_DETECTED,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE"],
        expected_remaining=("next_inspection_identified", "inspection_scheduled"),
        assert_metrics=dict(planner_loop_rate=1.0, mutations_attempted=0),
    ))

    S.append(PlanScenario(
        id="RP02", name="replanning after eligibility failure stops without mutation",
        goal=_ScenarioGoal(text="Schedule Rough Electrical inspection for permit P-1."),
        capability_factory=_ineligible_capability,
        expected_status=Status.PARTIAL_SUCCESS,
        expected_error=Error.NO_SAFE_ACTIONS,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_NEXT_INSPECTION", "CHECK_INSPECTION_AVAILABILITY"],
        expected_remaining=("inspection_scheduled",),
        # No mutation is attempted, so the recorded violation rate is unmeasured
        # (None), not a fabricated zero.
        assert_metrics=dict(constraint_violation_rate=None, mutations_attempted=0),
    ))

    S.append(PlanScenario(
        id="RP03", name="replanning after unavailable dates stops without mutation",
        goal=_ScenarioGoal(text="Schedule Rough Electrical inspection for permit P-1."),
        capability_factory=_no_dates_capability,
        expected_status=Status.PARTIAL_SUCCESS,
        expected_error=Error.NO_SAFE_ACTIONS,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_NEXT_INSPECTION", "CHECK_INSPECTION_AVAILABILITY"],
        expected_remaining=("inspection_scheduled",),
        assert_metrics=dict(mutations_attempted=0),
    ))

    S.append(PlanScenario(
        id="RP04", name="replanning after unexpected expiry reports blocker",
        goal=_ScenarioGoal(text="Schedule Rough Electrical inspection for permit P-1."),
        capability_factory=_expired_capability,
        expected_status=Status.PARTIAL_SUCCESS,
        expected_error=Error.EXTERNAL_DEPENDENCY,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_NEXT_INSPECTION", "CHECK_INSPECTION_AVAILABILITY"],
        expected_remaining=("inspection_scheduled",),
        assert_metrics=dict(mutations_attempted=0),
    ))

    S.append(PlanScenario(
        id="RP05", name="replanning on contradictory portal state stops safely",
        goal=_ScenarioGoal(text="Get this permit ready for its next inspection."),
        capability_factory=_contradictory_capability,
        expected_status=Status.BLOCKED,
        expected_error=Error.NO_SAFE_ACTIONS,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_BLOCKERS"],
        expected_remaining=("next_inspection_identified", "inspection_scheduled"),
        assert_metrics=dict(mutations_attempted=0),
    ))

    # ---- 5 partial completion ----

    S.append(PlanScenario(
        id="PC01", name="partial completion when fee gate blocks without payment",
        goal=_ScenarioGoal(text="Get this permit ready for its next inspection without spending money."),
        capability_factory=lambda: ScriptedCapabilities(blocker=ExternalDependency("payment", "Fee explicitly prevents scheduling")),
        expected_status=Status.PARTIAL_SUCCESS,
        expected_error=Error.EXTERNAL_DEPENDENCY,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_NEXT_INSPECTION", "CHECK_INSPECTION_AVAILABILITY"],
        expected_remaining=("inspection_scheduled",),
        assert_metrics=dict(partial_completion_rate=1.0, mutations_attempted=0),
    ))

    S.append(PlanScenario(
        id="PC02", name="partial completion when inspector approval is external",
        goal=_ScenarioGoal(text="Get this permit moving."),
        capability_factory=lambda: ScriptedCapabilities(blocker=ExternalDependency("inspector", "inspector must sign off")),
        expected_status=Status.PARTIAL_SUCCESS,
        expected_error=Error.EXTERNAL_DEPENDENCY,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_NEXT_INSPECTION", "CHECK_INSPECTION_AVAILABILITY"],
        expected_remaining=("inspection_scheduled",),
        assert_metrics=dict(partial_completion_rate=1.0, mutations_attempted=0),
    ))

    S.append(PlanScenario(
        id="PC03", name="partial completion when municipality review is external",
        goal=_ScenarioGoal(text="Get me as close to approval as possible."),
        capability_factory=lambda: ScriptedCapabilities(blocker=ExternalDependency("municipality", "review pending")),
        expected_status=Status.PARTIAL_SUCCESS,
        expected_error=Error.EXTERNAL_DEPENDENCY,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_NEXT_INSPECTION", "CHECK_INSPECTION_AVAILABILITY"],
        expected_remaining=("inspection_scheduled",),
        assert_metrics=dict(partial_completion_rate=1.0, mutations_attempted=0),
    ))

    S.append(PlanScenario(
        id="PC04", name="partial completion when document is external",
        goal=_ScenarioGoal(text="Get this permit ready for its next inspection."),
        capability_factory=lambda: ScriptedCapabilities(blocker=ExternalDependency("document", "inspector-uploaded document required")),
        expected_status=Status.PARTIAL_SUCCESS,
        expected_error=Error.EXTERNAL_DEPENDENCY,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_NEXT_INSPECTION", "CHECK_INSPECTION_AVAILABILITY"],
        expected_remaining=("inspection_scheduled",),
        assert_metrics=dict(partial_completion_rate=1.0, mutations_attempted=0),
    ))

    S.append(PlanScenario(
        id="PC05", name="partial completion when signature is external",
        goal=_ScenarioGoal(text="Get this permit ready for its next inspection without signing anything."),
        capability_factory=lambda: ScriptedCapabilities(blocker=ExternalDependency("signature", "legal signature required")),
        expected_status=Status.PARTIAL_SUCCESS,
        expected_error=Error.EXTERNAL_DEPENDENCY,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_NEXT_INSPECTION", "CHECK_INSPECTION_AVAILABILITY"],
        expected_remaining=("inspection_scheduled",),
        assert_metrics=dict(partial_completion_rate=1.0, mutations_attempted=0),
    ))

    # ---- 5 constraint handling ----

    S.append(PlanScenario(
        id="CH01", name="constraint preserved across replanning with no-spend",
        goal=_ScenarioGoal(
            text="Get this permit ready for its next inspection without spending money.",
            constraints=("without spending money",),
            prohibited=("payment",),
        ),
        capability_factory=lambda: _costly_capability(cost=1.0),
        expected_status=Status.PARTIAL_SUCCESS,
        expected_error=Error.PRECONDITION_NOT_MET,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_NEXT_INSPECTION", "CHECK_INSPECTION_AVAILABILITY"],
        expected_remaining=("inspection_scheduled",),
        # The constraint held and stopped the run before any mutation, so the
        # recorded violation rate is unmeasured (None), not a fabricated zero.
        assert_metrics=dict(constraint_violation_rate=None, mutations_attempted=0),
    ))

    S.append(PlanScenario(
        id="CH02", name="constraint preserved across replanning with no-signature",
        goal=_ScenarioGoal(
            text="Get this permit ready for its next inspection without signing anything.",
            prohibited=("signature",),
        ),
        capability_factory=lambda: _costly_capability(cost=0.0, signature=True),
        expected_status=Status.PARTIAL_SUCCESS,
        expected_error=Error.PRECONDITION_NOT_MET,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_NEXT_INSPECTION", "CHECK_INSPECTION_AVAILABILITY"],
        expected_remaining=("inspection_scheduled",),
        assert_metrics=dict(constraint_violation_rate=None, mutations_attempted=0),
    ))

    S.append(PlanScenario(
        id="CH03", name="contradictory instruction halts as CONSTRAINT_CONFLICT",
        # "Cancel ... but do not cancel anything" is the checklist's contradictory
        # instruction: it cannot be resolved into one reading, so Phase 6's
        # `detect_constraint_conflict` halts the run before any browser access
        # rather than proceeding and refusing later. Prohibited-cancellation
        # without a contradiction is still covered by CH05.
        goal=_ScenarioGoal(
            text="Cancel the Rough Electrical inspection for permit P-1, but do not cancel anything.",
            operation="cancel",
            existing_inspection_id="i1",
            prohibited=("cancel",),
        ),
        expected_status=Status.BLOCKED,
        expected_error=Error.GOAL_UNSATISFIABLE,
        expected_actions=[],
        expected_remaining=("permit_verified", "next_inspection_identified", "inspection_cancelled"),
        assert_metrics=dict(constraint_violation_rate=None, mutations_attempted=0),
    ))

    S.append(PlanScenario(
        id="CH04", name="constraint preserved across replanning with date window restriction",
        goal=_ScenarioGoal(
            text="Schedule Rough Electrical inspection for permit P-1.",
            date_window_end=date(2026, 9, 30),
        ),
        capability_factory=_widening_capability,
        expected_status=Status.PARTIAL_SUCCESS,
        expected_error=Error.PRECONDITION_NOT_MET,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_NEXT_INSPECTION", "CHECK_INSPECTION_AVAILABILITY"],
        expected_remaining=("inspection_scheduled",),
        assert_metrics=dict(constraint_violation_rate=None, mutations_attempted=0),
    ))

    S.append(PlanScenario(
        id="CH05", name="constraint preserved across replanning with prohibited operation",
        goal=_ScenarioGoal(
            text="Cancel the Rough Electrical inspection for permit P-1.",
            operation="cancel",
            existing_inspection_id="i1",
            prohibited=("cancel", "payment", "signature"),
        ),
        expected_status=Status.PARTIAL_SUCCESS,
        expected_error=Error.PRECONDITION_NOT_MET,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_NEXT_INSPECTION", "CHECK_INSPECTION_AVAILABILITY"],
        expected_remaining=("inspection_cancelled",),
        assert_metrics=dict(constraint_violation_rate=None, mutations_attempted=0),
    ))

    # ---- 5 external blockers ----

    S.append(PlanScenario(
        id="EB01", name="external blocker inspector required",
        goal=_ScenarioGoal(text="Get this permit ready for its next inspection."),
        capability_factory=lambda: ScriptedCapabilities(blocker=ExternalDependency("inspector", "an inspector must sign off")),
        expected_status=Status.PARTIAL_SUCCESS,
        expected_error=Error.EXTERNAL_DEPENDENCY,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_NEXT_INSPECTION", "CHECK_INSPECTION_AVAILABILITY"],
        expected_remaining=("inspection_scheduled",),
        assert_metrics=dict(mutations_attempted=0),
    ))

    S.append(PlanScenario(
        id="EB02", name="external blocker municipality review required",
        goal=_ScenarioGoal(text="Get this permit ready for its next inspection."),
        capability_factory=lambda: ScriptedCapabilities(blocker=ExternalDependency("municipality", "municipality review required")),
        expected_status=Status.PARTIAL_SUCCESS,
        expected_error=Error.EXTERNAL_DEPENDENCY,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_NEXT_INSPECTION", "CHECK_INSPECTION_AVAILABILITY"],
        expected_remaining=("inspection_scheduled",),
        assert_metrics=dict(mutations_attempted=0),
    ))

    S.append(PlanScenario(
        id="EB03", name="external blocker document required",
        goal=_ScenarioGoal(text="Get this permit ready for its next inspection."),
        capability_factory=lambda: ScriptedCapabilities(blocker=ExternalDependency("document", "document required")),
        expected_status=Status.PARTIAL_SUCCESS,
        expected_error=Error.EXTERNAL_DEPENDENCY,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_NEXT_INSPECTION", "CHECK_INSPECTION_AVAILABILITY"],
        expected_remaining=("inspection_scheduled",),
        assert_metrics=dict(mutations_attempted=0),
    ))

    S.append(PlanScenario(
        id="EB04", name="external blocker signature required",
        goal=_ScenarioGoal(text="Get this permit ready for its next inspection without signing anything."),
        capability_factory=lambda: ScriptedCapabilities(blocker=ExternalDependency("signature", "signature required")),
        expected_status=Status.PARTIAL_SUCCESS,
        expected_error=Error.EXTERNAL_DEPENDENCY,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_NEXT_INSPECTION", "CHECK_INSPECTION_AVAILABILITY"],
        expected_remaining=("inspection_scheduled",),
        assert_metrics=dict(mutations_attempted=0),
    ))

    S.append(PlanScenario(
        id="EB05", name="external blocker third_party required",
        goal=_ScenarioGoal(text="Get this permit ready for its next inspection."),
        capability_factory=lambda: ScriptedCapabilities(blocker=ExternalDependency("third_party", "third-party approval required")),
        expected_status=Status.PARTIAL_SUCCESS,
        expected_error=Error.EXTERNAL_DEPENDENCY,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_NEXT_INSPECTION", "CHECK_INSPECTION_AVAILABILITY"],
        expected_remaining=("inspection_scheduled",),
        assert_metrics=dict(mutations_attempted=0),
    ))

    # ---- 5 loop/error recovery ----

    S.append(PlanScenario(
        id="LE01", name="loop recovery on repeated failed read",
        goal=_ScenarioGoal(text="Schedule Rough Electrical inspection for permit P-1."),
        capability_factory=lambda: ScriptedCapabilities(failure=Action.READ_PERMIT_STATE),
        expected_status=Status.BLOCKED,
        expected_error=Error.PLAN_LOOP_DETECTED,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE"],
        expected_remaining=("next_inspection_identified", "inspection_scheduled"),
        assert_metrics=dict(planner_loop_rate=1.0, mutations_attempted=0),
    ))

    S.append(PlanScenario(
        id="LE02", name="loop recovery on budget exhaustion",
        goal=_ScenarioGoal(text="Schedule Rough Electrical inspection for permit P-1."),
        capability_factory=lambda: ScriptedCapabilities(),
        planner_kwargs=dict(max_steps=3),
        expected_status=Status.PARTIAL_SUCCESS,
        expected_error=Error.STEP_BUDGET_EXCEEDED,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS"],
        expected_remaining=("next_inspection_identified", "inspection_scheduled"),
        assert_metrics=dict(mutations_attempted=0, average_semantic_steps=3),
    ))

    S.append(PlanScenario(
        id="LE03", name="loop recovery on repeated mutation attempt",
        goal=_ScenarioGoal(text="Schedule Rough Electrical inspection for permit P-1."),
        capability_factory=_double_submit_capability,
        expected_status=Status.SUCCESS,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_NEXT_INSPECTION", "CHECK_INSPECTION_AVAILABILITY", "SCHEDULE_INSPECTION", "VERIFY_STATE"],
        expected_remaining=(),
        # The no-replay guard means the mutation is submitted exactly once, so
        # there is no duplicate action even though the fixture would refuse one.
        assert_metrics=dict(duplicate_action_rate=0.0, mutations_attempted=1),
    ))

    S.append(PlanScenario(
        id="LE04", name="loop recovery on churning portal",
        goal=_ScenarioGoal(text="Schedule Rough Electrical inspection for permit P-1."),
        capability_factory=_churning_capability,
        expected_status=Status.BLOCKED,
        expected_error=Error.PLAN_LOOP_DETECTED,
        # Only the identity read advances state; each further read changes the
        # snapshot label but no fact, so progress detection stops the loop.
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "READ_PERMIT_STATE"],
        expected_remaining=("next_inspection_identified", "inspection_scheduled"),
        assert_metrics=dict(planner_loop_rate=1.0, mutations_attempted=0),
    ))

    S.append(PlanScenario(
        id="LE05", name="loop recovery on unexpected expiry",
        goal=_ScenarioGoal(text="Schedule Rough Electrical inspection for permit P-1."),
        capability_factory=_expired_capability,
        expected_status=Status.PARTIAL_SUCCESS,
        expected_error=Error.EXTERNAL_DEPENDENCY,
        expected_actions=["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_NEXT_INSPECTION", "CHECK_INSPECTION_AVAILABILITY"],
        expected_remaining=("inspection_scheduled",),
        assert_metrics=dict(mutations_attempted=0),
    ))

    return S
