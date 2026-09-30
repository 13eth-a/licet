from licet.agent.state import AgentState
from licet.safety.guard import (
    GuardDecision,
    authorize,
    deny_approval,
    grant_approval,
)
from licet.safety.risk_levels import KNOWN_ACTIONS, RiskLevel, is_known


SANDBOX_URL = "https://aca-test.accela.com/nullisland/Cap/CapDetail.aspx"


def _state() -> AgentState:
    # The guard refuses a state-changing action unless the observed page is a
    # positively identified sandbox, so an authorizing fixture says which page it
    # is on — as the adapter's own state now does.
    return AgentState(goal="Schedule the earliest available electrical inspection",
                      current_url=SANDBOX_URL)


def test_automatic_action_is_allowed():
    state = _state()
    auth = authorize("read_inspection_history", state)
    assert auth.decision is GuardDecision.ALLOW
    assert auth.risk is RiskLevel.AUTOMATIC
    assert state.pending_approval is None


def test_consequential_action_is_held_and_records_approval_request():
    state = _state()
    auth = authorize("submit_application", state, context="apply_application step 'review'")

    assert auth.decision is GuardDecision.REQUIRE_APPROVAL
    assert state.pending_approval is not None
    assert state.pending_approval.action == "submit_application"
    # the approval request must explain itself to the user
    assert "review" in state.pending_approval.reason


def test_unclassified_action_is_blocked_not_allowed():
    state = _state()
    auth = authorize("teleport_the_record", state)
    assert auth.decision is GuardDecision.BLOCK
    assert not is_known("teleport_the_record")
    assert state.pending_approval is None  # nothing to approve: it is not a real action


def test_grant_approval_allows_exactly_that_action_once():
    state = _state()
    authorize("submit_application", state)

    assert grant_approval(state, "cancel_inspection") is False  # wrong action
    assert authorize("submit_application", state).decision is GuardDecision.REQUIRE_APPROVAL

    assert grant_approval(state, "submit_application") is True
    approved = authorize("submit_application", state)
    assert approved.decision is GuardDecision.ALLOW
    assert approved.approved is True  # caller consumes the grant

    # a different consequential action is still held
    assert authorize("enter_payment_details", state).decision is GuardDecision.REQUIRE_APPROVAL


def test_deny_approval_clears_the_request():
    state = _state()
    authorize("enter_payment_details", state)
    deny_approval(state)
    assert state.pending_approval is None


def test_payment_actions_are_consequential_not_automatic():
    for action in ("enter_payment_details", "submit_payment"):
        assert authorize(action, _state()).decision is GuardDecision.REQUIRE_APPROVAL


def test_legal_attestation_is_blocked_even_with_an_approval():
    # Prohibited means prohibited: a click-through cannot authorize signing a
    # legal attestation on the user's behalf.
    state = _state()
    state.request_approval("accept_legal_attestation", "user said yes")
    state.grant_approval()
    assert authorize("accept_legal_attestation", state).decision is GuardDecision.BLOCK


def test_a_state_changing_action_needs_an_identified_sandbox():
    # Unknown and live both refuse; only a positively identified sandbox mutates.
    unknown = AgentState(goal="Schedule the earliest available electrical inspection")
    assert authorize("schedule_inspection", unknown).decision is GuardDecision.BLOCK
    assert authorize("cancel_inspection", unknown).decision is GuardDecision.BLOCK
    live = AgentState(goal="g", current_url="https://aca-prod.accela.com/agency/record")
    live.request_approval("cancel_inspection", "user said yes")
    live.grant_approval()
    assert authorize("cancel_inspection", live).decision is GuardDecision.BLOCK
    # Reads are unaffected: live read-only is the normal live mode.
    assert authorize("read_inspection_history", unknown).decision is GuardDecision.ALLOW
    assert authorize("read_inspection_history", live).decision is GuardDecision.ALLOW


def test_catalogue_covers_the_actions_the_audit_found_missing():
    for action in (
        "login",
        "logout",
        "upload_document",
        "report_export",
        "add_to_collection",
        "register_account",
    ):
        assert action in KNOWN_ACTIONS
