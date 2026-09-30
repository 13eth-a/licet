from datetime import date

from licet.phase4.actions import ActionErrorCode, ActionVerificationState, InspectionAction, InspectionSnapshot
from licet.phase4.dates import normalize_date_constraints, select_date
from licet.phase4.matching import match_inspection_type
from licet.phase4.policy import decide_action_policy
from licet.phase4.workflow import InspectionActionExecutor
from licet.phase4.selection import select_inspection_action
from licet.phase3.state import NextActionCandidate, ReasoningResult, Uncertainty
from licet.safety.policy import ConfirmationRequest, Environment


def approval(selected: InspectionAction, **overrides) -> ConfirmationRequest:
    """The scoped approval a caller must present for a consequential action.

    Phase 6 refuses to turn a bare ``confirmed=True`` into permission: it names
    no permit, target or inspection, so it cannot be checked against what was
    actually approved. This is what the human-facing layers hand down instead.
    """
    values = {
        "action_type": selected.action_type, "permit_id": selected.permit_id,
        "target": selected.inspection_type or selected.existing_inspection_id or "",
        "consequence": "test approval", "inspection_id": selected.existing_inspection_id,
    }
    values.update(overrides)
    return ConfirmationRequest(**values)


class FakePortal:
    # This double stands in for the sandbox portal, and says so: an unidentified
    # portal is UNKNOWN and Phase 6 refuses every mutation from it.
    environment = Environment.SANDBOX

    def __init__(self, state, *, error=None, after=None):
        self.state, self.error, self.after, self.calls = state, error, after, []

    def read_inspection_state(self, permit_id, inspection_type=None, inspection_id=None):
        self.calls.append(("read", permit_id, inspection_type, inspection_id))
        return self.after if self.after is not None and len(self.calls) > 1 else self.state

    def submit_inspection_action(self, action, *, portal_type, selected_date=None):
        self.calls.append(("submit", portal_type, selected_date))
        if self.error:
            raise self.error
        return "CNF-1"


def snap(**kwargs):
    values = {"permit_id": "P-1", "inspection_id": "I-1", "inspection_type": "Rough Electrical", "status": "Eligible"}
    values.update(kwargs)
    return InspectionSnapshot(**values)


def action(**kwargs):
    return InspectionAction("schedule", "P-1", "Rough Electrical", **kwargs)


def test_exact_matching_accepts_word_order_variant():
    assert match_inspection_type("Rough Electrical", ["Electrical - Rough"]) == "Electrical - Rough"


def test_matching_is_case_and_punctuation_insensitive():
    assert match_inspection_type("rough electrical", ["Rough-Electrical"]) == "Rough-Electrical"


def test_matching_does_not_use_substrings():
    assert match_inspection_type("Electrical", ["Electrical Final"]) is None


def test_matching_ambiguous_exact_options_stops():
    assert match_inspection_type("Rough Electrical", ["Rough Electrical", "Rough Electrical"]) is None


def test_next_week_normalizes_to_monday_sunday():
    constraints = normalize_date_constraints("earliest available next week", reference=date(2026, 9, 21))
    assert constraints.start == date(2026, 9, 28)
    assert constraints.end == date(2026, 10, 4)


def test_after_wednesday_starts_thursday():
    constraints = normalize_date_constraints("after Wednesday", reference=date(2026, 9, 21))
    assert constraints.start == date(2026, 9, 24)


def test_before_date_is_inclusive_boundary():
    constraints = normalize_date_constraints("before October 1", reference=date(2026, 9, 1))
    assert constraints.end == date(2026, 10, 1)


def test_weekday_selects_exact_requested_day():
    constraints = normalize_date_constraints("Friday", reference=date(2026, 9, 21))
    assert constraints.preferred == date(2026, 9, 25)


def test_earliest_selection_is_chronological_not_input_order():
    constraints = normalize_date_constraints("earliest available", reference=date(2026, 9, 21))
    assert select_date(["2026-09-25", "2026-09-22", "2026-09-24"], constraints) == "2026-09-22"


def test_selection_preserves_date_window():
    constraints = normalize_date_constraints("next week", reference=date(2026, 9, 21))
    assert select_date(["2026-09-24", "2026-09-29", "2026-10-04", "2026-10-05"], constraints) == "2026-09-29"


def test_no_available_date_returns_none():
    constraints = normalize_date_constraints("next week", reference=date(2026, 9, 21))
    assert select_date(["2026-09-22"], constraints) is None


def test_schedule_policy_is_automatic():
    decision = decide_action_policy(action())
    assert decision.allowed and not decision.requires_confirmation


def test_reschedule_policy_is_automatic():
    decision = decide_action_policy(InspectionAction("reschedule", "P-1", "Rough Electrical"))
    assert decision.allowed and not decision.requires_confirmation


def test_cancel_requires_confirmation():
    decision = decide_action_policy(InspectionAction("cancel", "P-1", "Rough Electrical"))
    assert not decision.allowed and decision.requires_confirmation


def test_confirmed_cancel_is_allowed():
    assert decide_action_policy(InspectionAction("cancel", "P-1", "Rough Electrical"), confirmed=True).allowed


def test_schedule_success_is_verified_by_reread():
    portal = FakePortal(snap(), after=snap(status="Scheduled", scheduled_date="2026-09-24"))
    result = InspectionActionExecutor(portal).execute(action(), eligible_types=["Electrical - Rough"], available_dates=["2026-09-24"])
    assert result.success and result.verified
    assert result.verification_state is ActionVerificationState.VERIFIED_SUCCESS
    assert [call[0] for call in portal.calls] == ["read", "submit", "read"]


def test_schedule_uses_portal_terminology_after_match():
    portal = FakePortal(snap(), after=snap(status="Scheduled", scheduled_date="2026-09-24"))
    InspectionActionExecutor(portal).execute(action(), eligible_types=["Electrical - Rough"], available_dates=["2026-09-24"])
    assert portal.calls[1][1] == "Electrical - Rough"


def test_already_scheduled_is_idempotent():
    portal = FakePortal(snap(status="Scheduled", scheduled_date="2026-09-24"))
    result = InspectionActionExecutor(portal).execute(action(), eligible_types=["Rough Electrical"], available_dates=["2026-09-24"])
    assert result.error_code is ActionErrorCode.INSPECTION_ALREADY_SCHEDULED
    assert not any(call[0] == "submit" for call in portal.calls)


def test_ineligible_type_does_not_submit():
    portal = FakePortal(snap())
    result = InspectionActionExecutor(portal).execute(action(), eligible_types=["Final Electrical"])
    assert result.error_code is ActionErrorCode.INSPECTION_NOT_ELIGIBLE
    assert portal.calls == []


def test_missing_required_input_stops_before_submit():
    portal = FakePortal(snap(required_fields=("phone",)))
    result = InspectionActionExecutor(portal).execute(action(), eligible_types=["Rough Electrical"], required_inputs={})
    assert result.error_code is ActionErrorCode.MISSING_REQUIRED_INPUT
    assert not any(call[0] == "submit" for call in portal.calls)


def test_no_dates_is_reported_without_submission():
    portal = FakePortal(snap())
    result = InspectionActionExecutor(portal).execute(action(), eligible_types=["Rough Electrical"], available_dates=[])
    assert result.error_code is ActionErrorCode.NO_AVAILABLE_DATES
    assert not any(call[0] == "submit" for call in portal.calls)


def test_constraint_failure_does_not_expand_window():
    portal = FakePortal(snap())
    result = InspectionActionExecutor(portal).execute(action(date_window_start="2026-09-25", date_window_end="2026-09-26"), eligible_types=["Rough Electrical"], available_dates=["2026-09-24"])
    assert result.error_code is ActionErrorCode.DATE_CONSTRAINT_UNSATISFIED


def test_wrong_permit_is_protected():
    portal = FakePortal(InspectionSnapshot("P-2", "I-1", "Rough Electrical", "Eligible"))
    result = InspectionActionExecutor(portal).execute(action(), eligible_types=["Rough Electrical"])
    assert result.error_code is ActionErrorCode.STATE_MISMATCH
    assert not any(call[0] == "submit" for call in portal.calls)


def test_uncertain_submit_reconciles_success_without_retry():
    portal = FakePortal(snap(), error=TimeoutError(), after=snap(status="Scheduled", scheduled_date="2026-09-24"))
    result = InspectionActionExecutor(portal).execute(action(), eligible_types=["Rough Electrical"], available_dates=["2026-09-24"])
    assert result.success and result.verified
    assert [call[0] for call in portal.calls] == ["read", "submit", "read"]


def test_uncertain_submit_is_not_reported_success():
    portal = FakePortal(snap(), error=TimeoutError(), after=snap())
    result = InspectionActionExecutor(portal).execute(action(), eligible_types=["Rough Electrical"], available_dates=["2026-09-24"])
    assert not result.success and result.verification_state is ActionVerificationState.UNVERIFIED
    assert result.error_code is ActionErrorCode.UNCERTAIN_SUBMISSION


def test_state_mismatch_after_submit_is_not_success():
    portal = FakePortal(snap(), after=snap(status="Scheduled", scheduled_date="2026-09-25"))
    result = InspectionActionExecutor(portal).execute(action(), eligible_types=["Rough Electrical"], available_dates=["2026-09-24"])
    assert not result.success and result.verification_state is ActionVerificationState.STATE_MISMATCH


def test_reschedule_requires_scheduled_target():
    portal = FakePortal(snap())
    result = InspectionActionExecutor(portal).execute(InspectionAction("reschedule", "P-1", "Rough Electrical", existing_inspection_id="I-1"), eligible_types=["Rough Electrical"], available_dates=["2026-09-24"])
    assert result.error_code is ActionErrorCode.RESCHEDULE_FAILED


def test_reschedule_verifies_old_date_changed():
    portal = FakePortal(snap(status="Scheduled", scheduled_date="2026-09-20"), after=snap(status="Scheduled", scheduled_date="2026-09-24"))
    result = InspectionActionExecutor(portal).execute(InspectionAction("reschedule", "P-1", "Rough Electrical", existing_inspection_id="I-1"), eligible_types=["Rough Electrical"], available_dates=["2026-09-24"])
    assert result.success and result.previous_date == "2026-09-20"


def cancel(target: str | None = "I-1") -> InspectionAction:
    return InspectionAction("cancel", "P-1", "Rough Electrical", existing_inspection_id=target)


def test_cancel_never_submits_without_confirmation():
    portal = FakePortal(snap(status="Scheduled", scheduled_date="2026-09-24"))
    result = InspectionActionExecutor(portal).execute(cancel(), eligible_types=["Rough Electrical"])
    assert result.error_code is ActionErrorCode.ACTION_REQUIRES_CONFIRMATION
    assert not any(call[0] == "submit" for call in portal.calls)


def test_cancel_verifies_cancelled_state():
    portal = FakePortal(snap(status="Scheduled", scheduled_date="2026-09-24"), after=snap(status="Cancelled"))
    action = cancel()
    result = InspectionActionExecutor(portal).execute(action, eligible_types=["Rough Electrical"], approval=approval(action))
    assert result.success and result.verified


def test_cancel_with_only_a_boolean_is_not_authorized():
    # The Phase 6 addition: `confirmed=True` is not an approval. It carries no
    # target, so it could never be checked against the pending action.
    portal = FakePortal(snap(status="Scheduled", scheduled_date="2026-09-24"), after=snap(status="Cancelled"))
    result = InspectionActionExecutor(portal).execute(cancel(), eligible_types=["Rough Electrical"], confirmed=True)
    assert result.error_code is ActionErrorCode.ACTION_REQUIRES_CONFIRMATION
    assert not any(call[0] == "submit" for call in portal.calls)


def test_cancel_without_an_identified_target_is_refused():
    # "Cancel the Rough Electrical inspection" with no appointment named: there
    # is no way to bind the approval, the identity check or the verification to
    # one row, so it must not be resolved against whichever row is on screen.
    portal = FakePortal(snap(status="Scheduled", scheduled_date="2026-09-24"), after=snap(status="Cancelled"))
    action = cancel(None)
    result = InspectionActionExecutor(portal).execute(action, eligible_types=["Rough Electrical"], approval=approval(action))
    assert result.error_code is ActionErrorCode.TARGET_INSPECTION_UNIDENTIFIED
    assert not any(call[0] == "submit" for call in portal.calls)


def test_completed_inspection_cannot_be_cancelled():
    portal = FakePortal(snap(status="Completed"))
    result = InspectionActionExecutor(portal).execute(InspectionAction("cancel", "P-1", "Rough Electrical"), eligible_types=["Rough Electrical"], confirmed=True)
    assert result.error_code is ActionErrorCode.CANCELLATION_FAILED


def test_phase3_confidence_without_verified_context_cannot_become_action():
    reasoning = ReasoningResult("P-1", "s", "next", "answered", next_actions=[
        NextActionCandidate("Request inspection: Rough Electrical", "supported", .94)
    ])
    selected = select_inspection_action(reasoning, permit_id="P-1")
    assert selected.action is None
    assert "context" in selected.reason


def test_phase3_ambiguous_candidates_stop():
    reasoning = ReasoningResult("P-1", "s", "next", "answered", next_actions=[
        NextActionCandidate("Request inspection: Rough Electrical", "", .94),
        NextActionCandidate("Request inspection: Rough Plumbing", "", .94),
    ])
    assert select_inspection_action(reasoning, permit_id="P-1").action is None


def test_phase3_conflict_stops_action_selection():
    reasoning = ReasoningResult("P-1", "s", "next", "conflicting", contradictions=["conflict"])
    assert select_inspection_action(reasoning, permit_id="P-1").action is None


def test_phase3_blocking_uncertainty_stops_action_selection():
    reasoning = ReasoningResult("P-1", "s", "next", "partial", uncertainties=[
        Uncertainty("missing phone", "needed", blocks_answer=True)
    ])
    assert select_inspection_action(reasoning, permit_id="P-1").action is None


def test_audit_captures_before_and_after():
    portal = FakePortal(snap(), after=snap(status="Scheduled", scheduled_date="2026-09-24"))
    executor = InspectionActionExecutor(portal)
    executor.execute(action(), eligible_types=["Rough Electrical"], available_dates=["2026-09-24"])
    assert len(executor.audits) == 1
    assert executor.audits[0].previous_state.status == "Eligible"
    assert executor.audits[0].verified_final_state.scheduled_date == "2026-09-24"
