"""Deterministic planner scenarios: capability observations, never LLM success."""
import asyncio
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import date

import pytest

from licet.phase3.state import PermitState, ReasoningResult, Uncertainty
from licet.phase4.actions import InspectionAction, InspectionActionResult, InspectionSnapshot, ActionVerificationState
from licet.phase4.selection import ActionSelection, SelectionStatus
from licet.phase5 import *
from licet.phase5.planner import mutation_denial, next_actions
from licet.phase5.acceptance import format_live_plan_only_summary, live_plan_only_summary
from licet.phase5.state import established, operation_key

from licet.eval.phase5_fixtures import KEY, goal, ready_world, ScriptedCapabilities


def run(g=None, cap=None, **options):
    capability = cap or ScriptedCapabilities()
    result = asyncio.run(GoalPlanner(capability, **options).run(g or goal()))
    return result, capability


@pytest.mark.parametrize("iteration", range(10))
def test_ten_consecutive_flagship_runs(iteration):
    g = parse_goal("Find the permit at 123 Main Street, figure out what is blocking it, and get it ready for its next inspection without paying any fees or signing anything.")
    result, cap = run(g)
    assert result.status == Status.SUCCESS
    assert cap.calls.count(Action.SCHEDULE_INSPECTION) == 1
    assert cap.calls[-1] == Action.VERIFY_STATE
    assert len(cap.calls) <= 8
    assert all(observed == g for observed in cap.seen_goals)
    assert {"payment", "signature"} <= set(g.prohibited_actions)


def test_already_scheduled_verified_state_stops_without_any_capability_call():
    world = ready_world()
    world.verified_inspection = InspectionSnapshot("P-1", "i1", "Rough Electrical", "Scheduled", "2026-09-24", record_key=KEY)
    cap = ScriptedCapabilities()
    result = asyncio.run(GoalPlanner(cap).run(goal(), world=world))
    assert result.status == Status.SUCCESS and not cap.calls


def test_fee_gate_yields_partial_without_payment():
    result, cap = run(cap=ScriptedCapabilities(blocker=ExternalDependency("payment", "Fee explicitly prevents scheduling")))
    assert result.status == Status.PARTIAL_SUCCESS
    assert result.error == Error.EXTERNAL_DEPENDENCY
    assert Action.SCHEDULE_INSPECTION not in cap.calls


@pytest.mark.parametrize("kind", ["inspector", "municipality", "document", "signature", "third_party"])
def test_external_dependency_never_fabricates_completion(kind):
    result, cap = run(cap=ScriptedCapabilities(blocker=ExternalDependency(kind, f"{kind} required")))
    assert result.status == Status.PARTIAL_SUCCESS
    assert Action.SCHEDULE_INSPECTION not in cap.calls


def test_missing_phone_requests_information_without_fabrication():
    result, cap = run(cap=ScriptedCapabilities(missing="phone number required"))
    assert "phone" in result.reason and result.status == Status.PARTIAL_SUCCESS
    assert Action.SCHEDULE_INSPECTION not in cap.calls


def test_uncertain_submission_is_verified_once_without_replay():
    result, cap = run(cap=ScriptedCapabilities(uncertain=True))
    assert result.status == Status.SUCCESS
    assert cap.calls.count(Action.SCHEDULE_INSPECTION) == 1
    assert cap.calls[-1] == Action.VERIFY_STATE


def test_uncertain_unverified_submission_never_reports_success():
    result, cap = run(cap=ScriptedCapabilities(uncertain=True, verify=False))
    assert result.status != Status.SUCCESS
    assert cap.calls.count(Action.SCHEDULE_INSPECTION) == 1


def test_confirmed_submit_without_matching_reread_is_not_success():
    result, cap = run(cap=ScriptedCapabilities(verify=False))
    assert result.status != Status.SUCCESS
    assert result.world.result.success  # provider assertion cannot establish goal


def test_loop_is_detected_on_failed_read():
    result, cap = run(cap=ScriptedCapabilities(failure=Action.READ_PERMIT_STATE))
    assert result.error == Error.PLAN_LOOP_DETECTED
    assert cap.calls.count(Action.READ_PERMIT_STATE) == 3  # initial read + two bounded retries


def test_budget_is_global():
    result, cap = run(max_steps=3)
    assert result.error == Error.STEP_BUDGET_EXCEEDED
    assert len(cap.calls) == 3


def test_persistent_constraints_are_deeply_immutable():
    values = ["no payments"]
    g = goal(constraints=values)
    values.append("changed")
    assert g.constraints == ("no payments",)
    with pytest.raises(FrozenInstanceError):
        g.autonomous = False


def test_conflicting_goal_stops_before_browser():
    result, cap = run(parse_goal("Schedule the inspection, but don't make any changes."))
    assert result.error == Error.GOAL_UNSATISFIABLE and not cap.calls


def test_vague_goal_only_gathers_information():
    result, cap = run(parse_goal("Fix my permit."))
    assert not result.goal.autonomous
    assert Action.SCHEDULE_INSPECTION not in cap.calls


def test_misleading_click_instruction_does_not_authorize_arbitrary_execution():
    result, cap = run(parse_goal("Just click whatever gets this approved."))
    assert Action.SCHEDULE_INSPECTION not in cap.calls


def test_no_cancellation_survives_replanning():
    result, cap = run(goal(operation="cancel", prohibited_actions=("cancel",), existing_inspection_id="i1"))
    assert Action.CANCEL_INSPECTION not in cap.calls
    assert result.status != Status.SUCCESS


@pytest.mark.parametrize("change", [
    {"record_key": "other"}, {"snapshot_id": "old"}, {"date_window_end": "2030-01-01"},
    {"action_type": "cancel"}, {"evidence_ids": ()},
])
def test_preflight_blocks_stale_or_changed_proposals(change):
    world = ready_world()
    world.proposal = replace(world.proposal, **change)
    assert mutation_denial(Run(goal(), world), Action.SCHEDULE_INSPECTION)


@pytest.mark.parametrize("cost,signature", [(None, False), (1, False), (0, None), (0, True)])
def test_unknown_cost_or_signature_cannot_bypass_constraints(cost, signature):
    world = ready_world()
    world.eligibility_verified = world.availability_checked = True
    world.preflight_fingerprint = operation_key(world)
    world.cost, world.signature_required = cost, signature
    assert mutation_denial(Run(goal(), world), Action.SCHEDULE_INSPECTION)


def test_run_report_distinguishes_observed_availability_from_unknown_cost_and_signature():
    world = ready_world()
    world.availability_checked = True
    world.eligibility_verified = True
    world.available_dates = ("2026-09-24",)
    world.cost = None
    world.signature_required = None
    world.preflight_details = {
        "availability": {"calendar_read": True, "availability_status": "available"},
        "cost": {"status": "unknown"},
        "signature": {"status": "unknown"},
    }

    preflight = Run(goal(), world).report()["preflight"]

    assert preflight["checked"] is True
    assert preflight["eligible"] is True
    assert preflight["available_dates"] == ["2026-09-24"]
    assert preflight["cost_status"] == "unknown" and preflight["cost"] is None
    assert preflight["signature_status"] == "unknown" and preflight["signature_required"] is None
    assert preflight["details"]["availability"]["calendar_read"] is True


def test_stop_reason_prioritizes_identity_verified_empty_calendar_with_observed_window():
    world = ready_world()
    world.eligibility_verified = True
    world.availability_checked = True
    world.available_dates = ()
    world.preflight_details = {
        "availability": {
            "calendar_read": True,
            "identity_verified": True,
            "availability_status": "none_in_observed_calendar",
            "calendar_months": [
                {"month": "Sep 2026", "active_day_count": 0},
                {"month": "Oct 2026", "active_day_count": 0},
                {"month": "Nov 2026", "active_day_count": 0},
            ],
        },
        "cost": {"status": "unknown"},
        "signature": {"status": "unknown"},
    }

    result = asyncio.run(GoalPlanner(ScriptedCapabilities()).run(goal(), world=world))

    assert result.status == Status.PARTIAL_SUCCESS
    assert result.error == Error.NO_SAFE_ACTIONS
    assert result.reason == (
        "No active inspection dates were available in the observed calendar window "
        "(Sep 2026, Oct 2026, Nov 2026)."
    )
    assert "cost" not in result.reason.casefold()
    assert "signature" not in result.reason.casefold()


def test_unobserved_calendar_does_not_get_described_as_no_available_dates():
    world = ready_world()
    world.eligibility_verified = True
    world.availability_checked = True
    world.available_dates = ()
    world.preflight_details = {"availability": {"calendar_read": False}}

    result = asyncio.run(GoalPlanner(ScriptedCapabilities()).run(goal(), world=world))

    assert result.reason == "supported"
    assert "No active inspection dates" not in result.reason


def test_live_plan_only_acceptance_summary_separates_blocker_from_unresolved_facts():
    report = {
        "goal": {"permit_id": "000000014"},
        "status": "PARTIAL_SUCCESS",
        "error": "NO_SAFE_ACTIONS",
        "trace": [{"action": "FIND_PERMIT", "success": True}],
        "preflight": {
            "checked": True,
            "inspection_type": "Brycer Inspection History",
            "available_dates": [],
            "details": {
                "availability": {
                    "calendar_read": True,
                    "identity_verified": True,
                    "availability_status": "none_in_observed_calendar",
                    "calendar_months": [{"month": "Sep 2026"}, {"month": "Nov 2026"}],
                },
                "cost": {"status": "unknown"},
                "signature": {"status": "unknown"},
            },
        },
        "metrics": {"mutations_attempted": 0, "mutations_verified": 0},
    }

    summary = live_plan_only_summary(report)
    rendered = format_live_plan_only_summary(summary)

    assert summary["acceptance_case"] == "LIVE_PLAN_ONLY_ACCEPTANCE"
    assert summary["blocker"] == (
        "No active inspection dates were available in the observed calendar window "
        "(Sep 2026, Nov 2026)."
    )
    assert summary["unresolved"] == {"cost": "not disclosed", "signature_requirement": "not disclosed"}
    assert summary["result"] == "PARTIAL_SUCCESS / NO_SAFE_ACTIONS"
    assert summary["calendar_identity_verified"] is True
    assert summary["mutations_attempted"] == 0
    assert "BLOCKER" in rendered and "UNRESOLVED" in rendered and "Mutations: 0" in rendered


def test_approval_is_explicit_and_bound_to_proposal():
    cap = ScriptedCapabilities()
    planner = GoalPlanner(cap, confirmation_required=frozenset({Action.SCHEDULE_INSPECTION}))
    paused = asyncio.run(planner.run(goal()))
    assert paused.status == Status.NEEDS_APPROVAL
    assert Action.SCHEDULE_INSPECTION not in cap.calls
    with pytest.raises(ValueError):
        asyncio.run(planner.resume(paused, token="wrong", approved=True))
    denied_pause = deepcopy(paused)
    denied = asyncio.run(planner.resume(denied_pause, token=denied_pause.approval_token, approved=False))
    assert denied.status == Status.PARTIAL_SUCCESS
    assert Action.SCHEDULE_INSPECTION not in cap.calls
    with pytest.raises(ValueError):
        asyncio.run(planner.resume(paused, token=paused.approval_token, approved=True))
    paused = asyncio.run(planner.run(goal()))
    completed = asyncio.run(planner.resume(paused, token=paused.approval_token, approved=True))
    assert completed.status == Status.SUCCESS
    assert cap.calls.count(Action.CHECK_INSPECTION_AVAILABILITY) == 3
    assert cap.calls.count(Action.SCHEDULE_INSPECTION) == 1


def test_bad_plan_dependencies_are_rejected():
    with pytest.raises(ValueError):
        Plan(goal(), [PlanStep("a", Action.READ_FEES, "x", ("missing",))]).validate()


def test_goal_cannot_request_unobservable_success_condition():
    with pytest.raises(ValueError):
        goal(success_conditions=("looks done",))


def test_only_relevant_sections_are_planned():
    w = ready_world()
    w.reasoning.needed_sections = [{"section": "fees"}]
    assert next_actions(Run(goal(), w)) == (Action.READ_FEES,)


def test_plan_revisions_and_trace_are_separate_from_browser_logs(tmp_path):
    trace = tmp_path / "semantic.jsonl"
    result, cap = run(trace_path=trace)
    assert len(result.plans) > 1
    assert len(trace.read_text().splitlines()) == len(cap.calls)
    assert result.report()["remaining_goal"] == []
    assert result.report()["metrics"]["efficiency"] > 0


@pytest.mark.parametrize("phrase", ["next week", "Friday", "after Wednesday", "before October 1"])
def test_date_constraints_are_frozen_at_parse_time(phrase):
    g = parse_goal(f"Schedule Rough Electrical inspection {phrase} for permit BLD-2026-0147.", reference=date(2026,9,20))
    assert g.inspection_type == "Rough Electrical"
    assert g.date_instruction == phrase
    assert any((g.date_window_start, g.date_window_end, g.preferred_date))


def test_approval_cannot_be_replayed_from_a_copied_pause():
    cap = ScriptedCapabilities()
    planner = GoalPlanner(cap, confirmation_required=frozenset({Action.SCHEDULE_INSPECTION}))
    paused = asyncio.run(planner.run(goal()))
    copy = deepcopy(paused)
    asyncio.run(planner.resume(paused, token=paused.approval_token, approved=True))
    with pytest.raises(ValueError):
        asyncio.run(planner.resume(copy, token=copy.approval_token, approved=True))
    assert cap.calls.count(Action.SCHEDULE_INSPECTION) == 1


def test_new_availability_invalidates_old_approval():
    class Changing(ScriptedCapabilities):
        async def perform(self, action, goal, world, **kwargs):
            obs = await super().perform(action, goal, world, **kwargs)
            if action == Action.CHECK_INSPECTION_AVAILABILITY and self.calls.count(action) > 1:
                obs.world.available_dates = ("2026-09-25",)
            return obs
    cap = Changing()
    planner = GoalPlanner(cap, confirmation_required=frozenset({Action.SCHEDULE_INSPECTION}))
    paused = asyncio.run(planner.run(goal()))
    token = paused.approval_token
    resumed = asyncio.run(planner.resume(paused, token=token, approved=True))
    assert resumed.status == Status.NEEDS_APPROVAL
    assert resumed.approval_token != token
    assert Action.SCHEDULE_INSPECTION not in cap.calls


def test_snapshot_label_churn_is_not_progress():
    w = ready_world()
    other = deepcopy(w)
    other.snapshot_id = "changed"
    other.reasoning.snapshot_id = "changed"
    other.selection = replace(other.selection, snapshot_id="changed")
    other.proposal = replace(other.proposal, snapshot_id="changed")
    assert w.fingerprint() == other.fingerprint()


def test_unexpected_expiry_replans_to_external_blocker():
    class Expired(ScriptedCapabilities):
        async def perform(self, action, goal, world, **kwargs):
            obs = await super().perform(action, goal, world, **kwargs)
            if action == Action.CHECK_INSPECTION_AVAILABILITY:
                obs.world.permit.status_normalized = "EXPIRED"
                obs.world.eligibility_verified = False
                obs.world.dependencies = (ExternalDependency("permit_expired", "Portal reports expired; renewal eligibility needs municipality review"),)
            return obs
    result, cap = run(cap=Expired())
    assert result.status == Status.PARTIAL_SUCCESS and "expired" in result.reason
    assert Action.SCHEDULE_INSPECTION not in cap.calls


@pytest.mark.parametrize("text", [
    "Get ready for inspection but don't schedule anything.",
    "Schedule Rough Electrical inspection without making any changes.",
    "Schedule Rough Electrical inspection next week except Friday.",
    "Schedule Rough Electrical inspection on 2026-10-01.",
])
def test_restrictions_cannot_disappear_during_goal_parsing(text):
    result, cap = run(parse_goal(text))
    assert result.status != Status.SUCCESS
    assert Action.SCHEDULE_INSPECTION not in cap.calls


def test_other_record_observation_is_rejected():
    class Foreign(ScriptedCapabilities):
        async def perform(self, action, goal, world, **kwargs):
            obs = await super().perform(action, goal, world, **kwargs)
            if action == Action.READ_PERMIT_STATE:
                obs.world.record_key = "foreign"
            return obs
    result, cap = run(cap=Foreign())
    assert result.status != Status.SUCCESS
    assert Action.SCHEDULE_INSPECTION not in cap.calls


def test_verified_display_id_alone_cannot_satisfy_success():
    world = ready_world()
    world.verified_inspection = InspectionSnapshot("P-1", "i1", "Rough Electrical", "Scheduled", "2026-09-24", record_key="foreign")
    assert "inspection_scheduled" not in established(world)


def test_initial_verified_world_cannot_substitute_another_permit():
    world = ready_world()
    world.permit_id = "OTHER-1"
    cap = ScriptedCapabilities()
    result = asyncio.run(GoalPlanner(cap).run(goal(), world=world))
    assert result.error == Error.PRECONDITION_NOT_MET and not cap.calls


def test_goal_rejects_mutable_nested_constraint_values():
    with pytest.raises(ValueError):
        goal(constraints=(["can change"],))


def test_preflight_cannot_mutate_using_stale_reasoning():
    world = ready_world()
    world.eligibility_verified = world.availability_checked = True
    world.preflight_fingerprint = operation_key(world)
    world.cost, world.signature_required = 0, False
    world.reasoning.snapshot_id = "stale"
    assert mutation_denial(Run(goal(), world), Action.SCHEDULE_INSPECTION)


# --- goal-language cases found by the Phase 8 `prompts` suite ---------------
# PROMPT-003 and PROMPT-006 were measured production parsing failures (see
# docs/phase8/final_review.md). The goldens were left alone and the parser was
# fixed instead, so each fix is pinned here rather than only in the benchmark.


def test_book_is_an_authorized_scheduling_request():
    g = parse_goal("Book Rough Electrical inspection for permit P-1.")
    assert g.operation == "schedule"
    assert g.autonomous and g.inspection_type == "Rough Electrical"
    result, cap = run(g)
    assert result.status == Status.SUCCESS
    assert cap.calls.count(Action.SCHEDULE_INSPECTION) == 1


def test_book_does_not_outrank_an_explicit_no_changes_restriction():
    # "Book" is an authorized scheduling verb, so the restriction has to win
    # explicitly: the run may report state, but it must not schedule.
    g = parse_goal("Book Rough Electrical inspection for permit P-1, but don't make any changes.")
    assert not g.autonomous
    assert {"schedule", "reschedule", "cancel"} <= set(g.prohibited_actions)
    result, cap = run(g)
    assert not (set(cap.calls) & {Action.SCHEDULE_INSPECTION, Action.RESCHEDULE_INSPECTION,
                                   Action.CANCEL_INSPECTION})


def test_read_only_blocker_question_is_an_answerable_read_not_a_conflict():
    g = parse_goal("Read only: what is blocking permit P-1?")
    assert not g.autonomous and g.clarification is None
    result, cap = run(g)
    assert result.status == Status.SUCCESS
    assert Action.SCHEDULE_INSPECTION not in cap.calls


def test_read_only_grant_cannot_authorize_a_mutation():
    # "Read only" is an information grant. Paired with a mutation verb it is a
    # conflict, so the planner stops before any capability call.
    g = parse_goal("Read only: schedule Rough Electrical inspection for permit P-1.")
    assert g.clarification == "CONSTRAINT_CONFLICT"
    result, cap = run(g)
    assert result.status == Status.BLOCKED and not cap.calls


def test_calendar_search_limit_is_reported_without_claiming_later_unavailability():
    from licet.phase5.planner import _observed_no_availability_reason
    from types import SimpleNamespace
    world = ready_world()
    world.availability_checked = True
    world.available_dates = ()
    world.preflight_details = {"availability": {
        "calendar_read": True, "identity_verified": True,
        "availability_status": "none_in_observed_calendar",
        "calendar_months": [{"month": "Oct 2027"}],
        "search_stop": "search_limit", "search_limit_windows": 12,
    }}
    reason = _observed_no_availability_reason(SimpleNamespace(world=world))
    assert "12 calendar-window limit" in reason
    assert "later availability is unknown" in reason
