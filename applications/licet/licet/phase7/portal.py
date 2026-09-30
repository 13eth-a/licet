"""portal aware recovery: what aca just did, and what may be done about it"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from licet.browser import accela
from licet.phase7.recovery import FailureType, RecoveryResult


class PortalFinding(StrEnum):
    """the weird states aca produces, as the checklist's portal integration lane names them"""

    SESSION_EXPIRED_MODAL = "session_expired_modal"
    SESSION_EXPIRED = "session_expired"
    AJAX_SECTION_LOADING = "ajax_section_loading"
    EMPTY_TABLE_PENDING_ROWS = "empty_table_pending_rows"
    UNEXPECTED_MODAL = "unexpected_modal"
    CONSEQUENTIAL_MODAL = "consequential_modal"
    POPUP_OPEN = "popup_open"
    NEW_TAB_OPENED = "new_tab_opened"
    PORTAL_HOME_REDIRECT = "portal_home_redirect"
    WRONG_PAGE = "wrong_page"


# findings that mean the observation itself is not evidence: a decision made from it (a fact, a loop key,
# a "no inspections" claim) would be invented
UNSETTLED_FINDINGS = frozenset({
    PortalFinding.AJAX_SECTION_LOADING,
    PortalFinding.EMPTY_TABLE_PENDING_ROWS,
    PortalFinding.SESSION_EXPIRED_MODAL,
})

# findings that end the run rather than invite any recovery attempt: a dead session cannot be
# re authenticated by clicking through, and a modal whose acceptance mutates something belongs to policy,
# never to recovery
TERMINAL_FINDINGS = frozenset({
    PortalFinding.SESSION_EXPIRED,
    PortalFinding.SESSION_EXPIRED_MODAL,
    PortalFinding.CONSEQUENTIAL_MODAL,
})

_FINDING_FAILURE_TYPES: dict[PortalFinding, FailureType] = {
    PortalFinding.SESSION_EXPIRED_MODAL: FailureType.PORTAL,
    PortalFinding.SESSION_EXPIRED: FailureType.PORTAL,
    PortalFinding.AJAX_SECTION_LOADING: FailureType.PORTAL,
    PortalFinding.EMPTY_TABLE_PENDING_ROWS: FailureType.PORTAL,
    PortalFinding.UNEXPECTED_MODAL: FailureType.BROWSER,
    PortalFinding.CONSEQUENTIAL_MODAL: FailureType.POLICY,
    PortalFinding.POPUP_OPEN: FailureType.BROWSER,
    PortalFinding.NEW_TAB_OPENED: FailureType.NAVIGATION,
    PortalFinding.PORTAL_HOME_REDIRECT: FailureType.NAVIGATION,
    PortalFinding.WRONG_PAGE: FailureType.NAVIGATION,
}


@dataclass(frozen=True)
class PageIdentity:
    """the settled identity of one portal page, for loop keys and checkpoints"""

    url_path: str | None = None
    record_number: str | None = None
    flow: str | None = None
    step: str | None = None

    @classmethod
    def from_observation(cls, observation: Mapping[str, Any] | None) -> "PageIdentity":
        observation = observation or {}
        url = str(observation.get("url") or observation.get("current_url") or "")
        flow = observation.get("flow") if isinstance(observation.get("flow"), Mapping) else {}
        flow = flow or {}
        record = observation.get("record_number") or observation.get("permit_id")
        if not record:
            # the record identity that actually addresses the page is in the capdetail url's capid1/2/3
            # stable across aca's display label reformatting, and present even when the header text failed
            # to parse
            ref = accela.parse_ref_from_url(url)
            record = "/".join(ref[key] for key in ("capID1", "capID2", "capID3")) if ref else None
        return cls(
            url_path=(urlparse(url).path or None) if url else None,
            record_number=str(record) if record else None,
            flow=str(flow.get("flow")) if flow.get("flow") else None,
            step=str(flow.get("step")) if flow.get("step") else None,
        )

    @classmethod
    def from_fingerprint(cls, fingerprint: Any) -> "PageIdentity":
        """adapt a `recovery.pagefingerprint` (checkpoint reuse, trace joins)"""
        if fingerprint is None:
            return cls()
        return cls(
            url_path=getattr(fingerprint, "url", None),
            record_number=getattr(fingerprint, "record_number", None),
            flow=None,
            step=getattr(fingerprint, "active_section", None),
        )

    def key(self) -> str:
        """the loop key component: stable for the same settled page, distinct for any other page, and never containing live render tokens"""
        parts = (self.url_path or "?", self.record_number or "-", self.flow or "-",
                 self.step or "-")
        return "|".join(parts)

    def as_dict(self) -> dict[str, str | None]:
        return {"url_path": self.url_path, "record_number": self.record_number,
                "flow": self.flow, "step": self.step}


@dataclass(frozen=True)
class PortalState:
    """one observation through the portal weirdness lens"""

    findings: tuple[PortalFinding, ...]
    identity: PageIdentity

    @classmethod
    def from_observation(cls, observation: Mapping[str, Any] | None) -> "PortalState":
        observation = observation or {}
        url = str(observation.get("url") or "")
        text = str(observation.get("text") or observation.get("important_visible_text") or "")
        raw = accela.detect_weirdness(
            text,
            url,
            popup_open=bool(observation.get("popup_open")),
            loading=observation.get("loading") or (),
            notices=observation.get("notices") or (),
        )
        if any(grid.get("row_count") == 0 and not grid.get("declared_empty") for grid in observation.get("grids", ())):
            raw = (*raw, PortalFinding.EMPTY_TABLE_PENDING_ROWS.value)
        findings = tuple(dict.fromkeys(PortalFinding(value) for value in (*raw, *observation.get("findings", ())) if value in PortalFinding._value2member_map_))
        # a wrong page is relational: only detectable against the record the run verified
        expected = observation.get("expected_record_number")
        if expected and url and "capdetail" in url.lower():
            identity = PageIdentity.from_observation(observation)
            if identity.record_number and identity.record_number != str(expected):
                findings += (PortalFinding.WRONG_PAGE,)
        return cls(findings, PageIdentity.from_observation(observation))

    @property
    def unsettled(self) -> bool:
        """true when the observation is mid render: not evidence of anything"""
        return any(finding in UNSETTLED_FINDINGS for finding in self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "findings": [f.value for f in self.findings],
            "identity": self.identity.as_dict(),
            "unsettled": self.unsettled,
        }


@dataclass(frozen=True)
class RecoveryRoute:
    """the portal half of one recovery decision"""

    finding: PortalFinding
    failure_type: FailureType
    terminal: bool
    strategy: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"finding": self.finding.value, "failure_type": self.failure_type.value,
                "terminal": self.terminal, "strategy": self.strategy, "reason": self.reason}


def route_recovery(state: PortalState) -> RecoveryRoute | None:
    """the recovery route for the worst finding in one observation, or none"""
    priority = list(PortalFinding)
    for finding in sorted(state.findings, key=lambda f: (f not in TERMINAL_FINDINGS, priority.index(f))):
        if finding is PortalFinding.SESSION_EXPIRED_MODAL:
            return RecoveryRoute(
                finding, FailureType.PORTAL, terminal=True, strategy="STOP",
                reason="session expiry rendered as a modal: re-authentication is a user action, not a click-through",
            )
        if finding is PortalFinding.SESSION_EXPIRED:
            return RecoveryRoute(
                finding, FailureType.PORTAL, terminal=True, strategy="STOP",
                reason="session expired; returning AUTH_REQUIRED rather than clicking through",
            )
        if finding is PortalFinding.CONSEQUENTIAL_MODAL:
            return RecoveryRoute(
                finding, FailureType.POLICY, terminal=True, strategy="STOP",
                reason="a modal with a real consequence is routed to policy, never dismissed by recovery",
            )
        if finding is PortalFinding.AJAX_SECTION_LOADING:
            return RecoveryRoute(
                finding, FailureType.PORTAL, terminal=False, strategy="WAIT_FOR_SETTLE",
                reason="the section is still rendering; the observation is not evidence yet",
            )
        if finding is PortalFinding.EMPTY_TABLE_PENDING_ROWS:
            return RecoveryRoute(
                finding, FailureType.PORTAL, terminal=False, strategy="WAIT_FOR_SETTLE",
                reason="the grid rendered without rows; re-settle before reading it as empty",
            )
        if finding is PortalFinding.UNEXPECTED_MODAL:
            return RecoveryRoute(
                finding, FailureType.BROWSER, terminal=False, strategy="CLOSE_INFORMATIONAL_MODAL",
                reason="an informational dialog may be closed if it blocks the target",
            )
        if finding is PortalFinding.POPUP_OPEN:
            return RecoveryRoute(
                finding, FailureType.BROWSER, terminal=False, strategy="DISMISS_OR_RETURN",
                reason="a popup owns the interaction; dismiss it or return to the original context",
            )
        if finding is PortalFinding.NEW_TAB_OPENED:
            return RecoveryRoute(
                finding, FailureType.NAVIGATION, terminal=False, strategy="RETURN_TO_ORIGIN_TAB",
                reason="return to the original tab and re-verify before continuing",
            )
        if finding is PortalFinding.PORTAL_HOME_REDIRECT:
            return RecoveryRoute(
                finding, FailureType.NAVIGATION, terminal=False, strategy="RECOVER_FROM_HOME",
                reason="re-search the known permit, verify its identity, then resume the intended section",
            )
        if finding is PortalFinding.WRONG_PAGE:
            return RecoveryRoute(
                finding, FailureType.NAVIGATION, terminal=False, strategy="RETURN_TO_RECORD",
                reason="the page belongs to another record; navigate back to the verified record and re-verify",
            )
    return None


def route_from_result(result: RecoveryResult) -> RecoveryRoute | None:
    """recover a route from a recorded recovery result (trace/audit use)"""
    strategy_routes: dict[str, PortalFinding] = {
        "WAIT_FOR_SETTLE": PortalFinding.AJAX_SECTION_LOADING,
        "CLOSE_INFORMATIONAL_MODAL": PortalFinding.UNEXPECTED_MODAL,
        "DISMISS_OR_RETURN": PortalFinding.POPUP_OPEN,
        "RETURN_TO_ORIGIN_TAB": PortalFinding.NEW_TAB_OPENED,
        "RECOVER_FROM_HOME": PortalFinding.PORTAL_HOME_REDIRECT,
        "RETURN_TO_RECORD": PortalFinding.WRONG_PAGE,
        "STOP": PortalFinding.SESSION_EXPIRED,
    }
    finding = strategy_routes.get(result.strategy)
    if finding is None:
        return None
    failure_type = _FINDING_FAILURE_TYPES[finding]
    return RecoveryRoute(finding, failure_type, terminal=result.strategy == "STOP",
                         strategy=result.strategy, reason=result.error or "")


def settled_browser_state(observation: Mapping[str, Any] | None,
                          *, expected_record_number: str | None = None) -> dict[str, Any]:
    """the `browser_state` slice the planner should persist from one read"""
    observation = dict(observation or {})
    if expected_record_number:
        observation["expected_record_number"] = expected_record_number
    state = PortalState.from_observation(observation)
    identity = state.identity
    # the planner's loop key reads active_section first: the flow step is the finest position aca exposes,
    # and a step change is exactly what a url only key cannot see inside a postback wizard
    return {
        "url": observation.get("url"),
        "active_section": identity.step,
        "flow": identity.flow,
        "step": identity.step,
        "record_number": identity.record_number,
        "findings": [f.value for f in state.findings],
        "unsettled": state.unsettled,
    }


def identity_from_world(world: Any) -> str:
    """the settled page state string for `loop_observed`, from a phase 5 world"""
    browser_state = getattr(world, "browser_state", None) or {}
    section = browser_state.get("active_section")
    if section:
        return str(section)
    record = browser_state.get("record_number")
    if record:
        return str(record)
    url = str(browser_state.get("url") or "")
    if url:
        path = urlparse(url).path
        return path or url
    return "unknown"


def audit_transient_identity_sources() -> tuple[dict[str, str], ...]:
    """which aca states produce a transient `active_section`/url (the audit the adversarial review’s handoff asked for), each with the mitigation now in place"""
    return (
        {
            "state": "AJAX sections still rendering (Inspections shows 'Loading...')",
            "transient_signal": "in-flight grid text ('no data available in table', 'loading data')",
            "mitigation": "UNSETTLED_FINDINGS: the observation is flagged mid-render; settled_browser_state excludes text and the read is re-settled before any decision",
        },
        {
            "state": "Postback wizards (CapDetail/CapEdit): one URL across every step",
            "transient_signal": "none in the URL — the step is only in the popup text",
            "mitigation": "PageIdentity carries flow+step from accela.locate, refined by visible text, so a step change changes the loop key even though the URL did not move",
        },
        {
            "state": "Record-section navigation (summary ↔ Inspections ↔ Fees)",
            "transient_signal": "URL never changes; the section list in the text churns",
            "mitigation": "active_section is the settled flow step, not the section list; text is excluded from identity",
        },
        {
            "state": "ACA display-label reformatting (000000014 vs BLD26-…)",
            "transient_signal": "display id changes spelling between pages",
            "mitigation": "record_number resolves to capID1/2/3 from the URL, the identity that actually addresses the record",
        },
        {
            "state": "Session expiry rendered as a modal, not a redirect",
            "transient_signal": "the page underneath is unchanged, so a URL/step key sees nothing",
            "mitigation": "session_expired_modal is a TERMINAL finding: routed to STOP (AUTH_REQUIRED), so no loop key is consulted against a dead session",
        },
    )


__all__ = [
    "PageIdentity",
    "PortalFinding",
    "PortalState",
    "RecoveryRoute",
    "audit_transient_identity_sources",
    "identity_from_world",
    "route_from_result",
    "route_recovery",
    "settled_browser_state",
]
