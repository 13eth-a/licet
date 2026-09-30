"""Adversarial planner review (DeepSeek V4.1 Flash) — attack the loop, then lock it.

Each case is a counterexample that was reproduced against the pre-review planner
(or is a positive control proving the guard is not vacuous). The targets are the
Phase 5 hazards named in the assignment: constraint loss across replanning,
circular planning, repeated information gathering, "success" without verified
state, a missing precondition, an ignored external dependency, and treating
partial completion as full success.

Reasons are stated in terms of the planner's *own* contracts: a completion fact
must be at least as well-evidenced as the execution gate that guards the same
work, and no observed fact may be bound to another record, snapshot, or question.
"""
import asyncio
from copy import deepcopy
from dataclasses import replace

import pytest

from licet.phase3.state import Uncertainty
from licet.phase4.actions import InspectionSnapshot
from licet.phase4.policy import decide_action_policy
from licet.phase5 import *
from licet.phase5.planner import mutation_denial, next_actions
from licet.phase5.state import MUTATIONS, established, operation_key, reasoning_is_sound

from licet.eval.phase5_fixtures import KEY, goal, ready_world, ScriptedCapabilities


def run(g=None, cap=None, world=None, **options):
    capability = cap or ScriptedCapabilities()
    result = asyncio.run(GoalPlanner(capability, **options).run(g or goal(), world=world))
    return result, capability


VAGUE = "Fix my permit."

MUTATION_VALUES = {action.value for action in MUTATIONS}


# --------------------------------------------------------------------------- #
# A1-A5: an interpretation may only establish completion for THIS record,      #
# snapshot and question. The pre-review predicate accepted `answerability ==   #
# "answered"` alone, then declared SUCCESS before the planner's own STOP,      #
# contradiction and stale-snapshot guards could run.                           #
# --------------------------------------------------------------------------- #

class _Reasoning:
    """Injects one modified ReasoningResult through the capability interface."""

    def __init__(self, mutate):
        self.mutate = mutate

    def capabilities(self):
        mutate = self.mutate

        class Capabilities(ScriptedCapabilities):
            async def perform(self, action, goal, world, **kwargs):
                observation = await super().perform(action, goal, world, **kwargs)
                if action == Action.DETERMINE_BLOCKERS and observation.world.reasoning is not None:
                    mutate(observation.world.reasoning)
                return observation

        return Capabilities()


def _stale(reasoning):
    reasoning.snapshot_id = "previous-snapshot"


def _contradictory(reasoning):
    reasoning.contradictions = ["fee row says paid; balance row says unpaid"]


def _needs_a_section(reasoning):
    reasoning.needed_sections = [{"section": "fees"}]


def _blocking_uncertainty(reasoning):
    reasoning.uncertainties = [Uncertainty(
        "ordering unknown", "which inspection attempt is current", blocks_answer=True)]


def _another_question(reasoning):
    reasoning.question = "Is this permit approved yet?"


@pytest.mark.parametrize("mutate,label", [
    (_stale, "stale snapshot"),
    (_contradictory, "contradiction"),
    (_needs_a_section, "unread needed section"),
    (_blocking_uncertainty, "blocks-answer uncertainty"),
    (_another_question, "different question"),
])
def test_unsound_reasoning_cannot_establish_blockers_identified(mutate, label):
    world = ready_world()
    mutate(world.reasoning)
    assert "blockers_identified" not in established(world, goal()), label
    assert not reasoning_is_sound(world, goal()), label


def test_sound_reasoning_still_establishes_blockers_identified():
    # Positive control: the guard must reject the five deviations above, not
    # every reasoning result.
    world = ready_world()
    assert reasoning_is_sound(world, goal())
    assert "blockers_identified" in established(world, goal())


@pytest.mark.parametrize("mutate", [_stale, _contradictory, _blocking_uncertainty, _another_question])
def test_unsound_reasoning_never_reaches_success_end_to_end(mutate):
    result, capabilities = run(parse_goal(VAGUE), _Reasoning(mutate).capabilities())
    assert result.status != Status.SUCCESS
    assert "blockers_identified" in result.report()["remaining_goal"]
    assert not any(action.value in MUTATION_VALUES for action in capabilities.calls)


def test_reasoner_demanding_an_unread_section_cannot_declare_success():
    # A reasoner that keeps asking for a section must either get that section or
    # stop; it must never satisfy the goal by asserting it is answered.
    result, capabilities = run(parse_goal(VAGUE), _Reasoning(_needs_a_section).capabilities())
    assert result.status != Status.SUCCESS
    assert Action.READ_FEES in capabilities.calls
    assert result.semantic_steps <= 20


def test_reasoning_record_mismatch_still_cannot_establish_completion():
    # The one binding the pre-review predicate did have; kept as a lock.
    world = ready_world()
    world.reasoning.record_key = "another/record"
    assert "blockers_identified" not in established(world, goal())


# --------------------------------------------------------------------------- #
# A6: `next_inspection_identified` was granted from any proposal at all.       #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("change", [
    {"record_key": "another/record"},
    {"snapshot_id": "previous-snapshot"},
    {"record_key": None, "snapshot_id": None},
])
def test_unbound_proposal_cannot_satisfy_next_inspection_identified(change):
    world = ready_world()
    world.proposal = replace(world.proposal, **change)
    world.selection = replace(world.selection, **change)
    assert "next_inspection_identified" not in established(world, goal())
    # mutation_denial agrees with the completion predicate; the two must not
    # disagree about the same proposal.
    assert mutation_denial(Run(goal(), deepcopy(world)), Action.SCHEDULE_INSPECTION)


def test_bound_proposal_still_satisfies_next_inspection_identified():
    world = ready_world()
    assert "next_inspection_identified" in established(world, goal())


def test_unbound_proposal_cannot_complete_a_goal_end_to_end():
    class Unbound(ScriptedCapabilities):
        async def perform(self, action, goal, world, **kwargs):
            observation = await super().perform(action, goal, world, **kwargs)
            if action == Action.DETERMINE_NEXT_INSPECTION:
                observation.world.proposal = replace(observation.world.proposal, record_key="another/record")
            return observation

    result, capabilities = run(goal(success_conditions=("permit_verified", "next_inspection_identified")),
                               Unbound())
    assert result.status != Status.SUCCESS
    assert not any(action.value in MUTATION_VALUES for action in capabilities.calls)


def test_unbound_permit_cannot_claim_approval():
    world = ready_world()
    world.permit.status_normalized = "APPROVED"
    world.permit.record_key = "another/record"
    assert "permit_approved" not in established(world, goal(success_conditions=("permit_verified", "permit_approved")))


# --------------------------------------------------------------------------- #
# Constraint loss across replanning: the headline "attack the planner" case.   #
# --------------------------------------------------------------------------- #

def _stripping_capabilities():
    class Stripping(ScriptedCapabilities):
        async def perform(self, action, goal, world, **kwargs):
            observation = await super().perform(action, goal, world, **kwargs)
            if action == Action.DETERMINE_NEXT_INSPECTION:
                observation.world.proposal = replace(observation.world.proposal, constraints=[])
            return observation

    return Stripping()


def test_provider_dropping_a_user_constraint_is_refused_after_replanning():
    constrained = goal(constraints=("without spending money",))
    result, capabilities = run(constrained, _stripping_capabilities())
    assert result.status != Status.SUCCESS
    assert result.error == Error.PRECONDITION_NOT_MET
    assert not any(action.value in MUTATION_VALUES for action in capabilities.calls)


def test_provider_widening_the_user_date_window_is_refused_after_replanning():
    class Widening(ScriptedCapabilities):
        async def perform(self, action, goal, world, **kwargs):
            observation = await super().perform(action, goal, world, **kwargs)
            if action == Action.DETERMINE_NEXT_INSPECTION:
                observation.world.proposal = replace(observation.world.proposal, date_window_end="2030-01-01")
            return observation

    constrained = goal(date_window_end="2026-09-30")
    result, capabilities = run(constrained, Widening())
    assert result.status != Status.SUCCESS
    assert not any(action.value in MUTATION_VALUES for action in capabilities.calls)


@pytest.mark.parametrize("cost,signature", [(1.0, False), (None, False), (0.0, True), (0.0, None)])
def test_no_spend_and_no_signature_survive_every_replan(cost, signature):
    class Costly(ScriptedCapabilities):
        async def perform(self, action, goal, world, **kwargs):
            observation = await super().perform(action, goal, world, **kwargs)
            if action == Action.CHECK_INSPECTION_AVAILABILITY:
                observation.world.cost, observation.world.signature_required = cost, signature
            return observation

    g = goal(prohibited_actions=("payment", "signature", "application_submission"))
    result, capabilities = run(g, Costly())
    assert not any(action.value in MUTATION_VALUES for action in capabilities.calls)
    assert result.status != Status.SUCCESS


def test_immutable_constraints_survive_repeated_replanning():
    # Every capability call must observe the same frozen goal object: replanning
    # must not be able to hand a widened goal to the mutation path.
    constrained = goal(constraints=("without spending money",))
    result, capabilities = run(constrained)
    assert capabilities.seen_goals, "the planner produced no observations"
    assert all(observed is constrained for observed in capabilities.seen_goals)
    assert all(observed.constraints == ("without spending money",) for observed in capabilities.seen_goals)


def test_prohibited_operation_is_still_refused_after_a_replan():
    g = goal(operation="cancel", prohibited_actions=("cancel", "payment", "signature"), existing_inspection_id="i1")
    result, capabilities = run(g)
    assert Action.CANCEL_INSPECTION not in capabilities.calls
    assert result.status != Status.SUCCESS


# --------------------------------------------------------------------------- #
# Redundant action, loops and the step budget.                                 #
# --------------------------------------------------------------------------- #

def test_one_attempted_mutation_is_never_replayed():
    result, capabilities = run()
    assert capabilities.calls.count(Action.SCHEDULE_INSPECTION) == 1
    assert result.report()["metrics"]["mutations_attempted"] == 1


def test_verified_mutation_ledger_records_only_licet_issued_actions():
    result, _ = run()
    metrics = result.report()["metrics"]
    assert (metrics["mutations_attempted"], metrics["mutations_verified"]) == (1, 1)


def test_preexisting_appointment_is_not_recorded_as_a_licet_mutation():
    world = ready_world()
    world.verified_inspection = InspectionSnapshot("P-1", "i1", "Rough Electrical", "Scheduled", "2026-09-24", record_key=KEY)
    result, capabilities = run(world=world)
    metrics = result.report()["metrics"]
    assert result.status == Status.SUCCESS and not capabilities.calls
    assert (metrics["mutations_attempted"], metrics["mutations_verified"]) == (0, 0)


def test_unverified_mutation_is_not_recorded_as_verified():
    result, _ = run(cap=ScriptedCapabilities(verify=False))
    assert result.status != Status.SUCCESS
    assert result.report()["metrics"]["mutations_verified"] == 0


def test_churning_portal_stays_within_budget_and_does_not_duplicate_a_mutation():
    class Churn(ScriptedCapabilities):
        def __init__(self):
            super().__init__()
            self.sequence = 0

        async def perform(self, action, goal, world, **kwargs):
            self.sequence += 1
            observation = await super().perform(action, goal, world, **kwargs)
            # A new snapshot label every call is not progress; a planner that
            # treats it as progress would loop inside the budget indefinitely.
            observation.world.snapshot_id = f"churn-{self.sequence}"
            return observation

    result, capabilities = run(cap=Churn(), max_steps=20)
    assert result.semantic_steps <= 20
    assert capabilities.calls.count(Action.SCHEDULE_INSPECTION) <= 1


def test_repeated_information_gathering_is_cut_off():
    # A read whose result never changes the structured state is not progress.
    result, capabilities = run(cap=ScriptedCapabilities(failure=Action.READ_PERMIT_STATE))
    assert result.error == Error.PLAN_LOOP_DETECTED
    assert capabilities.calls.count(Action.READ_PERMIT_STATE) <= 3


# --------------------------------------------------------------------------- #
# Preconditions and external dependencies before any mutation.                 #
# --------------------------------------------------------------------------- #

def test_ineligible_but_available_record_cannot_be_scheduled():
    class Ineligible(ScriptedCapabilities):
        async def perform(self, action, goal, world, **kwargs):
            observation = await super().perform(action, goal, world, **kwargs)
            if action == Action.CHECK_INSPECTION_AVAILABILITY:
                observation.world.eligibility_verified = False
            return observation

    result, capabilities = run(cap=Ineligible())
    assert not any(action.value in MUTATION_VALUES for action in capabilities.calls)


def test_no_offered_dates_means_no_scheduling_attempt():
    class NoDates(ScriptedCapabilities):
        async def perform(self, action, goal, world, **kwargs):
            observation = await super().perform(action, goal, world, **kwargs)
            if action == Action.CHECK_INSPECTION_AVAILABILITY:
                observation.world.available_dates = ()
            return observation

    result, capabilities = run(cap=NoDates())
    assert Action.SCHEDULE_INSPECTION not in capabilities.calls
    assert result.status != Status.SUCCESS


def test_external_dependency_is_reported_not_papered_over():
    dependency = ExternalDependency("inspector", "an inspector must sign off")
    result, capabilities = run(cap=ScriptedCapabilities(blocker=dependency))
    assert result.error == Error.EXTERNAL_DEPENDENCY
    assert result.status == Status.PARTIAL_SUCCESS
    assert not any(action.value in MUTATION_VALUES for action in capabilities.calls)


def test_contradictory_state_stops_before_planning_a_mutation():
    class Contradiction(ScriptedCapabilities):
        async def perform(self, action, goal, world, **kwargs):
            observation = await super().perform(action, goal, world, **kwargs)
            if action == Action.DETERMINE_BLOCKERS:
                observation.world.reasoning.answerability = "conflicting"
                observation.world.reasoning.contradictions = ["inspection history disagrees"]
            return observation

    result, capabilities = run(cap=Contradiction())
    assert not any(action.value in MUTATION_VALUES for action in capabilities.calls)


def test_partial_completion_is_never_published_as_success():
    result, _ = run(cap=ScriptedCapabilities(blocker=ExternalDependency("municipality", "review pending")))
    assert result.status == Status.PARTIAL_SUCCESS
    assert result.status != Status.SUCCESS
    assert result.report()["status"] == "PARTIAL_SUCCESS"


def test_step_budget_is_global_across_replanning():
    result, capabilities = run(max_steps=3)
    assert result.error == Error.STEP_BUDGET_EXCEEDED
    assert len(capabilities.calls) == 3
    assert result.semantic_steps == 3


# --------------------------------------------------------------------------- #
# Policy agreement: the planner and the Phase 4 policy must not disagree.      #
# --------------------------------------------------------------------------- #

def test_planner_denial_and_policy_agree_for_a_cancellation():
    world = ready_world()
    world.proposal = replace(world.proposal, action_type="cancel", existing_inspection_id="i1")
    decision = decide_action_policy(world.proposal, confirmed=False)
    assert decision.requires_confirmation and not decision.allowed
    assert Action.CANCEL_INSPECTION in GoalPlanner(ScriptedCapabilities()).confirmations


def test_next_actions_never_proposes_a_mutation_without_verified_prerequisites():
    world = ready_world()
    world.eligibility_verified = False
    actions = next_actions(Run(goal(), world))
    assert not any(action in MUTATIONS for action in actions)
    assert mutation_denial(Run(goal(), world), Action.SCHEDULE_INSPECTION)


def test_next_actions_requires_fresh_preflight_after_a_record_change():
    world = ready_world()
    world.record_key = "replaced/record"
    actions = next_actions(Run(goal(), world))
    assert not any(action in MUTATIONS for action in actions)
    assert operation_key(world) != world.preflight_fingerprint
