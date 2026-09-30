import pytest

from licet.browser.errors import BrowserError, classify_error, tool_error

LIVE_MESSAGES = [
    ("element was detached from the DOM, retrying", BrowserError.POSTBACK_RACE),
    ("Timeout 30000ms exceeded waiting for locator('#btnSearch')", BrowserError.TIMEOUT),
    ("Please login to continue.', 'Notice', true, 1", BrowserError.AUTH_REQUIRED),
    ("Access denied | aca-test.accela.com used Cloudflare to restrict access (Error 1015)",
     BrowserError.RATE_LIMITED),
    ("A session timeout has occurred, please log in again", BrowserError.SESSION_TIMEOUT),
    ("Access is denied: capSearchForm 8035R", BrowserError.GATED),
    ("Strict mode violation: locator resolved to 3 elements", BrowserError.AMBIGUOUS_TARGET),
    ("net::ERR_CONNECTION_RESET", BrowserError.NAVIGATION_FAILED),
    ("The file '/nullisland/NULLISLAND/Cap/CapDetail.aspx' does not exist", BrowserError.PORTAL_ERROR),
]


@pytest.mark.parametrize("message,expected", LIVE_MESSAGES)
def test_live_failure_messages_classify(message, expected):
    assert classify_error(message) is expected


def test_unknown_message_stays_unknown():
    assert classify_error("something entirely new") is BrowserError.UNKNOWN


def test_retry_classification_drives_recovery():
    assert tool_error("element was detached from the DOM, retrying").retryable
    assert tool_error("Timeout 30000ms exceeded").retryable
    rate_limited = tool_error("Error 1015 you are being rate limited")
    assert rate_limited.needs_cooldown and not rate_limited.retryable
    # auth failures need re login, not a retry
    auth = tool_error("Please login to continue")
    assert auth.needs_reauth and not auth.retryable
    assert not tool_error("no element matches '#nope'").retryable


def test_tool_error_serializes_for_the_run_log():
    payload = tool_error("element was detached from the DOM").as_dict()
    assert payload["kind"] == "postback_race"
    assert payload["retryable"] is True
    assert payload["message"]
