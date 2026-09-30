"""noisy phase 2–7 integration: real runtime and policy, fake external i/o"""
import asyncio
from copy import deepcopy
from dataclasses import replace
import pytest
import random
from tests.test_phase5_integration import Portal
from tests.conftest import _ok, FakeClient, runner_for, detail_page, search_form, gs_field
from licet.phase3.runner import Phase3RetrievalRunner
from licet.phase3.state import Evidence
from licet.phase4.selection import InspectionOption, SelectionContext
from licet.phase5 import LicetCapabilities, GoalPlanner, Preflight, parse_goal, Action, Status, Observation
from licet.phase5.state import operation_key
from licet.phase7.portal import PortalState, route_recovery, settled_browser_state
from licet.phase7.recovery import RecoveryController, RecoveryBudgets

KEY = 'NULLISLAND/Building/REC26/00000/00014'
CASES = ('read_timeout', 'home', 'loading', 'modal', 'wrong_page', 'tab',
         'session', 'outage', 'lost_submit', 'unknown_submit', 'wrong_record', 'preflight_timeout',
         # r1: the stale-but-plausible read this portal actually produces
         'pending_rows')

# what each injection actually exercises
CASE_SCOPE = {
    'read_timeout': 'full: transport timeout, then recovery',
    'home': 'full: dead-session deep-link redirect',
    'loading': 'full: AJAX section still rendering',
    'modal': 'routing only: no dialog wording is injected, so detect_modal is not exercised (R3)',
    'wrong_page': 'full: display-label reformatting / wrong page',
    'tab': 'partial: partly other-agency markers on no benchmarked flow (R3)',
    'session': 'full: site-wide SessionTimeout.js expiry',
    'outage': 'full: repeated portal unavailability',
    'lost_submit': 'full: commit succeeded, response lost',
    'unknown_submit': 'full: outcome unknowable, reconcile only',
    'wrong_record': 'full: record identity drift',
    'preflight_timeout': 'full: auto-postback preflight timeout',
    'pending_rows': 'full (R1): grid rendered without rows while the section settles',
    'journal_retry': 'helper: restart against an existing mutation journal',
    'normal': 'helper: control run with no injection',
}


def test_noisy_injection_scope_metadata_is_complete():
    assert set(CASE_SCOPE) == set(CASES) | {'journal_retry', 'normal'}
    assert CASE_SCOPE['modal'].startswith('routing only')
    assert CASE_SCOPE['tab'].startswith('partial')


async def noisy_run(case, seed=0, journal=None, validate_outcome=True):
    rng = random.Random(seed)
    detail = detail_page(number='000000014', status='Issued')
    detail['text'] += '\nYou have not added any inspections.'
    class Client(FakeClient):
        async def wait_for_text(self, **kwargs):
            self.calls.append(('wait', kwargs))
            return _ok(url=detail['url'])
    client = Client([search_form(fields=[gs_field('txtGSPermitNumber')])] + [detail]*50)
    lookup = runner_for(client)
    retrieval = Phase3RetrievalRunner(lookup.dispatcher)
    class NoisyPortal(Portal):
        def submit_inspection_action(self, *args, **kwargs):
            if case == 'unknown_submit':
                self.submits.append(args[0])
                raise TimeoutError('response lost; server outcome unknown')
            result = super().submit_inspection_action(*args, **kwargs)
            if case == 'lost_submit':
                raise TimeoutError('response lost after commit')
            return result
    portal = NoisyPortal(KEY)
    def context(w):
        return SelectionContext(w.permit_id, w.record_key, w.snapshot_id, True,
            (InspectionOption('Rough Electrical', True, True, ('eligibility',)),),
            {'eligibility': Evidence('eligibility', 'inspections', 'Rough Electrical is eligible; no unmet prerequisites', record_key=KEY)},
            history_complete=True)
    def preflight(w):
        return Preflight(KEY, w.snapshot_id, operation_key(w), True, ('2026-09-24',), 0, False)
    class NoisyCapabilities(LicetCapabilities):
        injected = False
        async def perform(self, action, goal, world, **kwargs):
            await asyncio.sleep(rng.uniform(0, .001))
            target = Action.CHECK_INSPECTION_AVAILABILITY if case == 'preflight_timeout' else Action.READ_PERMIT_STATE
            if action == target and (not self.injected or case == 'outage') and case not in {'lost_submit','unknown_submit','journal_retry','normal'}:
                self.injected = True
                if case in {'read_timeout','preflight_timeout','outage'}:
                    raise TimeoutError('portal temporarily unavailable')
                if case == 'wrong_record':
                    world.record_key = 'FOREIGN'
                    return Observation(world)
                if case == 'pending_rows':
                    # r1: the observation the portal really produces mid-load
                    world.browser_state = settled_browser_state({
                        'url': detail['url'],
                        'text': 'Inspections\nYou have not added any inspections.\nLoading...',
                        'grids': [{'row_count': 0, 'declared_empty': False}],
                    })
                    return Observation(world, False,
                        'inspections grid rendered without rows while the section settles',
                        retryable=True)
                finding = {'home':'portal_home_redirect', 'loading':'ajax_section_loading',
                    'modal':'unexpected_modal', 'wrong_page':'wrong_page', 'tab':'new_tab_opened',
                    'session':'session_expired'}[case]
                world.browser_state = {'findings':[finding], 'url':detail['url']}
                return Observation(world, False, 'injected portal state', retryable=True)
            return await super().perform(action, goal, world, **kwargs)
    cap = NoisyCapabilities(lookup=lookup, retrieval=retrieval, selection_context=context, preflight=preflight, portal=portal, mutation_journal=journal)
    controller = RecoveryController()
    planner = GoalPlanner(cap, recovery=controller)
    result = await planner.run(parse_goal('Schedule Rough Electrical inspection for permit 000000014 without spending money.'))
    expected_success = case not in {'session','outage','unknown_submit','wrong_record','journal_retry'}
    if validate_outcome:
        assert (result.status == Status.SUCCESS) == expected_success, result.report()
        assert len(portal.submits) <= 1
        assert not expected_success or (result.world.verified_inspection.record_key == KEY)
        assert controller.stats.recovery_actions <= controller.budgets.max_recovery_actions
        if case in {'session','wrong_record','outage'}:
            assert not portal.submits
        if case in {'lost_submit','unknown_submit'}:
            assert len(portal.submits) == 1
        if case == 'pending_rows':
            # the stale-empty grid is never taken as a fact: the run re-settled and scheduled the real
            # inspection rather than concluding that the record has no inspections to schedule
            assert result.world.verified_inspection is not None
            assert result.world.verified_inspection.record_key == KEY
    return {'case':case, 'seed':seed, 'status':result.status.value, 'reason':result.reason,
            'submits':len(portal.submits), 'expected_success':expected_success,
            'trace':result.trace, 'recovery':controller.report(),
            'portal_final':__import__('dataclasses').asdict(portal.current) if portal.current else None}


@pytest.mark.parametrize('case', CASES)
@pytest.mark.parametrize('seed', [0,1])
def test_noisy_real_runtime(case, seed):
    asyncio.run(noisy_run(case,seed))


def test_pending_rows_grid_is_not_evidence_and_recovery_re_settles():
    """r1: a mid-load read is not evidence, whatever it appears to say"""
    observation = {
        'url': 'https://aca-test.accela.com/NULLISLAND/Cap/CapDetail.aspx?Module=Building',
        'text': 'Inspections\nYou have not added any inspections.\nLoading...',
        'grids': [{'row_count': 0, 'declared_empty': False}],
    }
    state = PortalState.from_observation(observation)
    assert state.unsettled is True
    assert 'empty_table_pending_rows' in {finding.value for finding in state.findings}
    route = route_recovery(state)
    assert route is not None and not route.terminal
    # non-terminal and re-settling: the only route for an observation that is not yet evidence, as opposed
    # to a stop or an invented reading of it
    assert route.finding.value == 'empty_table_pending_rows'
    assert route.strategy == 'WAIT_FOR_SETTLE'
    assert 're-settle' in route.reason


def test_validator_is_mandatory_and_false_callback_is_not_recovery():
    c = RecoveryController()
    failure = c.classify('element timeout', operation='read')
    ran=[]
    result=asyncio.run(c.recover(failure,'read',lambda:ran.append(1)))
    assert not result.recovered and not ran
    result=asyncio.run(c.recover(failure,'read',lambda:False,validate=lambda v:v is True))
    assert not result.recovered


def test_unknown_mutation_never_releases_reservation_and_absent_retry_is_bounded():
    c=RecoveryController()
    assert c.mutation_started('operation')
    assert not c.mutation_reconciled('operation',occurred=None)
    assert not c.mutation_started('operation')
    assert c.mutation_reconciled('operation',occurred=False)
    assert c.mutation_started('operation')
    assert not c.mutation_reconciled('operation',occurred=False)
    assert not c.mutation_started('operation')


def test_recovery_timeout_is_bounded():
    c=RecoveryController(budgets=RecoveryBudgets(action_timeout_seconds=.01))
    async def hangs():
        await asyncio.sleep(1)
    result=asyncio.run(c.recover(c.classify('element timeout',operation='read'),'read',hangs,validate=bool))
    assert not result.recovered and result.attempts == 2


def test_invalid_planner_choice_replans_then_executes_only_ready_reads():
    from licet.eval.phase5_fixtures import ScriptedCapabilities, ready_world, goal
    w=ready_world()
    w.reasoning.needed_sections=[{'section':'fees'}, {'section':'conditions'}]
    class Selector:
        calls=0
        async def choose(self, run, options):
            self.calls+=1
            return (Action.SCHEDULE_INSPECTION, 'invalid') if self.calls==1 else (options[0], 'valid read')
    cap=ScriptedCapabilities()
    planner=GoalPlanner(cap, selector=Selector())
    result=asyncio.run(planner.run(goal(),world=w))
    assert result.status == Status.SUCCESS
    assert cap.calls[0] == Action.READ_FEES
    assert planner.recovery.stats.replans == 1
    assert result.report()['recovery']['stats']['replans'] == 1


def test_checkpoint_has_no_cross_run_or_mutable_alias_leak():
    from licet.phase7.recovery import PageFingerprint
    c=RecoveryController()
    data={'facts':['verified']}
    p=c.checkpoint('record',data,PageFingerprint(record_number='P-1'))
    data['facts'].append('forged')
    assert p.state['facts'] == ['verified']
    c.begin_run()
    assert not c.checkpoints


def test_terminal_finding_overrides_wait_even_when_input_order_is_reversed():
    from licet.phase7.portal import PortalState, route_recovery
    state=PortalState.from_observation({'findings':['ajax_section_loading','consequential_modal']})
    assert route_recovery(state).terminal

@pytest.mark.parametrize('body,unsettled', [('<tr><th>Inspection</th></tr>',True),
    ('<tr><td>No inspections found</td></tr>',False), ('<tr><td>Rough Electrical</td></tr>',False)])
def test_dom_grid_distinguishes_unpopulated_and_declared_empty(body,unsettled):
    from licet.phase7.grids import grid_observations
    from licet.phase7.portal import PortalState
    grids=grid_observations('<table id="inspectionGrid">'+body+'</table>')
    assert PortalState.from_observation({'grids':grids}).unsettled == unsettled


def test_crash_journal_blocks_new_process_and_changed_proposal(tmp_path):
    from licet.phase7.journal import MutationJournal
    path=tmp_path/'journal.sqlite'
    assert MutationJournal(path).reserve('record','operation-A')
    restarted=MutationJournal(path)
    assert not restarted.reserve('record','operation-A')
    assert not restarted.reserve('record','operation-B')
    restarted.verified('another-record','operation-A')
    assert not restarted.reserve('record','operation-B')
    restarted.verified('record','operation-A')
    assert not restarted.reserve('record','operation-A')
    assert restarted.reserve('record','operation-B')


def test_real_runtime_restart_does_not_resubmit_unknown_result(tmp_path):
    from licet.phase7.journal import MutationJournal
    path=tmp_path/'journal.sqlite'
    first=asyncio.run(noisy_run('unknown_submit', journal=MutationJournal(path)))
    second=asyncio.run(noisy_run('journal_retry', journal=MutationJournal(path)))
    assert first['submits'] == 1 and second['submits'] == 0
    assert second['status'] != 'SUCCESS'
