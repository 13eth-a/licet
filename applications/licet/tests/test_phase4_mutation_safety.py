"""phase 4 mutation safety regressions (adversarial review portion)"""
from datetime import date

import pytest

from licet.phase4.accela_portal import AccelaInspectionPortal
from licet.phase4.actions import (
    ActionErrorCode,
    ActionVerificationState,
    InspectionAction,
    InspectionSnapshot,
)
from licet.phase4.dates import DateConstraints, select_date
from licet.phase4.workflow import InspectionActionExecutor
from licet.safety.policy import Environment
from tests.test_phase4_accela_portal import (
    FakeClient,
    dispatcher_over,
    make_action,
    run,
)

RECORD_KEY = "NULLISLAND/Building/REC26/00000/000QD"


class Portal:
    """minimal inspectionportal whose first read is `before`, later reads `after`"""

    environment = Environment.SANDBOX

    def __init__(self, before, *, after=None, error=None):
        self.before, self.after, self.error = before, after, error
        self.reads = 0
        self.submits = []

    def read_inspection_state(self, permit_id, inspection_type=None, inspection_id=None):
        self.reads += 1
        if self.reads == 1 or self.after is None:
            return self.before
        return self.after

    def submit_inspection_action(self, action, *, portal_type, selected_date=None):
        self.submits.append((portal_type, selected_date))
        if self.error:
            raise self.error
        return "CNF-1"


def snap(**overrides):
    values = {
        "permit_id": "P-1",
        "inspection_id": "I-1",
        "inspection_type": "Rough Electrical",
        "status": "Not Scheduled",
        "record_key": RECORD_KEY,
    }
    values.update(overrides)
    return InspectionSnapshot(**values)


def schedule(**overrides):
    return InspectionAction("schedule", "P-1", "Rough Electrical", **overrides)


@pytest.mark.parametrize("status", ["Requested", "Pending"])
def test_schedule_is_blocked_when_a_request_is_already_in_flight(status):
    portal = Portal(snap(status=status))
    result = InspectionActionExecutor(portal).execute(
        schedule(), eligible_types=["Rough Electrical"], available_dates=["2026-09-24"]
    )
    assert result.error_code is ActionErrorCode.INSPECTION_ALREADY_SCHEDULED
    assert portal.submits == []


def test_not_scheduled_is_requestable_and_is_not_a_duplicate():
    # "not scheduled" is a requestable state, not an outstanding request: the idempotency gate must not
    # turn it into a permanent block
    portal = Portal(snap(status="Not Scheduled"), after=snap(status="Scheduled", scheduled_date="2026-09-24"))
    result = InspectionActionExecutor(portal).execute(
        schedule(), eligible_types=["Rough Electrical"], available_dates=["2026-09-24"]
    )
    assert result.success and result.verified
    assert portal.submits == [("Rough Electrical", "2026-09-24")]


def test_action_bound_to_a_different_record_key_is_refused_before_submit():
    portal = Portal(snap(record_key="NULLISLAND/Building/REC26/00000/OTHER"))
    result = InspectionActionExecutor(portal).execute(
        schedule(record_key=RECORD_KEY), eligible_types=["Rough Electrical"], available_dates=["2026-09-24"]
    )
    assert result.error_code is ActionErrorCode.STATE_MISMATCH
    assert portal.submits == []
    assert "record key" in (result.error or "")


def test_matching_record_key_proceeds_and_is_kept_in_the_audit():
    portal = Portal(snap(), after=snap(status="Scheduled", scheduled_date="2026-09-24"))
    executor = InspectionActionExecutor(portal)
    result = executor.execute(
        schedule(record_key=RECORD_KEY), eligible_types=["Rough Electrical"], available_dates=["2026-09-24"]
    )
    assert result.success and result.verified
    assert executor.audits[0].record_key == RECORD_KEY


def test_portal_that_declines_to_assert_a_record_key_cannot_authorize():
    # this used to be a documented residual: a portal returning no observed record key left only the
    # displayed permit check
    portal = Portal(snap(record_key=None), after=snap(status="Scheduled", scheduled_date="2026-09-24", record_key=None))
    result = InspectionActionExecutor(portal).execute(
        schedule(record_key=RECORD_KEY), eligible_types=["Rough Electrical"], available_dates=["2026-09-24"]
    )
    assert result.error_code is ActionErrorCode.RECORD_IDENTITY_UNVERIFIED
    assert portal.submits == []


def test_unavailable_requested_weekday_is_not_silently_substituted():
    # "friday": another allowed day must never stand in for it
    constraints = DateConstraints(preferred=date(2026, 9, 25), earliest=True)
    assert select_date(["2026-09-21", "2026-09-24"], constraints) is None


def test_available_requested_weekday_is_chosen_exactly():
    constraints = DateConstraints(preferred=date(2026, 9, 25), earliest=True)
    assert select_date(["2026-09-21", "2026-09-25"], constraints) == "2026-09-25"


def test_executor_reports_constraint_failure_when_preferred_day_is_unavailable():
    portal = Portal(snap())
    result = InspectionActionExecutor(portal).execute(
        schedule(preferred_date="2026-09-25"),
        eligible_types=["Rough Electrical"],
        available_dates=["2026-09-21", "2026-09-24"],
    )
    assert result.error_code is ActionErrorCode.DATE_CONSTRAINT_UNSATISFIED
    assert portal.submits == []


def test_no_availability_without_constraints_is_reported_as_no_dates():
    portal = Portal(snap())
    result = InspectionActionExecutor(portal).execute(schedule(), eligible_types=["Rough Electrical"])
    assert result.error_code is ActionErrorCode.NO_AVAILABLE_DATES
    assert portal.submits == []


def test_omitted_required_inputs_do_not_bypass_the_gate():
    # the runner defaults required_inputs to none; that must mean "nothing supplied", not "the portal
    # requires nothing"
    portal = Portal(snap(required_fields=("phone",)))
    result = InspectionActionExecutor(portal).execute(
        schedule(), eligible_types=["Rough Electrical"], available_dates=["2026-09-24"]
    )
    assert result.error_code is ActionErrorCode.MISSING_REQUIRED_INPUT
    assert portal.submits == []


def test_supplied_required_input_allows_the_action():
    portal = Portal(snap(required_fields=("phone",)), after=snap(status="Scheduled", scheduled_date="2026-09-24"))
    result = InspectionActionExecutor(portal).execute(
        schedule(),
        eligible_types=["Rough Electrical"],
        available_dates=["2026-09-24"],
        required_inputs={"phone": "555-0100"},
    )
    assert result.success and result.verified


def test_reschedule_to_the_current_date_is_not_reported_success():
    # the state would be identical whether or not the reschedule ran, so "old date changed to new date"
    # cannot be verified
    portal = Portal(snap(status="Scheduled", scheduled_date="2026-09-24"))
    result = InspectionActionExecutor(portal).execute(
        InspectionAction("reschedule", "P-1", "Rough Electrical", existing_inspection_id="I-1"),
        eligible_types=["Rough Electrical"],
        available_dates=["2026-09-24"],
    )
    assert result.error_code is ActionErrorCode.RESCHEDULE_FAILED
    assert not result.verified
    assert portal.submits == []


def test_uncertain_submission_records_exactly_one_audit():
    portal = Portal(snap(), error=TimeoutError(), after=snap())
    executor = InspectionActionExecutor(portal)
    result = executor.execute(schedule(), eligible_types=["Rough Electrical"], available_dates=["2026-09-24"])
    assert result.error_code is ActionErrorCode.UNCERTAIN_SUBMISSION
    assert result.verification_state is ActionVerificationState.UNVERIFIED
    assert len(executor.audits) == 1
    assert executor.audits[0].browser_steps == ("submit", "re-read after uncertain response")


def test_post_submit_mismatch_records_exactly_one_audit():
    portal = Portal(snap(), after=snap(status="Scheduled", scheduled_date="2026-09-25"))
    executor = InspectionActionExecutor(portal)
    result = executor.execute(schedule(), eligible_types=["Rough Electrical"], available_dates=["2026-09-24"])
    assert result.error_code is ActionErrorCode.ACTION_VERIFICATION_FAILED
    assert result.verification_state is ActionVerificationState.STATE_MISMATCH
    assert len(executor.audits) == 1
    assert executor.audits[0].browser_steps == ("submit", "re-read inspection state")


def test_adapter_snapshot_carries_the_stable_record_key():
    portal = AccelaInspectionPortal(dispatcher_over(FakeClient()))
    snapshot = portal.read_inspection_state("BLD26-00469", "Electrical Final")
    assert snapshot.record_key == RECORD_KEY


def test_adapter_refuses_an_action_bound_to_another_record_key():
    client = FakeClient()
    portal = AccelaInspectionPortal(dispatcher_over(client))
    action = make_action(record_key="NULLISLAND/Building/REC26/00000/OTHER")
    with pytest.raises(RuntimeError, match="record identity"):
        run(portal.submit_inspection_action_async(action, portal_type="Electrical Final", selected_date="2026-09-24"))
    assert not any("schedule an inspection" in (click.get("text") or "").lower() for click in client.clicks)
