"""offline tests for the live phase 5 capability wiring's pure mapping helpers"""
from __future__ import annotations

import asyncio
import json
from datetime import date

from licet.browser import accela
from licet.browser.dispatcher import ToolDispatcher
from licet.eval.phase5_live import (
    MAX_CALENDAR_WINDOWS,
    LiveInspectionPortal,
    evidence_for_types,
    history_complete,
    history_from_permit,
    options_from_catalog,
    options_from_types,
    ref_from_record_key,
    snapshot_status,
)
from licet.phase3.state import Coverage, CoverageStatus, Inspection, PermitState
from tests.test_phase4_accela_portal import FakeClient, FakeFrame


class CatalogClient(FakeClient):
    """html backed frame fake: the production client parses radio label markers from markup"""

    async def read_page(self, *, include=None, max_text=4000):
        from licet.browser.solari_client import ToolResult
        payload = self.payload()
        for month in payload.get("calendar", []):
            month["month"] = "Sep 2026"
        self.page.frames = [CatalogFrame(self.page.url, payload)]
        return ToolResult(ok=True, url=self.page.url, data=dict(payload))


class CatalogFrame(FakeFrame):
    def __init__(self, url, payload):
        super().__init__(url)
        self.payload = payload

    async def content(self):
        import html
        fields = self.payload.get("fields") or []
        labels = "".join(
            f'<label for="{field["id"]}">{html.escape(field["label"])}</label>'
            f'<input type="radio" id="{field["id"]}" value="{field["id"]}" />'
            for field in fields
        )
        return labels

    async def inner_text(self):
        return str(self.payload.get("text") or "")

    async def title(self):
        return ""

KEY = "NULLISLAND/Building/REC26/00000/00014"
REF = {
    "capID1": "REC26", "capID2": "00000", "capID3": "00014",
    "module": "Building", "agency_code": "NULLISLAND",
}
RECORD_URL = (
    "https://aca-test.accela.com/NULLISLAND/Cap/CapDetail.aspx?Module=Building"
    "&TabName=Building&capID1=REC26&capID2=00000&capID3=00014"
    "&agencyCode=NULLISLAND&IsToShowInspection="
)


def test_record_key_parses_into_the_addressable_ref():
    assert ref_from_record_key(KEY) == REF


def test_malformed_record_key_is_refused_rather_than_guessed():
    for bad in (None, "", "NULLISLAND/Building/REC26", "NULLISLAND/Building//00000/00014",
                "a/b/c/d/e/f"):
        assert ref_from_record_key(bad) is None


def test_inspection_lifecycle_maps_to_the_snapshot_vocabulary():
    assert snapshot_status(Inspection(type="Rough", lifecycle_normalized="SCHEDULED")) == "Scheduled"
    assert snapshot_status(Inspection(type="Rough", lifecycle_normalized="COMPLETED",
                                      result_normalized="FAILED")) == "Failed"
    assert snapshot_status(Inspection(type="Rough", lifecycle_normalized="COMPLETED",
                                      result_normalized="PASSED")) == "Passed"
    assert snapshot_status(Inspection(type="Rough", lifecycle_normalized="CANCELLED")) == "Cancelled"


def test_unrecognised_status_is_preserved_so_selection_can_abstain():
    assert snapshot_status(Inspection(type="Rough", status="Not Scheduled")) == "Not Scheduled"
    assert snapshot_status(Inspection(type="Rough")) == "Unknown"


def test_options_and_evidence_bind_to_the_verified_record():
    names = ("Rough", "Electrical Final")
    options = options_from_types(names)
    assert [o.name for o in options] == list(names)
    assert all(o.eligible is True and o.prerequisites_satisfied is True for o in options)
    evidence = evidence_for_types(names, KEY)
    for option in options:
        assert option.evidence_ids == (f"portal-option:{option.name}",)
        assert evidence[option.evidence_ids[0]].record_key == KEY


def test_catalog_builder_separates_offered_and_required_evidence():
    options, evidence = options_from_catalog((
        {"name": "Brycer Inspection History", "required": True},
        {"name": "Electrical Final", "required": False},
    ), KEY)
    required, optional = options
    assert required.required is True
    assert required.evidence_ids == ("portal-option:Brycer Inspection History",)
    assert required.requirement_evidence_ids == ("portal-required:Brycer Inspection History",)
    assert optional.required is False and optional.requirement_evidence_ids == ()
    assert evidence[required.evidence_ids[0]].raw_text == "the scheduling wizard offers Brycer Inspection History"
    assert evidence[required.requirement_evidence_ids[0]].raw_text == (
        "the scheduling wizard marks Brycer Inspection History (required)"
    )
    assert all(item.record_key == KEY for item in evidence.values())


def test_live_catalog_reads_every_page_and_keeps_portal_on_first_page():
    types = tuple(f"Inspection {index:02d}" for index in range(18))
    client = CatalogClient(types=types, catalog_required=(types[-1],), url=RECORD_URL)
    portal = LiveInspectionPortal(ToolDispatcher(client), record_ref=REF, today=lambda: date(2026, 9, 20))
    catalog, complete = asyncio.run(portal.inspection_catalog("BLD26-00469", KEY))
    assert complete
    assert len(catalog) == 18
    assert catalog[-1] == {"name": types[-1], "required": True}
    assert client.type_page == 0
    click_texts = [c["text"] for c in client.clicks if c["text"]]
    assert click_texts.count("Next >") == 1
    assert click_texts.count("< Prev") == 1
    assert not any(c["text"] == "Continue" for c in client.clicks)
    assert portal.last_catalog["complete"] is True
    assert portal.last_catalog["declared_count"] == 18
    assert portal.last_catalog["observed_count"] == 18
    assert portal.last_catalog["pages_read"] == 2
    assert portal.last_catalog["required_types"] == [types[-1]]


def test_open_wizard_reuses_current_type_grid_instead_of_clicking_hidden_opener():
    client = CatalogClient(types=("Required A", "Required B"), url=RECORD_URL)
    client.state = client.STATE_TYPES
    client.catalog_required = {"Required A"}
    portal = LiveInspectionPortal(ToolDispatcher(client), record_ref=REF, today=lambda: date(2026, 9, 20))

    wizard = asyncio.run(portal._open_wizard())

    assert "Available Inspection Types" in wizard.text
    assert not any(c["selector"] == "lnkInspectionSchedule" for c in client.clicks)
    assert client.state == client.STATE_TYPES


def test_open_wizard_lands_on_the_inspection_view_before_the_opener():
    """the opener sits in a collapsed dropdown on the plain detail url"""
    client = CatalogClient(types=("Rough", "Electrical Final"), url=RECORD_URL)
    portal = LiveInspectionPortal(ToolDispatcher(client), record_ref=REF, today=lambda: date(2026, 9, 20))

    wizard = asyncio.run(portal._open_wizard())

    assert client.navigations[-1] == accela.inspection_detail_url(REF)
    assert client.navigations[-1].endswith("yes")
    assert [c["selector"] for c in client.clicks if c["selector"]] == [
        accela.SCHEDULE_LINK_CONTROL_ID]
    assert "Available Inspection Types" in wizard.text
    assert wizard.inspection_type_total == 2


def test_catalog_separates_a_wizard_that_never_opened_from_a_missing_count():
    """a missing declared count has two causes; the generic message hid which"""
    client = CatalogClient(types=("Rough",), url=RECORD_URL, no_scheduling_link=True)
    portal = LiveInspectionPortal(ToolDispatcher(client), record_ref=REF, today=lambda: date(2026, 9, 20))

    catalog, complete = asyncio.run(portal.inspection_catalog("BLD26-00469", KEY))

    assert catalog == () and complete is False
    assert portal.last_catalog["declared_count"] is None
    assert portal.last_catalog["failure"] == "wizard_not_open"
    assert not any(c["selector"] == accela.SCHEDULE_LINK_CONTROL_ID
                   for c in client.clicks)


def test_live_date_lookup_respects_caller_calendar_window_budget():
    from types import SimpleNamespace
    from licet.phase4.dates import DateConstraints
    client = CatalogClient(types=("Rough",), url=RECORD_URL)
    portal = LiveInspectionPortal(ToolDispatcher(client), record_ref=REF, today=lambda: date(2026, 9, 20))
    pages = [
        SimpleNamespace(record_key=KEY, record_header={"permit_id": "BLD26-00469"},
                        is_loading=False, calendar=[{"month": "Sep 2026", "active_days": []}]),
        SimpleNamespace(record_key=KEY, record_header={"permit_id": "BLD26-00469"},
                        is_loading=False, calendar=[{"month": "Oct 2026", "active_days": [2]}]),
    ]
    moved = []
    async def click(call):
        moved.append(call)
        return {"success": True}
    async def read(): return next(iter(pages[1:]))
    async def wait(**kwargs): pass
    portal._do, portal._read, portal._wait = click, read, wait

    dates = asyncio.run(portal._scan_calendar(
        pages[0], pages[0], "Rough", constraints=DateConstraints(), max_windows=1,
    ))

    assert dates == ()
    assert not moved
    assert portal.last_availability["windows_read"] == 1
    assert portal.last_availability["search_stop"] == "search_limit"


def test_live_date_lookup_can_select_a_required_type_on_later_page():
    types = tuple(f"Inspection {index:02d}" for index in range(18))
    client = CatalogClient(types=types, catalog_required=(types[-1],), url=RECORD_URL)
    portal = LiveInspectionPortal(ToolDispatcher(client), record_ref=REF, today=lambda: date(2026, 9, 20))
    dates = asyncio.run(portal.available_dates(types[-1]))
    assert dates
    assert client.type_page == 1
    assert any("rdinspectiontype" in (c["selector"] or "").lower() for c in client.clicks)
    assert any(c["selector"] == "lnkInspectionSchedule" for c in client.clicks)
    assert not any(c["text"] in {"Schedule an Inspection", "Schedule or Request an Inspection"}
                   for c in client.clicks)
    assert any(c["text"] == "Continue" for c in client.clicks)
    assert portal.last_availability["calendar_read"] is True
    assert portal.last_availability["availability_status"] == "available"
    assert portal.last_availability["available_date_count"] == len(dates)


def test_history_maps_observed_rows_and_reports_coverage():
    permit = PermitState(record_number="BLD26-00469", record_key=KEY, inspections=[
        Inspection(type="Rough Electrical", lifecycle_normalized="SCHEDULED", scheduled_date="2026-09-24"),
        Inspection(type="Rough Electrical", lifecycle_normalized="COMPLETED", result_normalized="FAILED",
                   inspection_id="I-1"),
    ])
    history = history_from_permit(permit, "BLD26-00469", KEY)
    assert [row.status for row in history] == ["Scheduled", "Failed"]
    assert history[0].is_scheduled and history[1].is_completed
    assert all(row.record_key == KEY for row in history)
    assert history_from_permit(None, "X", KEY) == ()


def test_history_is_only_complete_when_coverage_says_so():
    permit = PermitState(record_key=KEY)
    assert history_complete(permit) is False
    permit.coverage["inspections"] = Coverage(status=CoverageStatus.COMPLETE)
    assert history_complete(permit) is True
    permit.coverage["inspections"] = Coverage(status=CoverageStatus.EXPLICITLY_EMPTY)
    assert history_complete(permit) is True
    assert history_complete(None) is False


def test_survey_checkpoint_is_durable_jsonl_and_contains_no_raw_payload(tmp_path):
    from scripts.ni_calendar_horizon import checkpoint

    path = tmp_path / "survey.progress.jsonl"
    checkpoint(path, "inspection_selected", inspection_type="Rough")
    checkpoint(path, "calendar_loaded", inspection_type="Rough", months=["Sep 2026"])

    lines = path.read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    assert [event["event"] for event in events] == ["inspection_selected", "calendar_loaded"]
    assert events[0]["inspection_type"] == "Rough"
    assert events[1]["months"] == ["Sep 2026"]
    assert "password" not in path.read_text(encoding="utf-8").casefold()


def test_calendar_search_advances_into_december_and_preserves_evidence():
    portal, dates, clicks = _scan_windows([
        ("Sep 2026", []), ("Dec 2026", [4]),
    ])
    assert dates == ("2026-12-04",)
    assert len(clicks) == 1
    assert clicks[0].args["intent"] == "navigate"
    assert portal.last_availability["windows_read"] == 2
    assert portal.last_availability["search_stop"] == "matching_dates_found"


def test_calendar_search_rolls_year_and_honors_requested_date():
    from licet.phase4.dates import DateConstraints
    portal, dates, _ = _scan_windows([
        ("Dec 2026", [4]), ("Jan 2027", [5, 6]),
    ], constraints=DateConstraints(preferred=date(2027, 1, 6)))
    assert dates == ("2027-01-06",)


def test_calendar_search_stops_at_deadline_and_limit():
    from licet.phase4.dates import DateConstraints
    portal, dates, clicks = _scan_windows([
        ("Nov 2026", []), ("Dec 2026", [4]),
    ], constraints=DateConstraints(end=date(2026, 11, 30)))
    assert not dates and not clicks
    assert portal.last_availability["search_stop"] == "requested_window_exhausted"
    portal, dates, clicks = _scan_windows([("Sep 2026", [])], max_windows=1)
    assert not dates and not clicks
    assert portal.last_availability["search_stop"] == "search_limit"


def test_calendar_search_rejects_repeat_identity_change_and_navigation_failure():
    for options, reason in [
        ({}, "calendar_did_not_advance"),
        ({"wrong_record": True}, "calendar_record_identity_mismatch"),
        ({"click_success": False}, "calendar_navigation_failed"),
    ]:
        portal, dates, clicks = _scan_windows([("Sep 2026", []), ("Sep 2026", [])], **options)
        assert not dates and len(clicks) == 1
        assert portal.last_availability["failure"] == reason
        assert portal.last_availability["availability_status"] == "unknown"


def _scan_windows(windows, *, wrong_record=False, click_success=True, **kwargs):
    from types import SimpleNamespace
    pages = [SimpleNamespace(record_key=KEY, record_header={"permit_id": "BLD26-00469"},
                             is_loading=False, calendar=[{"month": month, "active_days": days}])
             for month, days in windows]
    if wrong_record:
        pages[-1].record_key = "other"
    portal = LiveInspectionPortal(ToolDispatcher(FakeClient(url=RECORD_URL)), record_ref=REF, today=lambda: date(2026, 9, 20))
    clicks = []
    remaining = iter(pages[1:])
    async def click(call):
        clicks.append(call)
        return {"success": click_success}
    async def read(): return next(remaining)
    async def wait(**kwargs): pass
    portal._do, portal._read, portal._wait = click, read, wait
    dates = asyncio.run(portal._scan_calendar(pages[0], pages[0], "Rough", **kwargs))
    return portal, dates, clicks


def test_execution_relocates_authorized_future_date_before_selecting(monkeypatch):
    from types import SimpleNamespace
    from licet.phase4.accela_portal import AccelaInspectionPortal
    portal = LiveInspectionPortal(ToolDispatcher(FakeClient(url=RECORD_URL)), record_ref=REF)
    first = SimpleNamespace(record_key=KEY, record_header={"permit_id": "BLD26-00469"})
    later = SimpleNamespace(record_key=KEY, record_header={"permit_id": "BLD26-00469"})
    selected = []
    async def scan(page, wizard, inspection_type, *, constraints):
        assert page is first and constraints.preferred == date(2027, 1, 6)
        return ("2027-01-06",)
    async def read(): return later
    async def select(self, day, page): selected.append((day, page))
    portal._scan_calendar, portal._read = scan, read
    monkeypatch.setattr(AccelaInspectionPortal, "_select_date", select)
    asyncio.run(portal._select_date("2027-01-06", first))
    assert selected == [("2027-01-06", later)]
    later.record_key = "wrong"
    import pytest
    with pytest.raises(RuntimeError, match="identity changed"):
        asyncio.run(portal._select_date("2027-01-06", first))
    assert len(selected) == 1


_MONTH_ABBREVIATIONS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def _month_strip(start_year: int, start_month: int, count: int):
    """(month label, active days) windows of consecutive months"""
    months = []
    year, month = start_year, start_month
    for _ in range(count):
        months.append((f"{_MONTH_ABBREVIATIONS[month - 1]} {year}", []))
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return months


def test_calendar_search_pages_past_the_old_twelve_window_ceiling_into_2027():
    """the 2026 09 30 capacity run stopped at sep nov 2026 and said nothing about 2027+"""
    portal, dates, clicks = _scan_windows(_month_strip(2026, 9, 20), max_windows=20)

    assert not dates
    assert len(clicks) == 19
    assert portal.last_availability["windows_read"] == 20
    assert portal.last_availability["search_stop"] == "search_limit"
    assert portal.last_availability["failure"] is None
    assert portal.last_availability["calendar_months"][0]["month"] == "Sep 2026"
    assert portal.last_availability["calendar_months"][-1]["month"] == "Apr 2028"
    assert MAX_CALENDAR_WINDOWS > 12


def test_calendar_search_stops_on_the_requested_horizon_month():
    """an explicit end date turns a deep scan into a definitive negative"""
    from licet.phase4.dates import DateConstraints

    portal, dates, clicks = _scan_windows(
        _month_strip(2026, 9, 40), max_windows=MAX_CALENDAR_WINDOWS,
        constraints=DateConstraints(end=date(2027, 12, 31)),
    )

    assert not dates
    assert portal.last_availability["search_stop"] == "requested_window_exhausted"
    assert portal.last_availability["failure"] is None
    assert portal.last_availability["windows_read"] == 16
    assert portal.last_availability["calendar_months"][-1]["month"] == "Dec 2027"
    assert len(clicks) == 15


def test_calendar_search_rejects_undated_months_and_past_days():
    portal, dates, clicks = _scan_windows([("", [21])])
    assert not dates and not clicks
    assert portal.last_availability["failure"] == "calendar_month_identity_missing"
    portal, dates, clicks = _scan_windows([("Sep 2026", [1, 19])], max_windows=1)
    assert not dates
    assert portal.last_availability["availability_status"] == "none_matching_constraints"
