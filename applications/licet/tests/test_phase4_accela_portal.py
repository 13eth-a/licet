"""Phase 4 adapter tests: the Accela portal adapter through the real guard.

The adapter (`licet/phase4/accela_portal.py`) must drive the portal only via
`ToolDispatcher`, so every test here runs the *real* dispatcher and safety
guard over a scripted `SolariClient`-shaped fake — the same stack the live
portal sees. The fake is a state machine (detail -> types -> calendar ->
times -> confirm -> result) because the real portal advances page state on
commit clicks and the dispatcher's post-action verification re-reads observe
the *new* page. Fixtures mirror the verified captures
(`logs/ni_backoffice/schedule/*_calendar_f10.html` markup, the type grid
`gvInspectionType_ctlNN_rdInspectionType`, popup Continue with
`href_disabled`) so the adapter is pinned to observed ACA markup.
"""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from licet.browser.dispatcher import ToolDispatcher
from licet.browser.errors import BrowserError, ToolError
from licet.browser.solari_client import Target, ToolResult
from licet.phase4 import accela_portal
from licet.phase4.accela_portal import AccelaInspectionPortal
from licet.phase4.actions import ActionErrorCode, ActionVerificationState, InspectionAction
from licet.phase4.workflow import InspectionActionExecutor

RECORD_URL = (
    "https://aca-test.accela.com/NULLISLAND/Cap/CapDetail.aspx?Module=Building"
    "&TabName=Building&capID1=REC26&capID2=00000&capID3=000QD&agencyCode=NULLISLAND&IsToShowInspection="
)
WIZARD_URL = RECORD_URL  # the wizard never changes the URL (postback popup)


def make_action(**kwargs):
    values = {"action_type": "schedule", "permit_id": "BLD26-00469", "inspection_type": "Electrical Final"}
    values.update(kwargs)
    return InspectionAction(**values)


def page_payload(**overrides):
    data = {
        "url": RECORD_URL,
        "text": "",
        "fields": [],
        "inspection_types": [],
        "inspection_type_total": None,
        "calendar": [],
        "selectable_times": "",
        "loading": [],
        "validation_errors": [],
        "frames": [],
    }
    data.update(overrides)
    return data


class FakeFrame:
    def __init__(self, url):
        self.url = url

    async def content(self):
        return ""

    async def inner_text(self):
        return ""


class FakeClient:
    """A state-machine fake of the ACA scheduling surface.

    States: detail -> types -> calendar -> times -> confirm -> result. Commit
    clicks advance the state; every read renders the *current* state, so the
    dispatcher's verification re-reads see the new page like a real postback.
    """

    STATE_DETAIL = "detail"
    STATE_TYPES = "types"
    STATE_CALENDAR = "calendar"
    STATE_TIMES = "times"
    STATE_CONFIRM = "confirm"
    STATE_RESULT = "result"

    def __init__(
        self,
        *,
        types=("Rough", "Electrical Final"),
        calendar_active=(22, 23, 24),
        times="8:00AM - 10:00AM\n1:00PM - 3:00PM",
        result_text="Inspection scheduled successfully. Confirmation Number: CNF-77K9",
        no_scheduling_link=False,
        wrong_record=False,
        scheduled_row=False,
        detail_scheduled_row=False,
        detail_types=True,
        catalog_required=(),
        catalog_page_size=10,
        loading=False,
        detail_empty_marker=True,
        url=RECORD_URL,
    ):
        self.page = type("Page", (), {"url": url, "frames": [FakeFrame(WIZARD_URL)]})()
        self.state = self.STATE_DETAIL
        self.types = tuple(types)
        self.calendar_active = tuple(calendar_active)
        self.times = times
        self.result_text = result_text
        self.no_scheduling_link = no_scheduling_link
        self.wrong_record = wrong_record
        self.scheduled_row = scheduled_row
        self.detail_scheduled_row = detail_scheduled_row
        self.detail_types = detail_types
        self.catalog_required = set(catalog_required)
        self.catalog_page_size = catalog_page_size
        self.type_page = 0
        self.detail_empty_marker = detail_empty_marker
        self.section_opened = False
        self.loading = loading
        self.clicks: list[dict] = []
        self.navigations: list[str] = []
        self.reads = 0

    # --- state rendering -----------------------------------------------------

    def _header(self):
        if self.wrong_record:
            return "Record BLD26-99999:\n Other\nRecord Status: Submitted"
        return "Record BLD26-00469:\n Commercial Electrical\nRecord Status: Submitted"

    def payload(self):
        header = self._header()
        loading = ["loading..."] if self.loading else []
        if self.state == self.STATE_DETAIL:
            link = "" if self.no_scheduling_link else "Schedule an Inspection"
            # The section content appears only after a section postback opened
            # it (the summary alone does not carry the declared-empty marker).
            empty = "\nYou have not added any inspections" if (self.detail_empty_marker or self.section_opened) else ""
            text = f"{header}\nInspections{empty}\n{link}".rstrip()
            if self.detail_scheduled_row:
                text += "\nElectrical Final | Scheduled | 09/24/2026"
            offered = (
                [{"name": name, "required": False} for name in self.types] if self.detail_types else []
            )
            return page_payload(url=self.page.url, text=text, inspection_types=offered, loading=loading)
        if self.state == self.STATE_TYPES:
            start = self.type_page * self.catalog_page_size
            shown = self.types[start:start + self.catalog_page_size]
            fields = [
                {"id": f"ctl00_phPopup_gvInspectionType_ctl{2 + i:02d}_rdInspectionType", "kind": "radio",
                 "label": f"{name} ({'required' if name in self.catalog_required else 'optional'})"}
                for i, name in enumerate(shown)
            ]
            pager = "< Prev 1 2 Next >" if start + len(shown) < len(self.types) else "< Prev 2"
            return page_payload(
                url=self.page.url,
                text=f"{header}\nAvailable Inspection Types ({len(self.types)})\n{pager}",
                inspection_types=[{"name": name, "required": name in self.catalog_required} for name in shown],
                inspection_type_total=len(self.types),
                fields=fields,
                loading=loading,
            )
        if self.state in (self.STATE_CALENDAR, self.STATE_TIMES):
            return page_payload(
                url=self.page.url,
                text=f"{header}\nSelect an appointment date and time range",
                calendar=[{"month": "", "active_days": list(self.calendar_active), "inactive_days": []}],
                selectable_times=self.times if self.state == self.STATE_TIMES else "",
                loading=loading,
            )
        if self.state == self.STATE_CONFIRM:
            return page_payload(url=self.page.url, text=f"{header}\nConfirm your inspection appointment", loading=loading)
        if self.scheduled_row:
            text = f"{header}\n{self.result_text}\nElectrical Final | Scheduled | 09/24/2026"
        else:
            text = f"{header}\n{self.result_text}"
        return page_payload(url=self.page.url, text=text, loading=loading)

    # --- client surface --------------------------------------------------------

    async def read_page(self, *, include=None, max_text=4000):
        self.reads += 1
        payload = self.payload()
        return ToolResult(ok=True, url=payload.get("url"), data=dict(payload))

    async def click(self, target: Target):
        self.clicks.append(
            {"target": target.describe(), "text": target.text, "label": target.label, "selector": target.selector}
        )
        text = (target.text or target.label or target.selector or "").lower()
        if self.state == self.STATE_TYPES and text in {"next >", "< prev"}:
            page_delta = 1 if text == "next >" else -1
            max_page = max(0, (len(self.types) - 1) // self.catalog_page_size)
            self.type_page = max(0, min(max_page, self.type_page + page_delta))
        elif self.state == self.STATE_DETAIL and text in ("inspections", "inspection history"):
            self.section_opened = True
        elif self.state == self.STATE_DETAIL and (
            "schedule an inspection" in text or text == "lnkinspectionschedule"
        ):
            self.state = self.STATE_TYPES
        elif self.state == self.STATE_TYPES and "rdinspectiontype" in text:
            pass  # radio choice; the wizard stays until Continue
        elif self.state == self.STATE_TYPES and text == "continue":
            self.state = self.STATE_CALENDAR
        elif self.state == self.STATE_CALENDAR and 'text="' in text:
            self.state = self.STATE_TIMES
        elif self.state == self.STATE_TIMES and text in self.times.lower():
            pass  # time range chosen
        elif self.state == self.STATE_TIMES and text == "continue":
            self.state = self.STATE_CONFIRM
        elif self.state == self.STATE_CONFIRM and text == "continue":
            self.state = self.STATE_RESULT
        return ToolResult(ok=True, url=self.page.url, data={})

    async def navigate(self, url):
        self.navigations.append(url)
        self.page.url = url
        self.state = self.STATE_DETAIL
        return ToolResult(ok=True, url=url, data={})

    async def wait_for_text(self, *, present=None, absent=None, timeout_ms=15000, poll_ms=700):
        body = str(self.payload().get("text") or "")
        ok = (present is None or present.lower() in body.lower()) and (
            absent is None or absent.lower() not in body.lower()
        )
        return ToolResult(ok=ok, url=self.page.url, data={"present": present, "absent": absent})


def dispatcher_over(client) -> ToolDispatcher:
    return ToolDispatcher(client)


def run(coro):
    return asyncio.run(coro)


# --- pure helpers in accela.py -------------------------------------------------


def test_parse_confirmation_number_standard():
    assert accela_portal.accela.parse_confirmation_number("Confirmation Number: CN-99X1") == "CN-99X1"


def test_parse_confirmation_number_hash_and_reference():
    assert accela_portal.accela.parse_confirmation_number("Confirmation #A22") == "A22"
    assert accela_portal.accela.parse_confirmation_number("Reference No. REF7Q") == "REF7Q"


def test_parse_confirmation_number_absent_or_phrase_only():
    assert accela_portal.accela.parse_confirmation_number("Your request was received.") is None
    assert accela_portal.accela.parse_confirmation_number("Confirmation Number:") is None


def test_resolve_calendar_months_implied_strip():
    months = accela_portal.accela.resolve_calendar_months(
        [{"month": "", "active_days": (3,)}, {"month": "", "active_days": ()}], reference=dt.date(2026, 9, 21)
    )
    assert months == [(2026, 9, (3,)), (2026, 10, ())]


def test_resolve_calendar_months_caption_wins_and_rolls_year():
    months = accela_portal.accela.resolve_calendar_months(
        [{"month": "December 2026", "active_days": (1,)}], reference=dt.date(2026, 9, 21)
    )
    assert months == [(2026, 12, (1,))]


def test_resolve_calendar_months_unknown_caption_yields_zero():
    months = accela_portal.accela.resolve_calendar_months(
        [{"month": "???", "active_days": (5,)}], reference=dt.date(2026, 9, 21)
    )
    assert months == [(0, 0, (5,))]


def test_active_day_selector_scopes_to_table_and_day():
    selector = accela_portal.accela.active_calendar_day_selector(0, 24)
    assert 'table[id*="calendar_calendar1"]' in selector
    assert 'td[class*="calendarday"]' in selector
    assert 'not([class*="calendardayinactive"])' in selector
    assert 'text="24"' in selector


# --- read path ------------------------------------------------------------------


def test_read_builds_snapshot_from_declared_empty_section():
    client = FakeClient(types=("Rough", "Electrical Final"))
    portal = AccelaInspectionPortal(dispatcher_over(client))
    snapshot = portal.read_inspection_state("BLD26-00469", "Electrical Final")
    assert snapshot.status == "Not Scheduled"
    assert snapshot.eligible is True


def test_read_marks_type_eligible_only_when_offered():
    portal = AccelaInspectionPortal(dispatcher_over(FakeClient(types=("Rough", "Electrical Final"))))
    assert portal.read_inspection_state("BLD26-00469", "Electrical Final").eligible is True
    portal2 = AccelaInspectionPortal(dispatcher_over(FakeClient(types=("Rough",))))
    assert portal2.read_inspection_state("BLD26-00469", "Electrical Final").eligible is False


def test_read_navigates_to_record_when_needed():
    # Navigation requires the *verified* record ref (Phase 2's output): the
    # adapter never guesses a deep link from a displayed id alone.
    client = FakeClient(url="https://aca-test.accela.com/nullisland/Cap/MyRecordsCap.aspx")
    ref = {"capID1": "REC26", "capID2": "00000", "capID3": "000QD", "module": "Building", "agency_code": "NULLISLAND"}
    portal = AccelaInspectionPortal(dispatcher_over(client), record_ref=ref)
    snapshot = portal.read_inspection_state("BLD26-00469", "Electrical Final")
    assert any("CapDetail.aspx" in url for url in client.navigations)
    assert snapshot.status == "Not Scheduled"


def test_read_without_verified_ref_does_not_guess_a_deeplink():
    client = FakeClient(url="https://aca-test.accela.com/nullisland/Cap/MyRecordsCap.aspx")
    portal = AccelaInspectionPortal(dispatcher_over(client))
    snapshot = portal.read_inspection_state("BLD26-00469", "Electrical Final")
    assert not client.navigations, "no navigation may be invented without a verified ref"
    assert snapshot.status.startswith("Unknown")


def test_read_wrong_record_fails_closed():
    portal = AccelaInspectionPortal(dispatcher_over(FakeClient(wrong_record=True)))
    snapshot = portal.read_inspection_state("BLD26-00469", "Electrical Final")
    assert snapshot.status.startswith("Unknown")
    assert snapshot.eligible is False


def test_read_loading_section_never_becomes_fact():
    # A mid-load read must degrade to unknown, not "not scheduled": the fake
    # keeps the section permanently loading so both reads report it.
    portal = AccelaInspectionPortal(dispatcher_over(FakeClient(loading=True)))
    snapshot = portal.read_inspection_state("BLD26-00469", "Electrical Final")
    assert snapshot.status.startswith("Unknown")


def test_read_falls_back_when_the_inspections_label_is_dead_but_rendered():
    """GLM Phase 9, live 2026-09-25: Null Island's detail page renders the
    'Inspections' anchor only as a hidden wrapper, so the exact-label click
    fails `not_actionable`. The read must fall back to the visible label and
    still produce a declared-empty snapshot — not an Unknown."""
    client = FakeClient(types=("Rough", "Electrical Final"), detail_types=False, detail_empty_marker=False)
    original_click = client.click

    async def hidden_wrapper_click(target):
        text = (getattr(target, "text", "") or getattr(target, "label", "") or "").lower()
        if text == "inspections":
            return ToolResult(
                ok=False,
                url=client.page.url,
                data={},
                error=ToolError(
                    BrowserError.NOT_ACTIONABLE,
                    "no visible element for text=Inspections across 9 frame(s); "
                    "present but not visible: [\"a:text-is('Inspections')\"]",
                ),
            )
        return await original_click(target)

    client.click = hidden_wrapper_click  # type: ignore[method-assign]
    portal = AccelaInspectionPortal(dispatcher_over(client))
    snapshot = portal.read_inspection_state("BLD26-00469", "Electrical Final")
    # the fallback label was attempted through the real dispatcher
    texts = [str(click.get("text") or "").lower() for click in client.clicks]
    assert "inspection history" in texts
    assert snapshot.status == "Not Scheduled"


def test_read_blocked_click_still_never_triggers_the_fallback():
    """A guard-blocked (not not_actionable) click must not open the fallback
    route: blocked means the guard held the action, and a hold is a decision,
    not a selector problem."""
    client = FakeClient(types=("Rough", "Electrical Final"), detail_empty_marker=False)
    original_click = client.click

    async def blocked_click(target):
        text = (getattr(target, "text", "") or getattr(target, "label", "") or "").lower()
        if text == "inspections":
            return ToolResult(ok=False, url=client.page.url, data={})
        return await original_click(target)

    client.click = blocked_click  # type: ignore[method-assign]
    portal = AccelaInspectionPortal(dispatcher_over(client))
    snapshot = portal.read_inspection_state("BLD26-00469", "Electrical Final")
    texts = [str(click.get("text") or "").lower() for click in client.clicks]
    assert "inspection history" not in texts
    # The failure is not `not_actionable`, so no fallback; and a failed section
    # open never invents "Not Scheduled" — the answer stays explicitly unknown.
    assert snapshot.status.startswith("Unknown")


def test_read_parses_scheduled_row():
    client = FakeClient(detail_scheduled_row=True)
    portal = AccelaInspectionPortal(dispatcher_over(client))
    snapshot = portal.read_inspection_state("BLD26-00469", "Electrical Final")
    assert snapshot.is_scheduled
    assert snapshot.scheduled_date == "2026-09-24"


# --- submit path: refusal shapes -------------------------------------------------


def test_submit_cancel_refuses_rather_than_improvises():
    portal = AccelaInspectionPortal(dispatcher_over(FakeClient()))
    with pytest.raises(RuntimeError, match="cancellation flow is not mapped"):
        run(portal.submit_inspection_action_async(make_action(action_type="cancel"), portal_type="Electrical Final"))


def test_submit_refuses_without_scheduling_link():
    portal = AccelaInspectionPortal(dispatcher_over(FakeClient(no_scheduling_link=True)))
    with pytest.raises(RuntimeError, match="no scheduling link"):
        run(portal.submit_inspection_action_async(make_action(), portal_type="Electrical Final"))


def test_submit_refuses_without_addressable_record():
    portal = AccelaInspectionPortal(dispatcher_over(FakeClient(url="https://aca-test.accela.com/nullisland/Cap/Home.aspx")))
    with pytest.raises(RuntimeError, match="deep link unavailable"):
        run(portal.submit_inspection_action_async(make_action(), portal_type="Electrical Final"))


def test_submit_refuses_on_wrong_record():
    portal = AccelaInspectionPortal(dispatcher_over(FakeClient(wrong_record=True)))
    with pytest.raises(RuntimeError, match="different record"):
        run(portal.submit_inspection_action_async(make_action(), portal_type="Electrical Final"))


def test_submit_type_not_in_grid_raises():
    portal = AccelaInspectionPortal(dispatcher_over(FakeClient(types=("Rough",))))
    with pytest.raises(RuntimeError, match="not offered by the wizard's grid"):
        run(portal.submit_inspection_action_async(make_action(), portal_type="Electrical Final", selected_date="2026-09-24"))


def test_submit_day_inactive_raises_without_clicking():
    client = FakeClient(calendar_active=(22, 23))
    portal = AccelaInspectionPortal(dispatcher_over(client))
    with pytest.raises(RuntimeError, match="renders inactive"):
        run(portal.submit_inspection_action_async(make_action(), portal_type="Electrical Final", selected_date="2026-09-24"))
    assert not any('text="24"' in (item["target"] or "") for item in client.clicks)


def test_submit_date_outside_rendered_months_raises():
    portal = AccelaInspectionPortal(dispatcher_over(FakeClient()))
    with pytest.raises(RuntimeError, match="outside the rendered calendar"):
        run(portal.submit_inspection_action_async(make_action(), portal_type="Electrical Final", selected_date="2026-12-01"))


def test_submit_no_times_after_day_click_raises():
    portal = AccelaInspectionPortal(dispatcher_over(FakeClient(times="")))
    with pytest.raises(RuntimeError, match="no selectable times"):
        run(portal.submit_inspection_action_async(make_action(), portal_type="Electrical Final", selected_date="2026-09-24"))


def test_submit_no_confirmation_and_no_row_is_uncertain():
    portal = AccelaInspectionPortal(dispatcher_over(FakeClient(result_text="Processing complete.")))
    with pytest.raises(RuntimeError, match="no confirmation number"):
        run(portal.submit_inspection_action_async(make_action(), portal_type="Electrical Final", selected_date="2026-09-24"))


# --- submit path: full wizard walk ------------------------------------------------


def test_submit_wizard_walk_carries_acknowledging_intents():
    client = FakeClient()
    portal = AccelaInspectionPortal(dispatcher_over(client))
    confirmation = run(
        portal.submit_inspection_action_async(make_action(), portal_type="Electrical Final", selected_date="2026-09-24")
    )
    assert confirmation == "CNF-77K9"
    # every click went through the dispatcher transcript
    assert client.clicks, "adapter must click through the dispatcher, not around it"
    joined = " | ".join(item["target"] or "" for item in client.clicks)
    assert "lnkInspectionSchedule" in joined
    assert "rdInspectionType" in joined  # type chosen by control id
    assert 'text="24"' in joined  # the requested day
    assert "8:00AM - 10:00AM" in joined  # first listed time slot
    # The first two Continue controls only advance wizard pages; the distinct
    # confirmation-step Continue acknowledges the scheduling commit.
    continue_steps = [
        step
        for step in portal.steps
        if step["call"]["name"] == "click" and step["call"]["args"].get("target") == "Continue"
    ]
    assert continue_steps, "the wizard walk must click Continue"
    assert [step["call"]["args"].get("intent") for step in continue_steps] == [
        "navigate", "navigate", "schedule_inspection",
    ]


# --- full-stack: adapter through the executor -------------------------------------


def test_executor_with_adapter_schedules_and_verifies():
    client = FakeClient(scheduled_row=True)
    portal = AccelaInspectionPortal(dispatcher_over(client))
    executor = InspectionActionExecutor(portal)
    result = executor.execute(
        make_action(date_window_start="2026-09-24", date_window_end="2026-09-24"),
        eligible_types=["Rough", "Electrical Final"],
        available_dates=["2026-09-24"],
    )
    assert result.success, result.error
    assert result.verification_state is ActionVerificationState.VERIFIED_SUCCESS
    assert result.scheduled_date == "2026-09-24"
    assert result.confirmation_number == "CNF-77K9"
    audits = executor.audits
    assert audits and audits[0].previous_state.status == "Not Scheduled"
    assert audits[0].verified_final_state.is_scheduled
    # the audit's verified final state came from the portal's own re-read
    assert audits[0].verified_final_state.scheduled_date == "2026-09-24"


def test_executor_with_adapter_stops_on_unverified_submission():
    # The portal prints no confirmation and the re-read still shows nothing
    # scheduled: UNVERIFIED, never success.
    client = FakeClient(result_text="Processing complete.")
    portal = AccelaInspectionPortal(dispatcher_over(client))
    executor = InspectionActionExecutor(portal)
    result = executor.execute(
        make_action(),
        eligible_types=["Electrical Final"],
        available_dates=["2026-09-24"],
    )
    assert not result.success
    assert result.verification_state is ActionVerificationState.UNVERIFIED
    assert result.error_code is ActionErrorCode.UNCERTAIN_SUBMISSION


def test_executor_with_adapter_wrong_permit_fails_closed():
    client = FakeClient(wrong_record=True)
    portal = AccelaInspectionPortal(dispatcher_over(client))
    executor = InspectionActionExecutor(portal)
    result = executor.execute(make_action(), eligible_types=["Electrical Final"])
    assert not result.success
    assert result.error_code is ActionErrorCode.STATE_MISMATCH
    assert not client.clicks, "no browser step may run against the wrong record"


def test_sync_bridge_rejects_running_loop():
    async def inside():
        portal = AccelaInspectionPortal(dispatcher_over(FakeClient()))
        with pytest.raises(RuntimeError, match="running event loop"):
            portal.read_inspection_state("BLD26-00469")

    asyncio.run(inside())
