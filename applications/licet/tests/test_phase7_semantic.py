from dataclasses import replace
import pytest
from licet.phase7.semantic import SemanticContext, RecoveryChoice, decide_semantic_recovery
from licet.phase7.recovery import StateConflict

BASE = SemanticContext(identity_verified=True, replan_remaining=2, read_retry_remaining=2,
                       verified_findings=('Rough Electrical failed',))

@pytest.mark.parametrize('status', ['Expired', 'Revoked', 'Void', 'Withdrawn'])
def test_lifecycle_change_stops_without_inventing_renewal(status):
    result = decide_semantic_recovery(replace(BASE, permit_status=status))
    assert result.choice == RecoveryChoice.STOP
    assert result.outcome == 'PARTIAL_SUCCESS'

@pytest.mark.parametrize('field', ['policy_denied', 'auth_required', 'required_input_missing'])
def test_terminal_conditions_cannot_be_replanned_away(field):
    assert decide_semantic_recovery(replace(BASE, **{field: True})).choice == RecoveryChoice.STOP


def test_wrong_identity_does_not_preserve_success_claim():
    result = decide_semantic_recovery(replace(BASE, identity_verified=False, goal_satisfied=True))
    assert result.outcome == 'BLOCKED'


def test_conflicts_refresh_both_sources_then_stop():
    context = replace(BASE, conflicts=(StateConflict('status', ('Issued', 'Expired'), ('overview', 'history')),))
    assert decide_semantic_recovery(context).read_sections == ('history', 'overview')
    assert decide_semantic_recovery(replace(context, refresh_attempted=True)).choice == RecoveryChoice.STOP


def test_unknown_optional_fees_do_not_block_failed_inspection_answer():
    context = replace(BASE, missing_sections=('fees',), required_sections=('inspections',), goal_satisfied=True)
    assert decide_semantic_recovery(context).outcome == 'SUCCESS'


def test_required_fees_are_unknown_not_zero():
    context = replace(BASE, missing_sections=('fees',), required_sections=('fees',))
    assert decide_semantic_recovery(context).read_sections == ('fees',)
    assert decide_semantic_recovery(replace(context, refresh_attempted=True)).outcome == 'PARTIAL_SUCCESS'

@pytest.mark.parametrize('flag', ['mutation_uncertain', 'mutation_attempted'])
def test_mutation_uncertainty_only_rereads_never_retries_submit(flag):
    context = replace(BASE, **{flag: True}, goal_satisfied=True)
    result = decide_semantic_recovery(context)
    assert result.read_sections == ('inspections',)
    assert result.outcome != 'SUCCESS'
    assert decide_semantic_recovery(replace(context, refresh_attempted=True)).choice == RecoveryChoice.STOP

@pytest.mark.parametrize('flag', ['invalid_plan', 'repeated_state'])
def test_invalid_or_oscillating_plan_spends_bounded_replan(flag):
    context = replace(BASE, **{flag: True})
    assert decide_semantic_recovery(context).choice == RecoveryChoice.REPLAN
    assert decide_semantic_recovery(replace(context, replan_remaining=0)).choice == RecoveryChoice.STOP


def test_stale_state_cannot_complete_goal():
    context = replace(BASE, fresh=False, goal_satisfied=True)
    assert decide_semantic_recovery(context).outcome == 'IN_PROGRESS'
    assert decide_semantic_recovery(replace(context, read_retry_remaining=0)).choice == RecoveryChoice.STOP


def test_no_verified_work_is_not_partial_success():
    assert decide_semantic_recovery(replace(BASE, verified_findings=(), auth_required=True)).outcome == 'BLOCKED'


def test_verified_mutation_can_finish_without_replay():
    context = replace(BASE, mutation_attempted=True, mutation_verified=True, goal_satisfied=True)
    assert decide_semantic_recovery(context).outcome == 'SUCCESS'
