"""phase 4 adapter resilience: bounded retries and fail closed unmapped flows"""
from __future__ import annotations

import pytest

from licet.agent.state import AgentState
from licet.browser.dispatcher import ToolCall, resolve_action
from licet.browser.solari_client import ToolResult
from licet.phase4.accela_portal import AccelaInspectionPortal
from tests.test_phase4_accela_portal import (
    RECORD_URL,
    FakeClient,
    dispatcher_over,
    make_action,
    run,
)

MY_RECORDS_URL = "https://aca-test.accela.com/nullisland/Cap/MyRecordsCap.aspx"
RECORD_REF = {
    "capID1": "REC26", "capID2": "00000", "capID3": "000QD",
    "module": "Building", "agency_code": "NULLISLAND",
}


class FlakyClient(FakeClient):
    """fails the first n reads / waits, modelling a slow or dropped render"""

    def __init__(self, *, fail_reads: int = 0, fail_waits: int = 0, **kwargs):
        super().__init__(**kwargs)
        self.fail_reads, self.fail_waits = fail_reads, fail_waits
        self.read_calls = 0
        self.wait_calls = 0

    async def read_page(self, *, include=None, max_text=4000):
        self.read_calls += 1
        if self.read_calls <= self.fail_reads:
            return ToolResult(ok=False, url=self.page.url, data={})
        return await super().read_page(include=include, max_text=max_text)

    async def wait_for_text(self, *, present=None, absent=None, timeout_ms=15000, poll_ms=700):
        self.wait_calls += 1
        if self.wait_calls <= self.fail_waits:
            return ToolResult(ok=False, url=self.page.url, data={})
        return await super().wait_for_text(
            present=present, absent=absent, timeout_ms=timeout_ms, poll_ms=poll_ms
        )


class CommitFailingClient(FakeClient):
    """records and fails every click on the wizard's confirm step"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.commit_clicks = 0

    async def click(self, target):
        text = (target.text or target.label or target.selector or "").lower()
        if self.state == self.STATE_CONFIRM and text == "continue":
            self.commit_clicks += 1
            return ToolResult(ok=False, url=self.page.url, data={})
        return await super().click(target)


def test_a_transient_read_failure_is_retried_before_concluding_unknown():
    client = FlakyClient(fail_reads=1)
    portal = AccelaInspectionPortal(dispatcher_over(client))
    snapshot = portal.read_inspection_state("BLD26-00469", "Electrical Final")
    assert snapshot.status == "Not Scheduled"
    assert client.read_calls >= 2


def test_a_timed_out_settle_wait_is_retried_before_reading():
    client = FlakyClient(url=MY_RECORDS_URL, fail_waits=1)
    portal = AccelaInspectionPortal(dispatcher_over(client), record_ref=dict(RECORD_REF))
    snapshot = portal.read_inspection_state("BLD26-00469", "Electrical Final")
    assert snapshot.status == "Not Scheduled"
    assert client.wait_calls >= 2


def test_a_permanently_failing_read_is_bounded_and_reports_unknown():
    # no browser read ever succeeds: the adapter must degrade to an explicit unknown after a fixed number
    # of attempts, not loop
    client = FlakyClient(fail_reads=999)
    portal = AccelaInspectionPortal(dispatcher_over(client))
    snapshot = portal.read_inspection_state("BLD26-00469", "Electrical Final")
    assert snapshot.status.startswith("Unknown")
    assert snapshot.eligible is False
    assert client.read_calls == 2


def test_a_failed_commit_click_is_never_retried():
    client = CommitFailingClient()
    portal = AccelaInspectionPortal(dispatcher_over(client))
    with pytest.raises(RuntimeError, match="confirm step did not complete"):
        run(portal.submit_inspection_action_async(
            make_action(), portal_type="Electrical Final", selected_date="2026-09-24"
        ))
    assert client.commit_clicks == 1  # attempted once, not replayed


def test_adapter_refuses_reschedule_rather_than_driving_the_new_request_wizard():
    client = FakeClient()
    portal = AccelaInspectionPortal(dispatcher_over(client))
    action = make_action(action_type="reschedule")
    with pytest.raises(RuntimeError, match="reschedule flow is not mapped"):
        run(portal.submit_inspection_action_async(
            action, portal_type="Electrical Final", selected_date="2026-09-24"
        ))
    assert client.clicks == []  # refused before any wizard step


def test_adapter_still_refuses_cancellation_with_its_original_reason():
    client = FakeClient()
    portal = AccelaInspectionPortal(dispatcher_over(client))
    with pytest.raises(RuntimeError, match="cancellation flow is not mapped"):
        run(portal.submit_inspection_action_async(
            make_action(action_type="cancel"), portal_type="Electrical Final"
        ))
    assert client.clicks == []


def _confirm_state() -> AgentState:
    state = AgentState(goal="reschedule the appointment")
    state.flow_name = "schedule_inspection"
    state.flow_step = "confirm"
    return state


def test_a_reschedule_commit_intent_is_acknowledged_not_relabelled():
    resolution = resolve_action(
        ToolCall("click", {"target": "Continue", "by": "text",
                           "intent": "reschedule_inspection"}),
        _confirm_state(),
    )
    assert resolution.action == "reschedule_inspection"


def test_a_benign_intent_still_cannot_bypass_the_commit_point():
    # adding reschedule to the acknowledging set must not reopen the hole: a non committing intent on the
    # confirm step still resolves to the commit
    resolution = resolve_action(
        ToolCall("click", {"target": "Continue", "by": "text",
                           "intent": "read_record"}),
        _confirm_state(),
    )
    assert resolution.action == "schedule_inspection"
