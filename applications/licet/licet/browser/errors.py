"""error taxonomy for aca/playwright failures"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BrowserError(str, Enum):
    """stable browser failure vocabulary used by tools, logs, and evaluators"""

    POSTBACK_RACE = "postback_race"
    TIMEOUT = "timeout"
    ELEMENT_NOT_FOUND = "not_found"
    NOT_FOUND = "not_found"
    ELEMENT_NOT_VISIBLE = "not_actionable"
    NOT_ACTIONABLE = "not_actionable"
    ACTION_OUTCOME_UNKNOWN = "action_outcome_unknown"
    CLICK_FAILED = "click_failed"
    INPUT_FAILED = "input_failed"
    NAVIGATION_TIMEOUT = "navigation_timeout"
    UNEXPECTED_MODAL = "unexpected_modal"
    # matched the dom but is not actionable (hidden, zero size, or a dead but rendered nav item with
    # `active: false`)
    AMBIGUOUS_TARGET = "ambiguous_target"
    NAVIGATION_FAILED = "navigation_failed"
    AUTH_REQUIRED = "auth_required"
    GATED = "gated"
    RATE_LIMITED = "rate_limited"
    SESSION_TIMEOUT = "session_timeout"
    PORTAL_ERROR = "portal_error"
    UNKNOWN = "unknown"


# candidates for recovery before dispatch or for navigation
RETRYABLE = frozenset(
    {
        BrowserError.POSTBACK_RACE,
        BrowserError.TIMEOUT,
        BrowserError.NAVIGATION_FAILED,
    }
)

NEEDS_COOLDOWN = frozenset({BrowserError.RATE_LIMITED})
NEEDS_REAUTH = frozenset({BrowserError.AUTH_REQUIRED, BrowserError.SESSION_TIMEOUT})

# order matters: playwright's commonest failure message ("timeout 30000ms exceeded ... waiting for
# locator(...)") must classify as timeout (retryable) rather than not_found (terminal)
_PATTERNS: tuple[tuple[BrowserError, tuple[str, ...]], ...] = (
    (BrowserError.POSTBACK_RACE, ("detached from the dom", "element was detached")),
    (BrowserError.AUTH_REQUIRED, ("please login", "login to continue", "not logged in")),
    (BrowserError.SESSION_TIMEOUT, ("session timeout", "session has expired", "sessiontimeout")),
    (BrowserError.RATE_LIMITED, ("1015", "rate limited", "being rate limited")),
    (
        BrowserError.GATED,
        ("access is denied", "approved address", "parcel verification", "8035r"),
    ),
    (BrowserError.AMBIGUOUS_TARGET, ("strict mode violation", "resolved to", "multiple elements")),
    (BrowserError.NOT_ACTIONABLE, ("not visible", "outside of the viewport")),
    (BrowserError.TIMEOUT, ("timeout", "timed out")),
    (BrowserError.NOT_FOUND, ("no element", "not found", "no matches")),
    (BrowserError.NAVIGATION_FAILED, ("net::err", "err_", "navigation")),
    (BrowserError.PORTAL_ERROR, ("does not exist", "server error", "http 5")),
)


@dataclass(frozen=True)
class ToolError:
    kind: BrowserError
    message: str

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE

    @property
    def needs_cooldown(self) -> bool:
        return self.kind in NEEDS_COOLDOWN

    @property
    def needs_reauth(self) -> bool:
        return self.kind in NEEDS_REAUTH

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "message": self.message,
            "retryable": self.retryable,
            "needs_cooldown": self.needs_cooldown,
            "needs_reauth": self.needs_reauth,
        }


def classify_error(message: str) -> BrowserError:
    """map a raw sdk/portal message onto an error kind"""
    text = (message or "").lower()
    for kind, needles in _PATTERNS:
        if any(needle in text for needle in needles):
            return kind
    return BrowserError.UNKNOWN


def tool_error(message: str) -> ToolError:
    return ToolError(kind=classify_error(message), message=message)
