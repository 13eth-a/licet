"""selection -> request -> policy/executor regressions for the narrow adapter"""
from dataclasses import replace
from datetime import date

import pytest

from licet.phase4.actions import ActionErrorCode, InspectionAction
from licet.phase4.runner import ActionRequest, Phase4ActionRunner, action_from_selection, preview
from licet.phase4.selection import ActionSelection, SelectionStatus
from tests.test_phase4_actions import FakePortal, snap


def selection(**kwargs):
    action = InspectionAction("schedule", "P-1", "Rough Electrical",
        date_window_start="2026-09-21", date_window_end="2026-09-27",
        constraints=["AM only", "do not expand window"])
    return ActionSelection(action, "supported", SelectionStatus.SELECTED,
        "agency/module/1/2/3", "snapshot-1", ("target", "eligibility"), **kwargs)


def test_handoff_preserves_dates_metadata_and_arbitrary_constraints():
    selected = selection()
    request = action_from_selection(selected)
    assert request.date_instruction is None
    assert request.date_window_start == "2026-09-21"
    assert request.date_window_end == "2026-09-27"
    assert request.constraints == ("AM only", "do not expand window")
    assert request.record_key == selected.record_key
    assert request.snapshot_id == selected.snapshot_id
    assert request.evidence_ids == selected.evidence_ids
    shown = preview(request)
    assert shown["date_window_start"] == request.date_window_start
    assert shown["constraints"] == list(request.constraints)
    assert shown["evidence_ids"] == list(selected.evidence_ids)


class CapturingExecutor:
    def execute(self, action, **kwargs):
        self.action, self.kwargs = action, kwargs
        return "captured"


def test_runner_passes_complete_action_to_executor():
    executor = CapturingExecutor()
    request = action_from_selection(selection())
    runner = Phase4ActionRunner(None, executor=executor)
    assert runner.run(request, eligible_types=["Rough Electrical"], available_dates=["2026-09-24"]) == "captured"
    action = executor.action
    assert action.date_window_start == request.date_window_start
    assert action.date_window_end == request.date_window_end
    assert action.constraints == list(request.constraints)
    assert action.record_key == request.record_key
    assert action.snapshot_id == request.snapshot_id
    assert action.evidence_ids == request.evidence_ids
    assert action.as_dict()["evidence_ids"] == ["target", "eligibility"]
    assert executor.kwargs["available_dates"] == ["2026-09-24"]


def test_additional_confirmation_reaches_policy_before_any_browser_action():
    request = action_from_selection(selection(requires_confirmation=True))
    portal = FakePortal(snap())
    result = Phase4ActionRunner(portal).run(request)
    assert preview(request)["requires_confirmation"]
    assert result.error_code is ActionErrorCode.ACTION_REQUIRES_CONFIRMATION
    assert not portal.calls


def test_confirmed_action_uses_window_and_preserves_audit_context():
    request = replace(action_from_selection(selection(requires_confirmation=True), confirmed=True), constraints=())
    portal = FakePortal(snap(record_key=request.record_key),
                        after=snap(record_key=request.record_key, status="Scheduled", scheduled_date="2026-09-24"))
    runner = Phase4ActionRunner(portal)
    result = runner.run(request, eligible_types=["Rough Electrical"],
                        available_dates=["2026-09-28", "2026-09-25", "2026-09-20", "2026-09-24"])
    assert result.success and result.verified
    assert ("submit", "Rough Electrical", "2026-09-24") in portal.calls
    audit = runner.executor.audits[-1].as_dict()
    assert audit["record_key"] == request.record_key
    assert audit["snapshot_id"] == request.snapshot_id
    assert audit["evidence_ids"] == list(request.evidence_ids)
    assert audit["requires_confirmation"] is True


def test_exact_requested_day_unavailable_never_falls_back():
    request = replace(action_from_selection(selection()), preferred_date="2026-09-24", constraints=())
    portal = FakePortal(snap(record_key=request.record_key))
    assert preview(request)["preferred_date"] == "2026-09-24"
    result = Phase4ActionRunner(portal).run(request, eligible_types=["Rough Electrical"],
                                           available_dates=["2026-09-25"])
    assert result.error_code is ActionErrorCode.DATE_CONSTRAINT_UNSATISFIED
    assert not any(call[0] == "submit" for call in portal.calls)


def test_explicit_window_intersects_date_language_without_widening():
    request = ActionRequest("schedule", "P-1", "Rough Electrical", "next week",
                            date_window_start="2026-09-23", date_window_end="2026-09-30")
    shown = preview(request, date(2026, 9, 20))
    assert shown["date_window_start"] == "2026-09-23"
    assert shown["date_window_end"] == "2026-09-27"


@pytest.mark.parametrize("overrides", [
    {"date_window_start": "2026-09-28", "date_window_end": "2026-09-21"},
    {"preferred_date": "2026-09-30"},
    {"date_instruction": "Friday", "preferred_date": "2026-09-24"},
    {"date_window_start": "not-a-date"},
])
def test_conflicting_or_invalid_dates_stop_before_browser(overrides):
    request = replace(action_from_selection(selection()), **overrides)
    portal = FakePortal(snap())
    with pytest.raises(ValueError):
        Phase4ActionRunner(portal).run(request, reference=date(2026, 9, 21))
    assert not portal.calls


def test_no_selected_action_cannot_be_adapted():
    with pytest.raises(ValueError):
        action_from_selection(ActionSelection(None, "ambiguous"))


@pytest.mark.parametrize("field", ["record_key", "snapshot_id"])
def test_conflicting_action_selection_context_is_rejected(field):
    selected = selection()
    selected = replace(selected, action=replace(selected.action, **{field: "foreign"}))
    with pytest.raises(ValueError, match=field):
        action_from_selection(selected)


def test_existing_action_metadata_and_confirmation_are_not_erased():
    action = InspectionAction("schedule", "P-1", "Rough Electrical", record_key="key",
                               snapshot_id="snapshot", evidence_ids=("e1",), requires_confirmation=True)
    request = action_from_selection(ActionSelection(action, "selected", SelectionStatus.SELECTED))
    assert request.record_key == "key" and request.snapshot_id == "snapshot"
    assert request.evidence_ids == ("e1",) and request.requires_confirmation
