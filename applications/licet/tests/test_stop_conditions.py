import pytest

from licet.agent.state import AgentState
from licet.agent.stop_conditions import (
    MAX_STALLED_STEPS,
    StopCondition,
    check_stop_condition,
    describe_stop,
)


def _state(**kwargs) -> AgentState:
    return AgentState(goal="Find permit X", **kwargs)


def test_clean_state_does_not_stop():
    assert check_stop_condition(_state()) is None


def test_goal_completed_outranks_everything():
    state = _state()
    state.request_approval("submit_application", "issues the record")
    state.record_missing_information("applicant phone")
    assert check_stop_condition(state, goal_completed=True) is StopCondition.GOAL_COMPLETED


def test_approval_outranks_missing_information():
    state = _state()
    state.request_approval("submit_application", "issues the record")
    state.record_missing_information("applicant phone")
    assert check_stop_condition(state) is StopCondition.APPROVAL_REQUIRED


def test_missing_information_is_reachable_and_resolvable():
    state = _state()
    state.record_missing_information("applicant name", "applicant name", "zip")
    assert state.missing_information == ["applicant name", "zip"]
    assert check_stop_condition(state) is StopCondition.MISSING_INFORMATION
    state.resolve_missing_information("zip")
    assert check_stop_condition(state) is StopCondition.MISSING_INFORMATION
    state.resolve_missing_information()
    assert check_stop_condition(state) is None


def test_ambiguous_record_is_reachable_and_resolvable():
    state = _state()
    state.record_ambiguous_candidates("BLD26-00468", "BLD26-00468", "000000014")
    assert state.ambiguous_candidates == ["BLD26-00468", "000000014"]
    assert check_stop_condition(state) is StopCondition.AMBIGUOUS_RECORD
    state.resolve_ambiguity()
    assert check_stop_condition(state) is None


def test_no_valid_action_is_reachable():
    state = _state()
    state.record_no_valid_action("no inspection slots in any window tried")
    assert check_stop_condition(state) is StopCondition.NO_VALID_ACTION


def test_portal_unavailable_is_reachable_and_clearable():
    state = _state()
    state.record_portal_issue("Cloudflare 1015 rate limit")
    assert check_stop_condition(state) is StopCondition.PORTAL_UNAVAILABLE
    state.clear_portal_issue()
    assert check_stop_condition(state) is None


def test_max_steps_is_overridable_per_run():
    state = _state()
    for _ in range(3):
        state.record_step("did something")
    assert check_stop_condition(state, max_steps=3) is StopCondition.MAX_STEPS_EXCEEDED
    assert check_stop_condition(state, max_steps=4) is None


def test_failure_counter_is_scoped_to_page_and_args():
    """one flaky selector failing once on two pages is not a repeated failure"""
    state = _state()
    for page in ("search_results", "record_details"):
        state.current_page = page
        state.record_failure("click", "element detached", args={"selector": "#btnSearch"})
    assert [failure.attempt_count for failure in state.failed_actions] == [1, 1]
    assert check_stop_condition(state) is None


def test_repeated_failure_stops_on_third_attempt_of_same_key():
    state = _state()
    state.current_page = "record_details"
    for _ in range(3):
        state.record_failure("click", "timeout", args={"selector": "#continue"})
    assert state.failed_actions[0].attempt_count == 3
    assert check_stop_condition(state) is StopCondition.REPEATED_ACTION_FAILED


def test_success_resets_the_failure_counter():
    """retry-then-succeed must not keep counting toward the stop condition"""
    state = _state()
    state.current_page = "record_details"
    args = {"selector": "#continue"}
    for _ in range(4):
        state.record_failure("click", "element detached", args=args)
        state.record_success("click", args=args)
    assert state.failed_actions == []
    assert check_stop_condition(state) is None


def test_stall_detection_resets_when_the_url_changes():
    """stall accounting needs a content signature: position alone is not enough"""
    state = _state()
    url = "https://aca-test.accela.com/NULLISLAND/Cap/CapEdit.aspx?stepNumber=3"
    # the first sighting of a url is not a stall; each repeat increments
    assert state.observe_page(url=url, page="contact", signature="one") == 0
    for expected in range(1, MAX_STALLED_STEPS):
        assert state.observe_page(url=url, page="contact", signature="one") == expected
    assert state.observe_page(url=url, page="contact", signature="one") == MAX_STALLED_STEPS
    assert check_stop_condition(state) is StopCondition.REPEATED_ACTION_FAILED
    assert state.observe_page(url=url + "&pageNumber=2", page="detail", signature="two") == 0
    assert check_stop_condition(state) is None


def test_a_click_that_returns_no_page_is_not_a_stall_step():
    state = _state()
    url = "https://aca-test.accela.com/NULLISLAND/Cap/CapDetail.aspx?capID3=000QB"
    state.observe_page(url=url, page="summary", signature="page")
    for _ in range(4):
        state.observe_page(url=url, page="summary", signature=None)

    assert state.stalled_steps == 0
    assert check_stop_condition(state) is None


def test_recent_actions_detect_repeated_action_state_pairs():
    state = _state()
    assert state.record_action("click", "Search", state="results") is False
    assert state.record_action("click", "Search", state="results") is False
    assert state.record_action("click", "Search", state="results") is True
    assert state.repeated_action("click", "Search", state="results") == 3
    assert state.record_action("click", "Search", state="form") is False


def test_observe_page_records_url_and_label():
    state = _state()
    state.observe_page(url="https://example.test/a", page="search_results")
    assert state.current_url == "https://example.test/a"
    assert state.current_page == "search_results"


@pytest.mark.parametrize("condition", list(StopCondition))
def test_every_condition_has_a_readable_explanation(condition):
    state = _state()
    state.request_approval("submit_application", "issues the record")
    state.record_missing_information("zip")
    state.record_ambiguous_candidates("BLD26-00468", "000000014")
    state.record_no_valid_action("no slots")
    state.record_portal_issue("1015")
    state.record_step("step")
    state.record_failure("click", "timeout", page="record_details")

    text = describe_stop(state, condition)
    assert isinstance(text, str) and text.strip()
    assert text != condition.value, f"{condition} fell through to its raw value"
