"""Offline adversarial review probes. Failures are findings, not passing tests.

Run with the development environment:
  .venv/bin/python scripts/phase2_review_probe.py
No browser, model, credentials, or network is used. The runner probes reuse the
project's fake client; evidence records source hashes for concurrent development.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from licet.lookup import (
    PermitLookupRequest, SearchResult, LookupStatus,
    parse_lookup_request, choose_search_strategy, resolve_lookup,
)
from licet.agent.state import AgentState
from tests.conftest import (
    FakeClient, runner_for, search_form, search_results, detail_page,
    gs_field, apo_fields, mode_dropdown, row,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ['licet/lookup.py', 'licet/lookup_runner.py', 'licet/schema/extract.py',
           'licet/agent/state.py', 'tests/conftest.py']


def source_hashes():
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in SOURCES}


def main(output: Path) -> int:
    before = source_hashes()
    checks = []

    def record(case_id, expected, actual, passed, inputs=None):
        checks.append(dict(id=case_id, expected=expected, actual=actual,
                           passed=bool(passed), inputs=inputs))

    # Fields not specified in 'wanted' may be present; None forbids cross-field leakage.
    parse_cases = [
        ('P01_live_record_format', 'Find permit BLD26-00472', {'record_number': 'BLD26-00472', 'zip_code': None}, 'record_number'),
        ('P02_labeled_short_numeric', 'permit 12345', {'record_number': '12345', 'zip_code': None}, 'record_number'),
        ('P03_record_not_zip', 'Find permit BLD-2026-00123', {'record_number': 'BLD-2026-00123', 'zip_code': None}, 'record_number'),
        ('P04_numeric_parcel_not_record', 'Find record for parcel 123456789', {'parcel_number': '123456789', 'record_number': None}, 'parcel_number'),
        ('P05_explicit_zip_wins', 'Find permit BLD-2026-00123 in ZIP 57104', {'record_number': 'BLD-2026-00123', 'zip_code': '57104'}, 'record_number'),
        ('P06_leading_zero_record', 'Find permit 000000014', {'record_number': '000000014'}, 'record_number'),
        ('P07_exact_address_control', 'Find the permit at 123 Main Street', {'street_number': '123', 'street_name': 'main st'}, 'full_address'),
        ('P08_type_address_control', 'commercial alteration for 800 state', {'street_number': '800', 'street_name': 'state', 'permit_type': 'Commercial Alteration'}, 'full_address'),
        ('P09_direction_control', 'building record at 1200 e main', {'street_number': '1200', 'street_name': 'e main'}, 'full_address'),
        ('P10_parcel_delimiter_spacing', 'Find parcel 42 - 18 - 33', {'parcel_number': '421833', 'record_number': None}, 'parcel_number'),
        ('P11_postal_city_not_street', 'Find the permit at 123 Main St, Springfield, IL 62704', {'street_number': '123', 'street_name': 'main st', 'zip_code': '62704'}, 'full_address'),
        ('P12_fractional_house_number', 'Find permit at 1200 1/2 E Main St', {'street_number': '1200 1/2', 'street_name': 'e main st'}, 'full_address'),
        ('P13_partial_street_control', 'Find permits on Main Street', {'street_name': 'main st', 'street_number': None}, 'partial_address'),
    ]
    for case_id, text, wanted, method in parse_cases:
        try:
            request = parse_lookup_request(text)
            actual = request.model_dump(exclude_none=True)
            try:
                actual['strategy'] = choose_search_strategy(request).value
            except ValueError as exc:
                actual['strategy_error'] = str(exc)
            passed = all(actual.get(k) == v for k, v in wanted.items()) and actual.get('strategy') == method
        except Exception as exc:
            actual, passed = {'exception': f'{type(exc).__name__}: {exc}'}, False
        record(case_id, {**wanted, 'strategy': method}, actual, passed, text)

    # Unsupported compositional constraints must stop for clarification, not vanish.
    reject_cases = [
        ('P14_negated_record', 'Do not use BLD-2026-00123; find BLD-2026-00124'),
        ('P15_alternative_records', 'Find BLD-2026-00123 or BLD-2026-00124'),
        ('P16_multiple_addresses', 'Find the permit at 123 Main St or at 456 Oak St'),
        ('P17_unsupported_reasoning_control', 'What inspections should happen next?'),
        ('P18_unresolved_reference_control', 'Find the permit at this address'),
    ]
    for case_id, text in reject_cases:
        try:
            actual = parse_lookup_request(text).model_dump(exclude_none=True)
            # A parser can legitimately resolve the explicit positive ID in P14.
            passed = case_id == 'P14_negated_record' and actual.get('record_number') == 'BLD-2026-00124'
        except ValueError as exc:
            actual, passed = {'rejected': str(exc)}, True
        record(case_id, 'clarify/reject; P14 may resolve the positive ID', actual, passed, text)

    for case_id, text, field, wanted in [
        ('P19_unit_retained', 'Find the permit at 123 Main St Apt 4', 'unit', '4'),
        ('P20_status_retained', 'Find the issued permit at 123 Main St', 'status', 'issued'),
    ]:
        try:
            actual = parse_lookup_request(text).model_dump(exclude_none=True)
            passed = str(actual.get(field, '')).lower() == wanted
        except ValueError as exc:
            actual, passed = {'rejected': str(exc)}, True
        record(case_id, f'preserve {field}={wanted}, or reject unsupported constraint', actual, passed, text)

    address = dict(street_number='123', street_name='main st')
    rank_cases = [
        ('R01_exact_id_control', {'record_number': 'BLD-1'}, [dict(record_number='BLD-1')], 'BLD-1', {}),
        ('R02_conflicting_record_id', {**address, 'record_number': 'BLD-1'}, [dict(record_number='BLD-2', address='123 Main St')], None, {}),
        ('R03_conflicting_type', {**address, 'permit_type': 'Commercial Alteration'}, [dict(record_number='ELE-2', address='123 Main St', record_type='Electrical')], None, {}),
        ('R04_wrong_street_compensated', {**address, 'zip_code': '57104', 'permit_type': 'Commercial Alteration'}, [dict(record_number='BLD-2', address='123 Other St 57104', record_type='Commercial Alteration')], None, {}),
        ('R05_unit_is_not_house_number', address, [dict(record_number='BLD-2', address='999 Main St Apt 123')], None, {}),
        ('R06_wrong_unit', dict(street_number='123', street_name='main st apt 4'), [dict(record_number='BLD-2', address='123 Main St Apt 5')], None, {}),
        ('R07_wrong_zip', {**address, 'zip_code': '57104'}, [dict(record_number='BLD-2', address='123 Main St 90210')], None, {}),
        ('R08_wrong_parcel', {**address, 'parcel_number': '42-18-33'}, [dict(record_number='BLD-2', address='123 Main St', parcel_number='99-99-99')], None, {}),
        ('R09_wrong_applicant', {**address, 'applicant_name': 'Jane Doe'}, [dict(record_number='BLD-2', address='123 Main St', applicant='John Roe')], None, {}),
        ('R10_same_address_tie_control', address, [dict(record_number=n, address='123 Main St') for n in ('BLD-1','BLD-2','ELE-3')], None, {}),
        ('R11_type_disambiguation_control', {**address, 'permit_type': 'Commercial Alteration'}, [dict(record_number='BLD-1', address='123 Main St', record_type='Commercial Alteration'),dict(record_number='ELE-2', address='123 Main St', record_type='Electrical')], 'BLD-1', {}),
        ('R12_parcel_is_not_record_identity', dict(parcel_number='42-18-33', permit_type='Commercial Alteration'), [dict(record_number='BLD-1', parcel_number='42-18-33', record_type='Commercial Alteration'),dict(record_number='ELE-2', parcel_number='42-18-33', record_type='Electrical')], 'BLD-1', {}),
        ('R13_applicant_threshold_bypass', dict(applicant_name='Jane Doe'), [dict(record_number='BLD-1', applicant='Jane Doe')], None, {'min_confidence': .95}),
        ('R14_missing_applicant_competitor', dict(applicant_name='Jane Doe'), [dict(record_number='BLD-1', applicant='Jane Doe'),dict(record_number='BLD-2')], None, {}),
        ('R15_generic_type_not_exact', {**address, 'permit_type': 'Commercial Alteration'}, [dict(record_number='BLD-1', address='123 Main St', record_type='Commercial')], None, {}),
    ]
    for case_id, request, candidates, selected, kwargs in rank_cases:
        result = resolve_lookup(PermitLookupRequest(**request), [SearchResult(**r) for r in candidates], **kwargs)
        actual = result.model_dump(mode='json')
        got = result.selected.record_number if result.selected else None
        record(case_id, {'selected': selected}, actual, got == selected,
               dict(request=request, candidates=candidates, options=kwargs))

    def run_probe(request, reads, **options):
        client = FakeClient(reads)
        runner = runner_for(client, **options)
        state = AgentState(goal='offline adversarial review')
        result = asyncio.run(runner.run(state.goal, PermitLookupRequest(**request), state))
        return client, runner, state, result

    def runner_view(runner, result):
        return dict(result=result.model_dump(mode='json'), identity_verified=runner.identity_verified,
                    open_error=runner.open_error, metrics=runner.metrics.as_dict())

    form = search_form(fields=[gs_field('txtGSPermitNumber')])
    grid = search_results(row('BLD-1', 'Commercial Alteration', '123 Main St'), footer='Showing 1-1 of 1')
    client, runner, state, result = run_probe({'record_number': 'BLD-1'}, [form, grid, detail_page(number='BLD-2')])
    record('A01_found_despite_opened_mismatch', 'non-FOUND with RECORD_MISMATCH', runner_view(runner,result), result.status is not LookupStatus.FOUND)

    # Unique-match redirects are observed live: search need not show a grid.
    client, runner, state, result = run_probe({'record_number': 'BLD-1'}, [form, detail_page(number='BLD-1')])
    record('A02_direct_detail_redirect', 'FOUND and identity verified', runner_view(runner,result), result.status is LookupStatus.FOUND and runner.identity_verified)

    address_form = search_form(fields=mode_dropdown(['General Search', 'Search by Address']))
    partial_grid = search_results(row('BLD-1', 'Commercial Alteration', '123 Main St'), footer='Showing 1-1 of 2 Next')
    client, runner, state, result = run_probe(address, [address_form, search_form(fields=apo_fields()), partial_grid, detail_page(number='BLD-1')], max_pages=1)
    record('A03_incomplete_pagination', 'no selected candidate while another result page is unscanned', runner_view(runner,result), result.selected is None)

    zeros = search_form(fields=[gs_field('txtGSPermitNumber')], text='No records found.')
    client, runner, state, result = run_probe({'record_number': 'BLD-1'}, [form, zeros, zeros, zeros], max_attempts=1)
    submits = sum(name == 'click' and args.get('target') == 'text=Search' for name,args in client.calls)
    record('A04_global_attempt_budget', 'at most 1 search submission', {'submissions': submits, 'trace_attempts': len(runner.trace.attempts)}, submits <= 1)

    wrong_address_detail = detail_page(number='BLD-1')
    wrong_address_detail['text'] += '\nWork Location:\n999 Other St'
    client, runner, state, result = run_probe(address, [address_form, search_form(fields=apo_fields()), grid, wrong_address_detail])
    record('A05_independent_address_evidence', 'do not verify requested address using selected-row data as observed detail', runner_view(runner,result), not runner.identity_verified)

    client, runner, state, result = run_probe({'record_number': 'BLD-1'}, [form, grid, detail_page(number='BLD-1')])
    assert runner.identity_verified, 'control setup must find the first record'
    second = asyncio.run(runner.run('unsupported query', PermitLookupRequest(zip_code='57104'), state))
    record('A06_stale_active_permit', 'failed new lookup must invalidate or explicitly scope previous permit',
           {'new_status': second.status.value, 'active_permit': state.active_permit.permit.permit_id if state.active_permit else None}, state.active_permit is None)

    from licet.browser.errors import ToolError, BrowserError
    from licet.browser.solari_client import ToolResult

    failing_client = FakeClient([address_form, address_form])
    async def uncertain_select(target, value):
        failing_client.calls.append(('select', {'target': target.describe(), 'value': value}))
        return ToolResult(ok=False, error=ToolError(BrowserError.ACTION_OUTCOME_UNKNOWN, 'outcome uncertain'))
    failing_client.select = uncertain_select
    failing_runner = runner_for(failing_client)
    failing_state = AgentState(goal='uncertain select')
    asyncio.run(failing_runner.run(failing_state.goal, PermitLookupRequest(**address), failing_state))
    selection_calls = [args for name,args in failing_client.calls if name == 'select']
    record('A07_uncertain_mutation_replayed_by_runner', 'one select, then stop for reconciliation',
           {'select_calls': selection_calls, 'stop_reason': failing_state.no_valid_action_reason}, len(selection_calls) == 1)

    wrong_grid = search_results(row('BLD-2', 'Commercial Alteration', '123 Main St'), footer='Showing 1-1 of 1')
    client, runner, state, result = run_probe({**address, 'record_number': 'BLD-1'},
                                             [form, wrong_grid, detail_page(number='BLD-2')])
    record('A08_wrong_request_identity_verified', 'never verify BLD-2 for explicit BLD-1 request',
           runner_view(runner,result), not runner.identity_verified and result.status is not LookupStatus.FOUND)

    after = source_hashes()
    report = dict(generated_utc=datetime.now(timezone.utc).isoformat(), scope='offline Phase 2 review',
                  source_sha256=before, source_changed_during_probe=before != after,
                  total=len(checks), passed=sum(c['passed'] for c in checks),
                  failed=sum(not c['passed'] for c in checks), checks=checks)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2))
    print(json.dumps({k:report[k] for k in ('total','passed','failed','source_changed_during_probe')}))
    print(f'Evidence: {output}')
    print('Failed: ' + ', '.join(c['id'] for c in checks if not c['passed']))
    return 1 if report['failed'] else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/phase2_review_evidence.json')
    args = parser.parse_args()
    raise SystemExit(main(args.output))
