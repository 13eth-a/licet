"""phase 4 checklist cases, executed as data (the test portion)"""
from __future__ import annotations

from datetime import date

import pytest

from licet.eval import phase4_fixtures as fixtures
from licet.phase4.dates import DateConstraints, closest_alternatives, select_date
from licet.phase4.runner import ActionRequest, Phase4ActionRunner, preview

CASES = fixtures.build_cases()


def test_the_case_table_matches_the_checklist_split():
    groups = {}
    for case in CASES:
        groups[case.group] = groups.get(case.group, 0) + 1
    assert groups == {"scheduling": 10, "rescheduling": 5, "cancellation": 5, "safety": 5}
    assert len({case.case_id for case in CASES}) == len(CASES) == 25


@pytest.mark.parametrize("case", CASES, ids=fixtures.case_ids(CASES))
def test_checklist_case(case):
    result, portal = fixtures.run_case(case)
    assert result.success is case.expect_success, result.error
    if case.expect_error:
        assert result.error_code is not None and result.error_code.value == case.expect_error
    else:
        assert result.error_code is None
    assert result.verification_state.value == case.expect_verification
    assert result.verified is (case.expect_verification == "VERIFIED_SUCCESS")
    # the mutation count is the safety assertion: refusals must not submit
    assert len(portal.submits) == case.expect_submits
    if case.expect_portal_type:
        assert portal.submits[0][0] == case.expect_portal_type
    if case.expect_read_inspection_id:
        assert portal.reads[0][2] == case.expect_read_inspection_id
    if case.expect_alternatives:
        assert result.alternatives == case.expect_alternatives


def test_refused_cases_never_touched_the_portal_at_all():
    # policy/eligibility refusals must not even read the record; everything else that refuses must at
    # least not submit
    refused = [case for case in CASES if case.expect_submits == 0]
    assert {case.case_id for case in refused} >= {"S09", "C04", "F01", "F04", "F05"}
    for case in refused:
        _, portal = fixtures.run_case(case)
        assert portal.submits == []
    _, s09 = fixtures.run_case(next(case for case in CASES if case.case_id == "S09"))
    _, c04 = fixtures.run_case(next(case for case in CASES if case.case_id == "C04"))
    assert s09.reads == [] and c04.reads == []


def test_alternatives_are_the_nearest_dates_outside_the_window():
    constraints = DateConstraints(start=date(2026, 9, 21), end=date(2026, 9, 27), earliest=True)
    # 09-24 is inside the window (selectable, never an "alternative"); the rest are ordered by days
    # outside the window: 09-20 (1), 09-30 (3), 10-04 (7)
    assert closest_alternatives(
        ["2026-09-20", "2026-09-30", "2026-09-24", "2026-10-04"], constraints, limit=3
    ) == ["2026-09-20", "2026-09-30", "2026-10-04"]


def test_alternatives_respect_an_exact_requested_day():
    constraints = DateConstraints(preferred=date(2026, 9, 25), earliest=True)
    assert closest_alternatives(["2026-09-24", "2026-09-26"], constraints) == ["2026-09-24", "2026-09-26"]


def test_no_alternatives_when_the_window_is_satisfiable():
    constraints = DateConstraints(start=date(2026, 9, 21), end=date(2026, 9, 27), earliest=True)
    assert closest_alternatives(["2026-09-22"], constraints) == []


def test_executor_reports_alternatives_only_when_allowed():
    from licet.phase4.workflow import InspectionActionExecutor

    unavailable = fixtures.action(date_window_start="2026-09-28", date_window_end="2026-09-30")
    portal = fixtures.ScriptedPortal(fixtures.snapshot())
    result = InspectionActionExecutor(portal).execute(
        unavailable,
        eligible_types=(fixtures.INSPECTION_TYPE,),
        available_dates=("2026-09-24", "2026-09-25"),
    )
    assert result.error_code.value == "DATE_CONSTRAINT_UNSATISFIED"
    assert result.alternatives == ()  # not requested
    portal_again = fixtures.ScriptedPortal(fixtures.snapshot())
    allowed = InspectionActionExecutor(portal_again).execute(
        unavailable,
        eligible_types=(fixtures.INSPECTION_TYPE,),
        available_dates=("2026-09-24", "2026-09-25"),
        allow_alternatives=True,
    )
    assert allowed.error_code.value == "DATE_CONSTRAINT_UNSATISFIED"
    assert allowed.alternatives == ("2026-09-25", "2026-09-24")
    # advisory only: the reported dates are still never submitted
    assert portal_again.submits == []


def test_runner_and_preview_surface_the_alternatives_flag():
    request = ActionRequest("schedule", fixtures.PERMIT_ID, fixtures.INSPECTION_TYPE,
                            date_window_start="2026-09-28", date_window_end="2026-09-30",
                            allow_alternatives=True)
    assert preview(request)["allow_alternatives"] is True
    portal = fixtures.ScriptedPortal(fixtures.snapshot())
    result = Phase4ActionRunner(portal).run(
        request,
        eligible_types=(fixtures.INSPECTION_TYPE,),
        available_dates=("2026-09-24", "2026-09-25"),
    )
    assert result.alternatives == ("2026-09-25", "2026-09-24")
    assert result.as_dict()["alternatives"] == ["2026-09-25", "2026-09-24"]
    assert portal.submits == []


def test_selection_still_refuses_out_of_window_dates():
    constraints = DateConstraints(start=date(2026, 9, 28), end=date(2026, 9, 30), earliest=True)
    assert select_date(["2026-09-24", "2026-09-25"], constraints) is None
    assert closest_alternatives(["2026-09-24", "2026-09-25"], constraints) == ["2026-09-25", "2026-09-24"]
