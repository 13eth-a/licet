from licet.safety.risk_levels import (
    KNOWN_ACTIONS,
    STATE_CHANGING_ACTIONS,
    RiskLevel,
    changes_state,
    classify,
    requires_confirmation,
)


def test_automatic_actions():
    for action in ["search_permit", "navigate", "read_status", "schedule_inspection", "reschedule_inspection"]:
        assert classify(action) is RiskLevel.AUTOMATIC
        assert not requires_confirmation(action)


def test_confirmation_required_actions():
    for action in ["cancel_inspection", "submit_payment", "submit_application"]:
        assert classify(action) is RiskLevel.CONFIRMATION_REQUIRED
        assert requires_confirmation(action)
        assert changes_state(action)


def test_legal_attestation_is_prohibited_not_merely_consequential():
    # the phase 6 checklist lists "sign legal attestation for user" under prohibited, and the central
    # engine already classifies legal_attestation that way
    assert classify("accept_legal_attestation") is RiskLevel.PROHIBITED
    assert not requires_confirmation("accept_legal_attestation")
    assert changes_state("accept_legal_attestation")


def test_every_state_changing_action_is_known():
    assert changes_state("schedule_inspection") and changes_state("reschedule_inspection")
    assert not changes_state("read_status") and not changes_state("navigate")
    assert STATE_CHANGING_ACTIONS <= KNOWN_ACTIONS


def test_unknown_action_defaults_to_confirmation_required():
    assert classify("some_never_seen_action") is RiskLevel.CONFIRMATION_REQUIRED
