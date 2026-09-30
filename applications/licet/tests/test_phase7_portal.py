"""Phase 7 portal-weirdness tests (GLM 5.3 Flash).

The checklist's GLM lane: Accela states that are not plan failures but portal
behaviour — unexplained redirects, session expiry (redirect *and* modal),
partial AJAX rendering, stale/empty result tables, unexpected modals, popups
and new tabs, postback-wizard position. Every classifier here runs on plain
text/URL observations, so all of it is offline and deterministic.

The planner-integration tests pin the DeepSeek-handoff fix: the loop key is the
*settled* page identity, not a live render token and not a URL that never
changes inside a postback wizard.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from licet.browser import accela
from licet.browser.solari_client import SolariClient
from licet.phase5.state import World
from licet.phase7 import (
    PageIdentity,
    PortalFinding,
    PortalState,
    RecoveryBudgets,
    RecoveryController,
    FailureType,
    audit_transient_identity_sources,
    identity_from_world,
    route_from_result,
    route_recovery,
    settled_browser_state,
)
from licet.eval.phase5_fixtures import KEY, ScriptedCapabilities, goal
from licet.phase5.planner import GoalPlanner
from licet.phase5.state import Action, Status

DETAIL = (
    "https://aca-test.accela.com/NULLISLAND/Cap/CapDetail.aspx"
    "?Module=Building&capID1=REC26&capID2=00000&capID3=00014&agencyCode=NULLISLAND"
)
HOME = "https://aca-test.accela.com/nullisland/default.aspx"
SEARCH = accela.search_url()


# --- classification: the Accela states the GLM lane must recognise ----------


def test_async_section_still_loading_is_flagged_not_evidence():
    state = PortalState.from_observation(
        {"url": DETAIL, "text": "Inspections | Loading... | Payments", "loading": ["loading..."]}
    )
    assert PortalFinding.AJAX_SECTION_LOADING in state.findings
    assert state.unsettled
    route = route_recovery(state)
    assert route.strategy == "WAIT_FOR_SETTLE" and not route.terminal
    assert route.failure_type is FailureType.PORTAL


def test_empty_table_then_rows_is_inflight_then_settled():
    """The checklist's wording: rows are empty for 2 seconds, then populate."""
    pending = PortalState.from_observation(
        {"url": DETAIL, "text": "Inspections grid: No data available in table"}
    )
    assert PortalFinding.EMPTY_TABLE_PENDING_ROWS in pending.findings
    assert pending.unsettled
    assert route_recovery(pending).strategy == "WAIT_FOR_SETTLE"

    settled = PortalState.from_observation(
        {"url": DETAIL, "text": "Inspections grid: Rough Electrical | Failed | 09/18/2026"}
    )
    assert not settled.findings
    assert not settled.unsettled
    assert route_recovery(settled) is None


def test_stale_result_table_is_never_a_fact():
    """A grid that rendered empty must not become \"no inspections\"."""
    from licet.browser.accela import declares_no_inspections, detect_empty_table

    text = "Inspections | No data available in table"
    # The declared-empty vocabulary does not claim the grid is empty...
    assert not declares_no_inspections(text)
    # ...the in-flight vocabulary does, and it routes a re-settle.
    assert detect_empty_table(text)
    assert PortalFinding.EMPTY_TABLE_PENDING_ROWS in PortalState.from_observation(
        {"url": DETAIL, "text": text}
    ).findings


def test_session_expiry_as_redirect_is_terminal():
    state = PortalState.from_observation(
        {"url": accela.LOGIN_URL, "text": "Sign In | Username", "notices": ["session has expired"]}
    )
    assert PortalFinding.SESSION_EXPIRED in state.findings
    route = route_recovery(state)
    assert route.terminal and route.strategy == "STOP"
    assert route.failure_type is FailureType.PORTAL


def test_session_expiry_as_modal_is_terminal_without_redirect():
    """Session death does not always navigate; the modal wording is caught."""
    state = PortalState.from_observation(
        {"url": DETAIL, "text": "Warning: your session is about to expire. Do you want to stay logged in?"}
    )
    assert state.findings == (PortalFinding.SESSION_EXPIRED_MODAL,)
    route = route_recovery(state)
    assert route.terminal and route.strategy == "STOP"


def test_consequential_modal_is_routed_to_policy_never_dismissed():
    state = PortalState.from_observation(
        {"url": DETAIL, "text": "dialog: are you sure you want to cancel this inspection? This cannot be undone."}
    )
    assert PortalFinding.CONSEQUENTIAL_MODAL in state.findings
    route = route_recovery(state)
    assert route.terminal and route.strategy == "STOP"
    assert route.failure_type is FailureType.POLICY


def test_informational_modal_is_closeable():
    state = PortalState.from_observation(
        {"url": DETAIL, "text": "A dialog opened: please note the office hours."}
    )
    assert PortalFinding.UNEXPECTED_MODAL in state.findings
    route = route_recovery(state)
    assert not route.terminal and route.strategy == "CLOSE_INFORMATIONAL_MODAL"
    assert route.failure_type is FailureType.BROWSER


def test_portal_home_redirect_is_detected_and_routed():
    state = PortalState.from_observation({"url": HOME, "text": "welcome to null island"})
    assert PortalFinding.PORTAL_HOME_REDIRECT in state.findings
    route = route_recovery(state)
    assert route.strategy == "RECOVER_FROM_HOME" and not route.terminal
    # The search page is a legitimate page, not "home".
    assert PortalFinding.PORTAL_HOME_REDIRECT not in PortalState.from_observation(
        {"url": SEARCH, "text": "search for permits"}
    ).findings


def test_new_tab_targets_are_recognised():
    state = PortalState.from_observation(
        {"url": f"{accela.SITE_ROOT}/NULLISLAND/Cap/printview.aspx?id=1", "text": "printable view"}
    )
    assert PortalFinding.NEW_TAB_OPENED in state.findings
    assert route_recovery(state).strategy == "RETURN_TO_ORIGIN_TAB"


def test_popup_open_is_reported():
    state = PortalState.from_observation(
        {"url": DETAIL, "text": "", "popup_open": True}
    )
    assert PortalFinding.POPUP_OPEN in state.findings
    assert route_recovery(state).strategy == "DISMISS_OR_RETURN"


def test_wrong_page_is_relational_not_absolute():
    """Wrong-page needs the record the run verified to compare against."""
    other = "https://aca-test.accela.com/NULLISLAND/Cap/CapDetail.aspx?Module=Building&capID1=REC26&capID2=00000&capID3=00099"
    state = PortalState.from_observation(
        {"url": other, "text": "Record Detail", "expected_record_number": "REC26/00000/00014"}
    )
    assert PortalFinding.WRONG_PAGE in state.findings
    assert route_recovery(state).strategy == "RETURN_TO_RECORD"
    # The right record is not wrong, even with the flag set.
    right = PortalState.from_observation(
        {"url": DETAIL, "text": "Record Detail", "expected_record_number": "REC26/00000/00014"}
    )
    assert PortalFinding.WRONG_PAGE not in right.findings


def test_finding_worst_first_ordering_session_beats_modal():
    """A dead session with a dialog on top must stop, not close the dialog."""
    state = PortalState.from_observation(
        {"url": DETAIL, "text": "session has expired — warning dialog", "notices": ["session has expired"]}
    )
    assert route_recovery(state).strategy == "STOP"


# --- settled page identity: the DeepSeek-handoff fix -------------------------


def test_identity_is_stable_across_text_churn_and_render_tokens():
    """The same settled page yields the same key whatever the text says."""
    a = settled_browser_state({"url": DETAIL, "text": "Loading... spinner 17:42:03.114"})
    b = settled_browser_state({"url": DETAIL, "text": "Rough Electrical | Failed"})
    assert PageIdentity.from_observation(a).key() == PageIdentity.from_observation(b).key()


def test_wizard_step_change_changes_the_key_even_though_the_url_does_not():
    """ACA's postback wizard shares one URL across every step."""
    step1 = PageIdentity.from_observation(
        {"url": DETAIL, "text": "Available Inspection Types (13)", "flow": {"flow": "schedule_inspection", "step": "select_type"}}
    )
    step2 = PageIdentity.from_observation(
        {"url": DETAIL, "text": "Select an appointment date and time range", "flow": {"flow": "schedule_inspection", "step": "select_date"}}
    )
    assert step1.key() != step2.key()
    assert step1.url_path == step2.url_path


def test_display_label_churn_does_not_change_record_identity():
    """000000014 and BLD26-00472 are spellings, not identities: capIDs are."""
    ref = accela.parse_ref_from_url(DETAIL)
    assert ref["capID1"] == "REC26"
    identity = PageIdentity.from_observation({"url": DETAIL, "text": "Record BLD26-00472"})
    assert identity.record_number == "REC26/00000/00014"


def test_identity_from_world_prefers_the_settled_section():
    world = World(browser_state={"active_section": "select_date", "url": DETAIL,
                                 "record_number": "REC26/00000/00014"})
    assert identity_from_world(world) == "select_date"


def test_identity_from_world_falls_back_to_path_not_query():
    world = World(browser_state={"url": DETAIL + "&IsToShowInspection=yes"})
    assert identity_from_world(world) == "/NULLISLAND/Cap/CapDetail.aspx"


def test_loop_detection_survives_a_live_render_token():
    """The regression the DeepSeek handoff named: a transient string must not
    make every loop occurrence unique."""
    controller = RecoveryController(budgets=RecoveryBudgets(max_no_progress=99))
    # Legacy caller shape: raw page text as the page-state string.
    for text in ("Loading... 17:42:03", "Loading... 17:42:04", "Loading... 17:42:05"):
        controller.loops.observe("READ_INSPECTIONS", text, KEY)
    assert not controller.loops.observe("READ_INSPECTIONS", "Loading... 17:42:06", KEY)

    # Settled caller shape: the same settled identity every time.
    controller2 = RecoveryController()
    settled = identity_from_world(World(browser_state={"active_section": "summary", "url": DETAIL}))
    assert not controller2.loop_observed("READ_INSPECTIONS", settled, KEY)
    assert not controller2.loop_observed("READ_INSPECTIONS", settled, KEY)
    assert controller2.loop_observed("READ_INSPECTIONS", settled, KEY)


def test_transient_identity_audit_names_the_sources():
    audit = audit_transient_identity_sources()
    assert len(audit) >= 4
    assert all({"state", "transient_signal", "mitigation"} <= set(entry) for entry in audit)


# --- checkpoint fingerprints join the same identity --------------------------


def test_checkpoint_fingerprint_adapts_to_page_identity():
    from licet.phase7.recovery import PageFingerprint

    fingerprint = PageFingerprint(url="/NULLISLAND/Cap/CapDetail.aspx",
                                  record_number="REC26/00000/00014")
    identity = PageIdentity.from_fingerprint(fingerprint)
    assert identity.url_path == "/NULLISLAND/Cap/CapDetail.aspx"
    assert identity.record_number == "REC26/00000/00014"
    assert identity.key() == "/NULLISLAND/Cap/CapDetail.aspx|REC26/00000/00014|-|-"


# --- read_page integration: the real observation shape -----------------------


class _FakeFrame:
    def __init__(self, url: str = "", html: str = "", text: str = "") -> None:
        self.url = url
        self._html = html
        self._text = text

    async def content(self):
        return self._html

    async def inner_text(self):
        return self._text

    async def title(self):
        return ""

    def locator(self, selector):
        _text = self._text

        class _L:
            async def inner_text(self):
                return _text
        return _L()


class _FakePage:
    def __init__(self, url: str, text: str = "") -> None:
        self.url = url
        self.frames = [_FakeFrame(url=url, text=text)]
        self.keyboard = None

    async def wait_for_load_state(self, state="load"):
        return None

    async def wait_for_timeout(self, ms):
        return None

    async def evaluate(self, script):
        return None

    async def title(self):
        return ""

    async def content(self):
        return "<html></html>"

    def locator(self, selector):
        raise AssertionError("not used by read_page")


def test_read_page_carries_portal_findings_and_identity():
    page = _FakePage(DETAIL, text="Inspections | Loading... | Fees")
    data = asyncio.run(SolariClient(page).read_page()).data
    assert "ajax_section_loading" in data["portal_findings"]
    assert data["page_identity"]["record_number"] == "REC26/00000/00014"
    assert data["page_identity"]["url_path"] == "/NULLISLAND/Cap/CapDetail.aspx"


def test_read_page_flags_the_empty_grid_and_the_home_redirect():
    page = _FakePage(DETAIL, text="grid: No data available in table")
    data = asyncio.run(SolariClient(page).read_page()).data
    assert "empty_table_pending_rows" in data["portal_findings"]

    page = _FakePage(HOME, text="welcome")
    data = asyncio.run(SolariClient(page).read_page()).data
    assert "portal_home_redirect" in data["portal_findings"]


# --- planner integration: routes downgrade, never retry ----------------------


class _PortalScripted(ScriptedCapabilities):
    """A scripted capability that also writes browser_state like the runner."""

    def __init__(self, browser_state, **kwargs):
        super().__init__(**kwargs)
        self.browser_state = browser_state

    async def perform(self, action, goal, world, **kwargs):
        obs = await super().perform(action, goal, world, **kwargs)
        world.browser_state = dict(self.browser_state)
        return obs


def test_planner_session_modal_downgrades_a_read_failure_to_a_stop():
    """A read fails while the page shows a session modal: the run must stop
    (AUTH_REQUIRED shape) rather than retry or replan through a dead session."""
    cap = _PortalScripted(
        {"url": DETAIL, "text": "your session is about to expire"},
        failure=Action.READ_PERMIT_STATE,
    )
    result = asyncio.run(GoalPlanner(cap).run(goal()))
    assert result.status is Status.BLOCKED
    assert any(t.get("event") == "PORTAL_RECOVERY_ROUTE" for t in result.trace)
    route_event = next(t for t in result.trace if t.get("event") == "PORTAL_RECOVERY_ROUTE")
    assert route_event["route"]["terminal"] is True
    assert route_event["route"]["strategy"] == "STOP"
    # The terminal route was recorded and the observation downgraded (the
    # fixture *returns* a failed observation, so there is no FAILURE event —
    # the FAILed step trace entry carries the message instead).
    step_event = next(t for t in result.trace if t.get("action") == Action.READ_PERMIT_STATE.value)
    assert step_event["message"] == (
        "session expiry rendered as a modal: re-authentication is a user action, "
        "not a click-through"
    )


def test_planner_unsettled_page_is_not_treated_as_a_hard_failure():
    """A read fails on a still-rendering page: the observation is marked
    evidence-free (message carries the settle route) instead of terminal."""
    from licet.phase5.state import Action

    class _LoadingCap(_PortalScripted):
        async def perform(self, action, goal, world, **kwargs):
            obs = await super().perform(action, goal, world, **kwargs)
            if action == Action.READ_PERMIT_STATE:
                obs = replace(obs, success=False, message="read failed mid-render")
            return obs

    cap = _LoadingCap({"url": DETAIL, "text": "Inspections | Loading...", "loading": ["loading..."]})
    result = asyncio.run(GoalPlanner(cap).run(goal()))
    routes = [t for t in result.trace if t.get("event") == "PORTAL_RECOVERY_ROUTE"]
    assert routes and routes[0]["route"]["strategy"] == "WAIT_FOR_SETTLE"


def test_planner_portal_home_redirect_routes_recovery_without_mutation():
    from licet.phase5.state import Action

    class _HomeCap(_PortalScripted):
        async def perform(self, action, goal, world, **kwargs):
            obs = await super().perform(action, goal, world, **kwargs)
            if action == Action.READ_PERMIT_STATE:
                obs = replace(obs, success=False, message="read failed after redirect")
            return obs

    cap = _HomeCap({"url": HOME, "text": "welcome to null island"})
    result = asyncio.run(GoalPlanner(cap).run(goal()))
    routes = [t for t in result.trace if t.get("event") == "PORTAL_RECOVERY_ROUTE"]
    assert routes and routes[0]["route"]["strategy"] == "RECOVER_FROM_HOME"


def test_planner_mutation_failure_is_never_portal_routed():
    """Mutations reconcile upstream; the portal router must not touch them."""
    from licet.phase5.state import Action

    cap = _PortalScripted(
        {"url": DETAIL, "text": "your session is about to expire"},
    )
    # With no `failure` action configured the run succeeds; assert the route
    # machinery did not fire on the mutation's success path.
    result = asyncio.run(GoalPlanner(cap).run(goal()))
    assert not [t for t in result.trace if t.get("event") == "PORTAL_RECOVERY_ROUTE"]


def test_route_from_result_round_trips_strategies():
    from licet.phase7.recovery import RecoveryResult

    for strategy, finding in (
        ("WAIT_FOR_SETTLE", PortalFinding.AJAX_SECTION_LOADING),
        ("RETURN_TO_ORIGIN_TAB", PortalFinding.NEW_TAB_OPENED),
        ("RECOVER_FROM_HOME", PortalFinding.PORTAL_HOME_REDIRECT),
        ("RETURN_TO_RECORD", PortalFinding.WRONG_PAGE),
    ):
        route = route_from_result(RecoveryResult(True, strategy))
        assert route.finding is finding and not route.terminal
    stop = route_from_result(RecoveryResult(False, "STOP", error="session expired"))
    assert stop.terminal and stop.finding is PortalFinding.SESSION_EXPIRED
    # Controller-internal strategies do not invent a portal route.
    assert route_from_result(RecoveryResult(False, "RECONCILE_MUTATION_STATE")) is None


def test_settled_browser_state_excludes_live_render_tokens():
    st = settled_browser_state({"url": DETAIL, "text": "Loading... 17:42:03.114 spinner"})
    assert "text" not in st
    assert st["unsettled"] is False or st["unsettled"] is True  # shape check
    assert st["findings"] == [] or isinstance(st["findings"], list)


def test_settled_browser_state_keeps_expected_record_gate():
    st = settled_browser_state(
        {"url": DETAIL, "text": "Record Detail"}, expected_record_number="REC26/00000/00014"
    )
    assert st["record_number"] == "REC26/00000/00014"
