"""Read-only architecture counterexamples. No browser, network, or model calls.
Exit 1 means at least one Phase 6 safety invariant remains violated.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from licet.agent.state import AgentState
from licet.agent.model import ToolCall
from licet.browser.dispatcher import resolve_action
from licet.safety.guard import authorize
from licet.safety.policy import (
    ConfirmationRequest, Environment, MutationLedger, PolicyEngine,
    ProposedAction, RecordIdentity, UserConstraints,
)
from licet.phase4.actions import InspectionAction, InspectionSnapshot
from licet.phase4.workflow import InspectionActionExecutor


def review():
    rows = []
    def check(name, safe, observed):
        rows.append(dict(check=name, safe=bool(safe), observed=observed))
    state = AgentState(goal='review', current_url='https://permits.example.gov/record')
    decision = authorize('schedule_inspection', state)
    check('unknown browser environment refuses scheduling', not decision.allowed, decision.decision.value)
    state.current_url = 'https://aca-prod.accela.com/agency/record'
    for action in ('cancel_inspection', 'submit_payment', 'accept_legal_attestation'):
        state.request_approval(action, 'test human approval')
        state.grant_approval()
        decision = authorize(action, state)
        check(f'live browser refuses approved {action}', not decision.allowed, decision.decision.value)
    state.clear_approval()
    resolution = resolve_action(ToolCall('click', {'target': 'Submit Payment', 'intent': 'read_record'}), state)
    decision = authorize(resolution.action, state)
    check('model read intent cannot downgrade payment button', not decision.allowed,
          f'{resolution.action}: {decision.decision.value}')
    action = ProposedAction('CANCEL_INSPECTION', permit_id='P-1', target='Rough Electrical',
                            inspection_type='Rough Electrical', inspection_id='I-1',
                            existing_date='2026-09-25', record_key='agency:A')
    observed = RecordIdentity('P-1', 'agency:A', 'Rough Electrical', 'I-1', '2026-09-25')
    # Re-targeted after the response: the approval is now taken from the engine's
    # own issuance path and the copy is taken *before* the first use, so the
    # check cannot be satisfied by the object's `used` flag. The invariant under
    # test -- a copy cannot authorize a second execution -- is unchanged.
    engine = PolicyEngine(environment=Environment.SANDBOX)
    approval = engine.decide(action, observed_identity=observed).confirmation
    other = replace(action, existing_date='2026-09-27', record_key='agency:B')
    check('approval binds date and stable record', approval is not None and not approval.matches(other),
          'matches=' + str(approval.matches(other) if approval else None))
    copied = deepcopy(approval)
    first = engine.decide(action, observed_identity=observed, confirmation=approval)
    second = engine.decide(action, observed_identity=observed, confirmation=copied)
    check('copied approval cannot authorize twice', first.allowed and not second.allowed,
          f'first={first.verdict.value}, second={second.verdict.value}')
    for text, kind in (("Do everything possible, but don't submit anything", 'SUBMIT_APPLICATION'),
                       ("Do everything possible, but don't cancel", 'CANCEL_INSPECTION'),
                       ("Don't schedule inspections", 'SCHEDULE_INSPECTION')):
        allowed = UserConstraints.from_text(text).allows(kind)
        check('constraint: ' + text, not allowed, f'{kind} allowed={allowed}')
    cancel = InspectionAction('cancel', 'P-1', 'Rough Electrical',
                              existing_inspection_id='I-1', record_key='agency:A')
    before = InspectionSnapshot('P-1', 'I-1', 'Rough Electrical', 'Cancelled', record_key='agency:A')
    foreign = replace(before, inspection_id='I-2', record_key='agency:B')
    # Re-targeted after the response: verification now also compares the
    # appointment the action named, so the probe passes the action in.
    matched = InspectionActionExecutor._matches('cancel', before, foreign, cancel)
    check('independent verification rejects foreign inspection and record', not matched, f'matched={matched}')
    ledger = MutationLedger()
    first = ledger.begin(action)
    second = ledger.begin(action)
    check('duplicate reservation cannot claim second mutation slot', first.allowed and not second.allowed,
          f'first={first.allowed}, second={second.allowed}')
    # Positive controls ensure this probe does not simply expect every action denied.
    engine = PolicyEngine(environment=Environment.LIVE_READ_ONLY)
    check('positive control: live reads remain allowed', engine.decide('READ_FEES').allowed, 'READ_FEES')
    check('positive control: central live mutation blocked', not engine.decide(action).allowed, 'CANCEL_INSPECTION')
    return rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    rows = review()
    payload = {'scope': 'local deterministic architecture probes; no portal mutations',
               'passed': sum(r['safe'] for r in rows), 'failed': sum(not r['safe'] for r in rows), 'checks': rows}
    text = json.dumps(payload, indent=2)
    if args.json:
        args.json.write_text(text + '\n')
    print(text)
    sys.exit(1 if payload['failed'] else 0)
