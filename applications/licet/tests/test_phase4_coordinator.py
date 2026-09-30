"""end-to-end phase 4 workflow and metrics regressions"""
from dataclasses import replace

from licet.eval import phase4_fixtures as fixtures
from licet.logging.logger import RunLogger
from licet.phase3.state import Evidence, NextActionCandidate, ReasoningResult
from licet.phase4.actions import (
    ActionErrorCode,
    ActionVerificationState,
    InspectionAction,
    InspectionActionResult,
    InspectionSnapshot,
    MutationAudit,
)
from licet.phase4.coordinator import run_inspection_workflow
from licet.phase4.metrics import Phase4Metrics
from licet.phase4.selection import InspectionOption, SelectionContext, SelectionStatus

KEY = "NULLISLAND/Building/REC26/00000/00001"
PERMIT = "P-1"
TYPE = "Rough Electrical"
WINDOW = ("2026-09-28", "2026-10-04")


def reasoning(action: str = f"Schedule inspection: {TYPE}", **kwargs) -> ReasoningResult:
    candidate = NextActionCandidate(
        action, "supported target", .94, requirement_strength="likely", evidence_ids=["target"]
    )
    return ReasoningResult(
        KEY, "s1", "schedule the next inspection", "answered", next_actions=[candidate], **kwargs
    )


def context(**kwargs) -> SelectionContext:
    base = SelectionContext(
        PERMIT, KEY, "s1", True,
        (InspectionOption(TYPE, True, True, ("eligibility",)),),
        {name: Evidence(name, "inspections", record_key=KEY) for name in ("target", "eligibility")},
        history_complete=True,
    )
    return replace(base, **kwargs)


def scheduled(inspection_id: str = "I-1") -> InspectionSnapshot:
    return fixtures.snapshot(
        permit_id=PERMIT, record_key=KEY, inspection_id=inspection_id,
        status="Scheduled", scheduled_date="2026-09-24",
    )


def portal(*, before: InspectionSnapshot | None = None, after: InspectionSnapshot | None = None):
    before = before or fixtures.snapshot(permit_id=PERMIT, record_key=KEY)
    after = after if after is not None else fixtures.snapshot(
        permit_id=PERMIT, record_key=KEY, status="Scheduled", scheduled_date="2026-09-24"
    )
    return fixtures.ScriptedPortal(before, after=after)


def test_end_to_end_selects_executes_and_verifies():
    p = portal()
    outcome = run_inspection_workflow(
        reasoning(), permit_id=PERMIT, context=context(), portal=p,
        available_dates=("2026-09-24",),
    )
    assert outcome.stage == "execution" and outcome.success and outcome.executed
    assert outcome.status == ActionVerificationState.VERIFIED_SUCCESS.value
    assert p.submits == [(TYPE, "2026-09-24")]
    assert outcome.result.after.scheduled_date == "2026-09-24"
    assert outcome.audits and outcome.audits[0].permit_id == PERMIT
    assert outcome.as_dict()["success"] is True


def test_end_to_end_schedules_the_earliest_date_in_the_requested_window():
    request = InspectionAction(
        "schedule", PERMIT, TYPE,
        date_window_start=WINDOW[0], date_window_end=WINDOW[1],
    )
    p = portal(
        after=fixtures.snapshot(
            permit_id=PERMIT, record_key=KEY, status="Scheduled", scheduled_date="2026-09-29"
        )
    )
    outcome = run_inspection_workflow(
        reasoning(), permit_id=PERMIT, context=context(), portal=p, requested_action=request,
        available_dates=("2026-09-25", "2026-09-29", "2026-10-05"),
    )
    assert outcome.success
    assert p.submits == [(TYPE, "2026-09-29")]  # earliest inside the window, not the earliest overall


def test_selection_stop_never_touches_the_portal():
    p = portal()
    outcome = run_inspection_workflow(reasoning(), permit_id=PERMIT, context=None, portal=p)
    assert outcome.stage == "selection" and not outcome.selected and not outcome.executed
    assert outcome.status == SelectionStatus.NEEDS_DATA.value
    assert p.reads == [] and p.submits == []


def test_unsatisfiable_request_stops_at_validation_without_a_browser_read():
    p = portal()
    bad = InspectionAction(
        "schedule", PERMIT, TYPE, date_window_start="2026-09-28", date_window_end="2026-09-21"
    )
    outcome = run_inspection_workflow(
        reasoning(), permit_id=PERMIT, context=context(), portal=p, requested_action=bad
    )
    assert outcome.stage == "validation" and outcome.status == "INVALID_REQUEST"
    assert p.reads == [] and p.submits == []


def test_cancellation_confirmation_boundary_holds_end_to_end():
    p = portal(before=scheduled(), after=fixtures.snapshot(
        permit_id=PERMIT, record_key=KEY, status="Cancelled"
    ))
    request = InspectionAction("cancel", PERMIT, TYPE, existing_inspection_id="I-1")
    outcome = run_inspection_workflow(
        reasoning("Cancel inspection: " + TYPE), permit_id=PERMIT,
        context=context(inspections=(scheduled(),)), portal=p, requested_action=request,
    )
    assert outcome.stage == "execution" and not outcome.success
    assert outcome.result.error_code is ActionErrorCode.ACTION_REQUIRES_CONFIRMATION
    assert p.submits == []


def test_metrics_count_a_verified_end_to_end_run():
    metrics = Phase4Metrics()
    p = portal()
    run_inspection_workflow(
        reasoning(), permit_id=PERMIT, context=context(), portal=p,
        available_dates=("2026-09-24",), metrics=metrics,
        expected_action=InspectionAction("schedule", PERMIT, TYPE),
    )
    snapshot = metrics.as_dict()
    assert snapshot["selection_attempts"] == 1 and snapshot["actions_selected"] == 1
    assert snapshot["actions_attempted"] == 1 and snapshot["submission_attempts"] == 1
    assert snapshot["verified_successes"] == 1 and snapshot["scheduling_successes"] == 1
    assert metrics.verification_success_rate == 1.0
    assert metrics.scheduling_success_rate == 1.0
    assert metrics.action_selection_accuracy == 1.0
    assert snapshot["within_zero_targets"] is True and metrics.safety_violations == 0


def _audit(steps=("submit", "re-read inspection state")) -> MutationAudit:
    return MutationAudit(
        permit_id=PERMIT, inspection_type=TYPE, requested_action="schedule",
        previous_state=None, proposed_state=None, browser_steps=steps,
    )


def _result(**overrides) -> InspectionActionResult:
    values = dict(
        success=False, action_type="schedule", inspection_type=TYPE,
        verification_state=ActionVerificationState.VERIFIED_FAILURE,
        before=fixtures.snapshot(permit_id=PERMIT, record_key=KEY),
    )
    values.update(overrides)
    return InspectionActionResult(**values)


def test_metrics_detect_a_duplicate_submission_regression():
    metrics = Phase4Metrics()
    metrics.record(
        fixtures.action(), _result(before=scheduled()), audits=(_audit(),)
    )
    assert metrics.duplicate_submissions == 1
    assert metrics.zero_targets()["duplicate_submission"] == 1
    assert metrics.as_dict()["within_zero_targets"] is False


def test_metrics_detect_a_wrong_record_regression():
    metrics = Phase4Metrics()
    metrics.record(
        fixtures.action(record_key=fixtures.OTHER_RECORD_KEY),
        _result(), audits=(_audit(),),
    )
    assert metrics.wrong_record_mutations == 1


def test_metrics_detect_a_wrong_inspection_regression():
    metrics = Phase4Metrics()
    metrics.record(
        fixtures.action("reschedule", existing_inspection_id=fixtures.EXISTING_INSPECTION_ID),
        _result(action_type="reschedule", before=scheduled(inspection_id="I-1")),
        audits=(_audit(),),
    )
    assert metrics.wrong_inspection_mutations == 1


def test_metrics_detect_a_constraint_violation_regression():
    metrics = Phase4Metrics()
    metrics.record(
        fixtures.action(preferred_date="2026-09-25"),
        _result(
            success=True, verified=True,
            verification_state=ActionVerificationState.VERIFIED_SUCCESS,
            after=fixtures.snapshot(permit_id=PERMIT, record_key=KEY, status="Scheduled", scheduled_date="2026-09-24"),
        ),
        audits=(_audit(),),
    )
    assert metrics.constraint_violations == 1


def test_metrics_detect_an_unverified_success_regression():
    metrics = Phase4Metrics()
    metrics.record(
        fixtures.action(),
        _result(success=True, verified=False, verification_state=ActionVerificationState.UNVERIFIED),
        audits=(),
    )
    assert metrics.unverified_successes == 1
    assert metrics.zero_targets()["unverified_success"] == 1


def test_metrics_merge_sums_counters_and_dicts_without_mutating_inputs():
    first = Phase4Metrics(actions_attempted=2, duplicate_submissions=1, refusals={"X": 1})
    second = Phase4Metrics(actions_attempted=3, duplicate_submissions=1, refusals={"X": 1, "Y": 2})
    total = Phase4Metrics.combine([first, second])
    assert total.actions_attempted == 5 and total.duplicate_submissions == 2
    assert total.refusals == {"X": 2, "Y": 2}
    assert first.actions_attempted == 2 and first.refusals == {"X": 1}


def test_mutation_audit_and_kpis_are_persisted_to_the_run_log(tmp_path):
    logger = RunLogger("phase4-coordinator", log_dir=tmp_path)
    metrics = Phase4Metrics()
    run_inspection_workflow(
        reasoning(), permit_id=PERMIT, context=context(), portal=portal(),
        available_dates=("2026-09-24",), metrics=metrics, logger=logger,
    )
    logger.log_metrics("phase4_kpis", metrics.as_dict())
    records = logger.read_all()
    events = [record["event"] for record in records]
    assert "mutation_audit" in events and "metrics" in events
    audit = next(record for record in records if record["event"] == "mutation_audit")
    assert audit["audit"]["permit"] == PERMIT
    assert audit["audit"]["verified_final_state"]["scheduled_date"] == "2026-09-24"
    assert logger.aggregates["phase4_kpis"]["within_zero_targets"] is True
