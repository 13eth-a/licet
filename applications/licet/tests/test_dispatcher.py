"""The dispatcher is where the safety boundary becomes enforceable."""

from __future__ import annotations

import asyncio
import types

from licet.agent.state import AgentState
from licet.agent.stop_conditions import StopCondition, check_stop_condition
from licet.browser.dispatcher import ToolDispatcher, resolve_action, ToolCall
from licet.browser.errors import BrowserError, ToolError
from licet.browser.solari_client import ToolResult
from licet.safety.guard import grant_approval

SEARCH_URL = "https://aca-test.accela.com/nullisland/Cap/CapHome.aspx?module=Building"
CAPEDIT_CONTACT_URL = (
    "https://aca-test.accela.com/nullisland/Cap/CapEdit.aspx?module=Building"
    "&stepNumber=2&pageNumber=2"
)


class RecordingClient:
    """A SolariClient stand-in that records what actually reached the browser."""

    def __init__(
        self,
        url: str = SEARCH_URL,
        *,
        ok: bool = True,
        error: ToolError | None = None,
        data: dict | None = None,
    ) -> None:
        self.calls: list[tuple] = []
        self.page = types.SimpleNamespace(url=url)
        self._ok = ok
        self._error = error
        self._data = dict(data or {})

    def _result(self, **data) -> ToolResult:
        return ToolResult(ok=self._ok, url=self.page.url, data={**self._data, **data}, error=self._error)

    async def navigate(self, url: str) -> ToolResult:
        self.calls.append(("navigate", url))
        return self._result()

    async def click(self, target) -> ToolResult:
        self.calls.append(("click", target.describe()))
        return self._result()

    async def type_text(self, target, value: str) -> ToolResult:
        self.calls.append(("type", target.describe(), value))
        return self._result()

    async def select(self, target, value: str) -> ToolResult:
        self.calls.append(("select", target.describe(), value))
        return self._result()

    async def read_page(self, include=None) -> ToolResult:
        self.calls.append(("read_page", tuple(include) if include else None))
        return self._result()

    async def screenshot(self, path: str | None = None) -> ToolResult:
        self.calls.append(("screenshot", path))
        return self._result()

    async def settle(self) -> None:
        self.calls.append(("settle", None))


def _state(**kwargs) -> AgentState:
    # The dispatcher's guard authorizes a state-changing action against the page
    # actually being driven, so a fixture that means to exercise the sandbox says
    # so — exactly as the real adapter does, whose scratch state now records the
    # URL the dispatcher read. An unobserved page is not sandbox.
    kwargs.setdefault("current_url", SEARCH_URL)
    kwargs.setdefault("goal", "Apply for a temporary sign permit")
    return AgentState(**kwargs)


CONTINUE_CLICK = {
    "name": "click",
    "args": {"target": "#ctl00_PlaceHolderMain_actionBarBottom_btnContinue", "by": "selector"},
}


# --- the commit point: the live duplicate-record hazard --------------------


def test_commit_point_click_is_treated_as_an_application_submission():
    client = RecordingClient()
    state = _state(flow_name="apply_application", flow_step="review", flow_page=5)
    outcome = asyncio.run(ToolDispatcher(client).execute(CONTINUE_CLICK, state))

    assert outcome["blocked"] is True
    assert outcome["semantic_action"] == "submit_application"
    assert outcome["resolution"]["provenance"] == "commit_point"
    assert client.calls == []  # nothing reached the browser
    assert state.pending_approval is not None
    assert state.pending_approval.action == "submit_application"
    assert check_stop_condition(state) is StopCondition.APPROVAL_REQUIRED


def test_granted_approval_lets_the_submission_run_exactly_once():
    client = RecordingClient()
    state = _state(flow_name="apply_application", flow_step="review")
    dispatcher = ToolDispatcher(client)

    asyncio.run(dispatcher.execute(CONTINUE_CLICK, state))
    assert grant_approval(state, "submit_application") is True

    outcome = asyncio.run(dispatcher.execute(CONTINUE_CLICK, state))
    assert outcome["success"] is True and outcome["blocked"] is False
    assert outcome["authorization"]["approved"] is True
    assert outcome["verification"] is not None
    assert client.calls == [
        ("click", "selector=#ctl00_PlaceHolderMain_actionBarBottom_btnContinue"),
        ("read_page", None),
    ]
    # the grant is consumed: the same click is held again next time
    assert state.pending_approval is None
    assert check_stop_condition(state) is not StopCondition.APPROVAL_REQUIRED


def test_approval_for_one_action_does_not_authorize_another():
    client = RecordingClient()
    state = _state(flow_name="apply_application", flow_step="review")
    dispatcher = ToolDispatcher(client)
    asyncio.run(dispatcher.execute(CONTINUE_CLICK, state))
    grant_approval(state, "submit_application")

    payment = {
        "name": "click",
        "args": {"target": "Make a Payment", "by": "text"},
    }
    outcome = asyncio.run(dispatcher.execute(payment, state))
    assert outcome["semantic_action"] == "enter_payment_details"
    assert outcome["blocked"] is True
    assert client.calls == []


# --- resolution: intent > commit point > labels > text ---------------------


def test_dangerous_target_text_is_held_even_outside_a_flow():
    client = RecordingClient()
    state = _state()
    outcome = asyncio.run(
        ToolDispatcher(client).execute(
            {"name": "click", "args": {"target": "Submit Application", "by": "text"}}, state
        )
    )

    assert outcome["semantic_action"] == "submit_application"
    assert outcome["resolution"]["provenance"] == "target_text"
    assert client.calls == []


def test_benign_section_click_is_allowed_and_reads():
    client = RecordingClient()
    state = _state()
    outcome = asyncio.run(
        ToolDispatcher(client).execute(
            {"name": "click", "args": {"target": "Attachments", "by": "text"}}, state
        )
    )

    assert outcome["success"] is True and outcome["blocked"] is False
    assert outcome["semantic_action"] == "read_record"
    assert client.calls == [("click", "text=Attachments")]


def test_payments_section_is_not_mistaken_for_a_payment_action():
    state = _state()
    resolution = resolve_action(
        ToolCall("click", {"target": "Payments", "by": "text"}), state
    )
    assert resolution.action == "read_record"


def test_benign_intent_cannot_submit_on_a_commit_step():
    """The hole this closes: a harmless label must not bypass the commit point."""
    client = RecordingClient()
    state = _state(flow_name="apply_application", flow_step="review")
    outcome = asyncio.run(
        ToolDispatcher(client).execute(
            {
                "name": "click",
                "args": {
                    "target": "#ctl00_PlaceHolderMain_actionBarBottom_btnContinue",
                    "by": "selector",
                    "intent": "read_record",
                },
            },
            state,
        )
    )

    assert outcome["semantic_action"] == "submit_application"
    assert outcome["resolution"]["provenance"] == "commit_point"
    assert outcome["blocked"] is True
    assert client.calls == []
    assert "does not acknowledge" in outcome["resolution"]["context"]


def test_commit_acknowledging_intent_is_honoured_then_held():
    client = RecordingClient()
    state = _state(flow_name="apply_application", flow_step="review")
    outcome = asyncio.run(
        ToolDispatcher(client).execute(
            {
                "name": "click",
                "args": {"target": "Continue", "by": "text", "intent": "submit_application"},
            },
            state,
        )
    )
    assert outcome["semantic_action"] == "submit_application"
    assert outcome["resolution"]["provenance"] == "intent"
    assert outcome["blocked"] is True  # still held for approval


def test_an_intent_cannot_downgrade_what_a_control_says_it_does():
    # The model supplies both the target and the intent, so the target's own text
    # has to outrank a milder label: otherwise a commit could be relabelled as a
    # read and sail past the guard as an automatic action.
    resolution = resolve_action(
        ToolCall("click", {"target": "Cancel", "by": "text", "intent": "read_record"}), _state()
    )
    assert resolution.action == "cancel_inspection"
    assert resolution.provenance == "target_text"
    assert "cannot lower" in resolution.context


def test_an_intent_still_refines_an_ambiguous_target():
    resolution = resolve_action(
        ToolCall("click", {"target": "Billing", "by": "text", "intent": "read_record"}), _state()
    )
    assert resolution.action == "read_record"
    assert resolution.provenance == "intent"


def test_an_intent_may_raise_the_risk_of_an_ambiguous_target():
    resolution = resolve_action(
        ToolCall("click", {"target": "Billing", "by": "text", "intent": "submit_application"}), _state()
    )
    assert resolution.action == "submit_application"
    assert resolution.provenance == "intent"


def test_unknown_intent_is_not_accepted():
    resolution = resolve_action(
        ToolCall("click", {"target": "x", "intent": "teleport"}), _state()
    )
    assert resolution.action is None
    assert "not a classified action" in resolution.context


def test_unclassified_click_is_blocked_not_guessed():
    client = RecordingClient()
    state = _state()
    outcome = asyncio.run(
        ToolDispatcher(client).execute(
            {"name": "click", "args": {"target": "Wibble", "by": "text"}}, state
        )
    )

    assert outcome["blocked"] is True
    assert outcome["semantic_action"] is None
    assert outcome["error"]["kind"] == "unknown"  # caller error, not a safety hold
    assert client.calls == []
    assert state.pending_approval is None


def test_a_live_page_refuses_an_approved_scheduling_click_without_touching_the_browser():
    # A human said yes; the environment still says no. The click never happens.
    client = RecordingClient(url="https://aca-prod.accela.com/NULLISLAND/Cap/CapDetail.aspx")
    state = _state(current_url=client.page.url)
    scheduled = {"name": "click", "args": {"target": "Schedule Inspection", "by": "text",
                                              "intent": "schedule_inspection"}}
    # Held first, then explicitly approved by a human: a live record stays
    # read-only, because the grant never outranks the environment.
    asyncio.run(ToolDispatcher(client).execute(scheduled, state))
    state.request_approval("schedule_inspection", "user said yes")
    state.grant_approval()
    outcome = asyncio.run(ToolDispatcher(client).execute(scheduled, state))

    assert outcome["blocked"] is True
    assert "only a positively identified sandbox" in outcome["authorization"]["reason"]
    assert client.calls == []


def test_navigation_between_approval_and_click_invalidates_the_grant():
    client = RecordingClient()
    state = _state()
    cancel = {"name": "click", "args": {"target": "Cancel Appointment", "by": "text",
                                        "intent": "cancel_inspection"}}
    asyncio.run(ToolDispatcher(client).execute(cancel, state))
    assert grant_approval(state, "cancel_inspection") is True

    # The user approved a cancellation of the record that was on screen. Another
    # record's page does not inherit that approval.
    state.observe_page(CAPEDIT_CONTACT_URL, page="record_details", signature="moved")
    outcome = asyncio.run(ToolDispatcher(client).execute(cancel, state))

    assert outcome["blocked"] is True
    assert client.calls == []
    assert state.pending_approval is not None  # re-asked about the new page


def test_the_goals_constraint_blocks_a_payment_click_in_a_sandbox():
    """The user's "don't spend money" outranks a sandbox approval."""
    client = RecordingClient()
    state = _state(goal="Schedule the next inspection, but don't spend any money")
    outcome = asyncio.run(
        ToolDispatcher(client).execute(
            {"name": "click", "args": {"target": "Submit Payment", "by": "text"}}, state
        )
    )

    assert outcome["blocked"] is True
    assert "forbidden by the user's instruction" in outcome["authorization"]["reason"]
    assert client.calls == []
    assert state.pending_approval is None  # nothing to approve: it is not permitted


def test_unknown_tool_is_rejected_without_calling_the_browser():
    client = RecordingClient()
    outcome = asyncio.run(
        ToolDispatcher(client).execute({"name": "teleport", "args": {}}, _state())
    )
    assert outcome["blocked"] is True
    assert client.calls == []


# --- state maintenance ----------------------------------------------------


def test_pre_calendar_continue_is_navigation_but_confirm_continue_remains_blocked():
    from licet.browser.dispatcher import VERIFY_AFTER_ACTIONS
    assert "navigate" not in VERIFY_AFTER_ACTIONS

    client = RecordingClient(url=(
        "https://aca-test.accela.com/NULLISLAND/Cap/CapDetail.aspx?IsToShowInspection=yes"
    ))
    dispatcher = ToolDispatcher(client)
    state = _state(
        current_url=client.page.url,
        goal="Measure calendar availability (read-only)",
        flow_name="schedule_inspection",
        flow_step="select_type",
    )

    advanced = asyncio.run(dispatcher.execute(
        {"name": "click", "args": {"target": "Continue", "by": "text", "intent": "navigate"}},
        state,
    ))
    assert advanced["success"] is True and advanced["semantic_action"] == "navigate"
    assert client.calls == [("click", "text=Continue")]

    client.calls.clear()
    confirm_state = _state(
        current_url=client.page.url,
        goal="Measure calendar availability (read-only)",
        flow_name="schedule_inspection",
        flow_step="confirm",
    )
    confirmation = asyncio.run(dispatcher.execute(
        {"name": "click", "args": {"target": "Continue", "by": "text", "intent": "schedule_inspection"}},
        confirm_state,
    ))
    assert confirmation["blocked"] is True
    assert confirmation["semantic_action"] == "schedule_inspection"
    assert client.calls == []


def test_read_page_is_allowed_and_updates_flow_position():
    client = RecordingClient(url=CAPEDIT_CONTACT_URL)
    state = _state()
    outcome = asyncio.run(ToolDispatcher(client).execute({"name": "read_page", "args": {}}, state))

    assert outcome["success"] is True
    assert state.flow_name == "apply_application"
    assert state.flow_step == "contact"
    assert state.flow_page == 2
    assert state.completed_steps
    assert state.step_count == 1


def test_failures_are_recorded_and_a_later_success_clears_them():
    failing = RecordingClient(
        ok=False,
        error=ToolError(BrowserError.POSTBACK_RACE, "element was detached from the DOM"),
    )
    state = _state()
    asyncio.run(
        ToolDispatcher(failing).execute(
            {"name": "click", "args": {"target": "Record Info", "by": "text"}}, state
        )
    )
    assert [failure.action for failure in state.failed_actions] == ["click"]

    asyncio.run(
        ToolDispatcher(RecordingClient()).execute(
            {"name": "click", "args": {"target": "Record Info", "by": "text"}}, state
        )
    )
    assert state.failed_actions == []


def test_a_failed_action_captures_a_screenshot_for_the_run_log(tmp_path):
    from licet.logging.logger import RunLogger

    failing = RecordingClient(
        ok=False,
        error=ToolError(BrowserError.POSTBACK_RACE, "element was detached from the DOM"),
        data={"verification": {
            "status": "unverified", "samples": 7, "elapsed_ms": 10000,
            "observation_error_type": "RuntimeError", "last_ready": False,
        }},
    )
    state = _state()
    logger = RunLogger("verification-log", log_dir=tmp_path)
    outcome = asyncio.run(
        ToolDispatcher(failing, logger=logger).execute(
            {"name": "click", "args": {"target": "Record Info", "by": "text"}}, state
        )
    )

    assert outcome["success"] is False
    assert outcome["failure_screenshot"] is not None
    logged = logger.read_all()[0]["browser_result"]
    assert logged["action_verification"]["samples"] == 7
    assert logged["action_verification"]["last_ready"] is False
    assert logged["failure_screenshot"] is None
    assert failing.calls == [("click", "text=Record Info"), ("screenshot", None)]


def test_post_action_verification_failure_is_not_reported_as_success():
    class VerificationFailClient(RecordingClient):
        async def read_page(self, include=None) -> ToolResult:
            self.calls.append(("read_page", tuple(include) if include else None))
            return ToolResult(
                ok=False,
                url=self.page.url,
                error=ToolError(BrowserError.SESSION_TIMEOUT, "session has expired"),
            )

    client = VerificationFailClient()
    state = _state(flow_name="schedule_inspection", flow_step="select_time")
    outcome = asyncio.run(
        ToolDispatcher(client).execute(
            {
                "name": "click",
                "args": {"target": "Continue", "by": "text", "intent": "schedule_inspection"},
            },
            state,
        )
    )

    assert outcome["success"] is False
    assert outcome["verified"] is False
    assert outcome["verification_error"]["kind"] == "session_timeout"
    assert state.step_count == 0
    assert state.failed_actions


def test_a_successful_action_does_not_capture_a_screenshot():
    client = RecordingClient()
    state = _state()
    outcome = asyncio.run(
        ToolDispatcher(client).execute(
            {"name": "click", "args": {"target": "Record Info", "by": "text"}}, state
        )
    )

    assert "failure_screenshot" not in outcome
    assert client.calls == [("click", "text=Record Info")]


def test_blocked_attempts_are_recorded_as_failures():
    state = _state(flow_name="apply_application", flow_step="review")
    asyncio.run(ToolDispatcher(RecordingClient()).execute(CONTINUE_CLICK, state))

    assert state.failed_actions
    assert "requires confirmation" in state.failed_actions[0].error


def test_wait_tool_settles_the_postback():
    client = RecordingClient()
    outcome = asyncio.run(ToolDispatcher(client).execute({"name": "wait", "args": {}}, _state()))
    assert outcome["success"] is True
    assert ("settle", None) in client.calls


def test_read_page_rejects_unknown_includes_at_the_dispatcher():
    outcome = asyncio.run(
        ToolDispatcher(RecordingClient()).execute(
            {"name": "read_page", "args": {"include": ["telepathy"]}}, _state()
        )
    )
    assert outcome["success"] is False
    assert outcome["error"]["kind"] == "unknown"


# --- logging is wired into the only path that can act ----------------------

# Phase 0 checklist said "set up logging immediately"; RunLogger existed with no
# callers outside its own test. Now every step the agent can actually take is
# recorded, including the ones the guard blocks (those matter most in an audit).


def test_dispatcher_writes_every_step_to_the_run_logger(tmp_path):
    from licet.logging.logger import RunLogger

    logger = RunLogger("run-logging-1", log_dir=tmp_path)
    client = RecordingClient()
    state = _state()
    dispatcher = ToolDispatcher(client, logger=logger)

    asyncio.run(dispatcher.execute({"name": "read_page", "args": {}}, state))
    # no intent and no flow: unresolvable, so it is blocked rather than guessed
    asyncio.run(dispatcher.execute({"name": "click", "args": {"target": "Something"}}, state))
    dispatcher.log_outcome(state, final_outcome="no bookable dates", stop_condition="APPROVAL_REQUIRED")

    records = logger.read_all()

    assert [record["event"] for record in records] == ["step", "step", "outcome"]
    assert records[0]["browser_action"] == "read_page"
    assert records[0]["reasoning_summary"].startswith("read_page -> read_record")
    assert records[1]["browser_result"]["blocked"] is True
    assert records[1]["errors"]  # the guard's reason is kept
    assert records[2]["final_outcome"] == "no bookable dates"
    assert records[2]["errors"] == ["APPROVAL_REQUIRED"]
    assert all(record["run_id"] == "run-logging-1" for record in records)


def test_dispatcher_without_a_logger_is_unchanged(tmp_path):
    """Logging is opt-in: no logger, no files, same behaviour."""
    client = RecordingClient()
    outcome = asyncio.run(
        ToolDispatcher(client).execute({"name": "read_page", "args": {}}, _state())
    )

    assert outcome["success"] is True
    assert not list(tmp_path.iterdir())


def test_uncertain_action_stops_run_instead_of_inviting_replay():
    client = RecordingClient(ok=False, error=ToolError(
        BrowserError.ACTION_OUTCOME_UNKNOWN, "Reconcile before replay"))
    state = _state()
    result = asyncio.run(ToolDispatcher(client).execute({
        "name": "click", "args": {"target": "Search", "intent": "search_records"}}, state))
    assert not result["success"]
    assert not result["error"]["retryable"]
    assert check_stop_condition(state) is StopCondition.NO_VALID_ACTION
