"""Error taxonomy for ACA/Playwright failures.

A single `success: bool` cannot drive recovery: the live portal fails in ways
that need *different* handling (Phase 0 review §2). Every class below was
observed on the real portal:

- POSTBACK_RACE   "element was detached from the DOM, retrying" — ACA
                  postbacks detach elements; an issued mutation is not safe to replay.
- TIMEOUT         the hidden `#divGlobalLoadingMask` iframe intercepting
                  pointer events, or a cosmetic `ButtonDisabled` class.
- AUTH_REQUIRED   the "Please login to continue" JS notice — no redirect, no
                  URL change, so navigation events cannot detect it.
- GATED           a deep link that renders for curl but is blocked in a
                  browser ("approved Address/Parcel Verification").
- RATE_LIMITED    Cloudflare 1015 on the back-office host; needs a cooldown,
                  not a retry.
- SESSION_TIMEOUT site-wide `SessionTimeout.js`; needs re-login.
- PORTAL_ERROR    ACA's own error page ("The file ... does not exist").
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BrowserError(str, Enum):
    """Stable browser failure vocabulary used by tools, logs, and evaluators.

    The long names below are the Phase 1 public vocabulary.  The shorter names
    are retained as aliases for compatibility with existing callers and logs.
    """

    POSTBACK_RACE = "postback_race"
    TIMEOUT = "timeout"
    ELEMENT_NOT_FOUND = "not_found"
    NOT_FOUND = "not_found"  # backwards-compatible alias
    ELEMENT_NOT_VISIBLE = "not_actionable"
    NOT_ACTIONABLE = "not_actionable"  # backwards-compatible alias
    ACTION_OUTCOME_UNKNOWN = "action_outcome_unknown"
    CLICK_FAILED = "click_failed"
    INPUT_FAILED = "input_failed"
    NAVIGATION_TIMEOUT = "navigation_timeout"
    UNEXPECTED_MODAL = "unexpected_modal"
    # Matched the DOM but is not actionable (hidden, zero-size, or a
    # dead-but-rendered nav item with `Active: False`). Distinct from NOT_FOUND
    # because the recovery differs: look for another route rather than
    # re-checking the selector.
    AMBIGUOUS_TARGET = "ambiguous_target"
    NAVIGATION_FAILED = "navigation_failed"
    AUTH_REQUIRED = "auth_required"
    GATED = "gated"
    RATE_LIMITED = "rate_limited"
    SESSION_TIMEOUT = "session_timeout"
    PORTAL_ERROR = "portal_error"
    UNKNOWN = "unknown"


# Candidates for recovery before dispatch or for navigation. An issued mutation
# with an unverified outcome is ACTION_OUTCOME_UNKNOWN and is never retryable.
RETRYABLE = frozenset(
    {
        BrowserError.POSTBACK_RACE,
        BrowserError.TIMEOUT,
        BrowserError.NAVIGATION_FAILED,
    }
)

# Retrying these immediately makes things worse: back off, or re-authenticate.
NEEDS_COOLDOWN = frozenset({BrowserError.RATE_LIMITED})
NEEDS_REAUTH = frozenset({BrowserError.AUTH_REQUIRED, BrowserError.SESSION_TIMEOUT})

# Order matters: Playwright's commonest failure message ("Timeout 30000ms
# exceeded ... waiting for locator(...)") must classify as TIMEOUT (retryable)
# rather than NOT_FOUND (terminal).
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
    """Map a raw SDK/portal message onto an error kind."""
    text = (message or "").lower()
    for kind, needles in _PATTERNS:
        if any(needle in text for needle in needles):
            return kind
    return BrowserError.UNKNOWN


def tool_error(message: str) -> ToolError:
    return ToolError(kind=classify_error(message), message=message)
