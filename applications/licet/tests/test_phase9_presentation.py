"""Prevent demo presentation from claiming more than the execution evidence."""
from types import SimpleNamespace

import pytest
from scripts.ni_agent_run import _friendly_result, _mutation_evidence, sandbox_problem


@pytest.mark.parametrize('url', [
    'https://aca-test.accela.com.evil.example/',
    'https://evil-aca-test.accela.com/',
    'https://aca-test.accela.com@evil.example/',
    'http://aca-test.accela.com/',
    'https://aca-test.accela.com:8443/',
    'https://aca-test.accela.com:invalid/',
])
def test_demo_host_guard_rejects_lookalikes_and_insecure_targets(url):
    assert sandbox_problem(url)


def test_demo_host_guard_accepts_exact_https_sandbox():
    assert sandbox_problem('https://aca-test.accela.com/NULLISLAND/') is None


def test_model_completion_does_not_claim_verified_outcome():
    title, text = _friendly_result('goal_completed', True)
    assert title == 'COMPLETION DECLARED'
    assert 'model declared' in text.lower()
    assert 'verified it' not in text


@pytest.mark.parametrize('stop', ['approval_required', 'missing_information', 'no_valid_action',
                                  'portal_unavailable', 'repeated_action_failed'])
def test_stop_reason_cannot_erase_an_earlier_submission(stop):
    _, text = _friendly_result(stop, True)
    assert 'No mutation' not in text
    assert 'No inspection was created' not in text


def test_successful_page_read_is_not_proof_of_booking():
    run = SimpleNamespace(actions=[{'semantic_action': 'schedule_inspection', 'success': True,
                                    'verification': {'success': True}}])
    assert 'does not prove' in _mutation_evidence(run)


def test_later_unknown_submission_is_not_hidden_by_earlier_success():
    run = SimpleNamespace(actions=[
        {'semantic_action': 'schedule_inspection', 'success': True, 'verification': {'success': True}},
        {'semantic_action': 'reschedule_inspection', 'success': False},
    ])
    assert 'Reconcile before retrying' in _mutation_evidence(run)


def test_held_action_is_not_a_dispatched_mutation():
    run = SimpleNamespace(actions=[{'semantic_action': 'submit_payment', 'blocked': True}])
    assert 'No state-changing action is recorded' in _mutation_evidence(run)


def test_reasoning_prompt_loads_outside_checkout(monkeypatch, tmp_path):
    from licet.phase3.model_reasoning import _reasoning_prompt, _FALLBACK_PROMPT
    monkeypatch.chdir(tmp_path)
    assert _reasoning_prompt() != _FALLBACK_PROMPT


def test_invalid_reasoning_adapter_reports_domain_error(monkeypatch):
    from licet.phase3.errors import Phase3Error
    from licet.phase3.model_reasoning import _model_adapter
    monkeypatch.setenv('LICET_PHASE3_MODEL_ADAPTER', 'invalid-adapter')
    with pytest.raises(Phase3Error, match='bad adapter spec'):
        _model_adapter()
