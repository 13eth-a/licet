from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from licet.agent.state import AgentState
from licet.browser import accela
from licet.browser.dispatcher import ToolDispatcher
from licet.browser.solari_client import ToolResult
from licet.phase4.accela_portal import PortalObservation
from licet.phase4.dates import DateConstraints
from scripts import ni_citizen_capacity_query as query


MY_RECORDS_URL = accela.MY_RECORDS_URL


def record_row(number: str, cap_id: str, record_type: str = "Commercial Electrical") -> str:
    return (
        f'<tr><td>09/20/2026</td><td><a href="/NULLISLAND/Cap/CapDetail.aspx?'
        f'Module=Building&amp;TabName=Building&amp;capID1=REC26&amp;capID2=00000&amp;'
        f'capID3={cap_id}&amp;agencyCode=NULLISLAND&amp;IsToShowInspection=">{number}</a></td>'
        f'<td>{record_type}</td><td></td><td>123 Commerce Ave, Null Island 00001</td>'
        '<td>Submitted</td><td>Eval User</td><td></td></tr>'
    )


def page_html(*rows: str) -> str:
    return (
        '<table><tr><th>Date</th><th>Record Number</th><th>Record Type</th>'
        '<th>Project Name</th><th>Address</th><th>Status</th><th>Description</th>'
        '<th>Expiration Date</th></tr>' + ''.join(rows) + '</table>'
    )


def test_parse_owned_page_requires_declared_range_and_matching_row_count():
    source = page_html(record_row("BLD26-00469", "00014"))
    parsed = query.parse_owned_page("Showing 1-1 of 1", [source])
    assert parsed.complete_page
    assert parsed.total == 1
    assert parsed.records[0]["Record Number"] == "BLD26-00469"
    assert parsed.records[0]["capids"] == {"capID1": "REC26", "capID2": "00000", "capID3": "00014"}
    assert query.record_reference({
        "Record Type": "Building/Commercial/Alteration/NA",
        "hrefs": ["/NULLISLAND/Cap/CapDetail.aspx?Module=Building&TabName=Building&capID1=REC26&capID2=00000&capID3=00014&agencyCode=NULLISLAND"],
    }) == {"capID1": "REC26", "capID2": "00000", "capID3": "00014",
          "module": "Building", "agency_code": "NULLISLAND"}
    assert query.record_reference({
        "Record Type": "Commercial Electrical",
        "hrefs": ["/NULLISLAND/Cap/CapDetail.aspx?Module=Building&TabName=Building&capID1=REC26&capID2=00000&capID3=00014&agencyCode=NULLISLAND"],
    }) == {"capID1": "REC26", "capID2": "00000", "capID3": "00014",
          "module": "Building", "agency_code": "NULLISLAND"}
    assert query.record_reference({
        "Record Type": "Building/Commercial/Alteration/NA",
        "hrefs": ["/NULLISLAND/Cap/CapDetail.aspx?Module=Building&TabName=Building&capID1=REC26&capID2=00000&capID3=00014&agencyCode=NULLISLAND"],
    }) == parsed.records[0]["capids"] | {"module": "Building", "agency_code": "NULLISLAND"}

    incomplete = query.parse_owned_page("Showing 1-2 of 2", [source])
    assert not incomplete.complete_page
    assert incomplete.error == "record_grid_rows_do_not_match_declared_range"


def test_grid_pager_row_is_not_counted_as_a_record():
    """live 2026-09-30: aca's pager rides in the same <table> as the rows"""
    rows = "".join(record_row(f"BLD26-004{i:02d}", f"000{i}")
                   for i in range(60, 70))
    pager = (
        '<tr class="ACA_Table_Pages ACA_Table_Pages_FontSize" align="center">'
        '<td colspan="12"><table class="aca_pagination"><tbody><tr>'
        '<td class="ACA_Hide"><a href="javascript:__doPostBack(\'x\',\'\')"></a></td>'
        '<td class="aca_pagination_PrevNext"><span>&lt; Prev</span></td>'
        '<td class="aca_pagination_td"><span>1</span></td>'
        '<td class="aca_pagination_td"><a href="javascript:__doPostBack(\'y\',\'\')">2</a></td>'
        '<td class="aca_pagination_PrevNext">'
        '<a href="javascript:__doPostBack(\'z\',\'\')">Next &gt;</a></td>'
        '</tr></tbody></table></td></tr>'
    )
    source = page_html(rows + pager)

    assert len(query.parse_grid(source)) == 10

    parsed = query.parse_owned_page("Showing 1-10 of 14 Next >", [source])
    assert parsed.complete_page, parsed.error
    assert [r["Record Number"] for r in parsed.records] == [
        f"BLD26-004{i:02d}" for i in range(60, 70)]


def test_record_reference_rejects_non_test_host_wrong_agency_and_type_module():
    wrong_host = {
        "hrefs": ["https://aca.accela.com/NULLISLAND/Cap/CapDetail.aspx?"
                  "capID1=REC26&capID2=00000&capID3=00014&module=Building&agencyCode=NULLISLAND"]
    }
    wrong_agency = {
        "hrefs": ["/OTHER/Cap/CapDetail.aspx?"
                  "capID1=REC26&capID2=00000&capID3=00014&module=Building&agencyCode=OTHER"]
    }
    type_module_mismatch = {
        "Record Type": "Building/Commercial/Electrical/NA",
        "hrefs": ["/NULLISLAND/Cap/CapDetail.aspx?"
                  "capID1=REC26&capID2=00000&capID3=00014&module=Planning&agencyCode=NULLISLAND"],
    }
    assert query.record_reference(wrong_host) is None
    assert query.record_reference(wrong_agency) is None
    assert query.record_reference(type_module_mismatch) is None


def test_record_type_mapping_fails_closed_for_unknown_paths():
    assert query.record_type_display_label("Building/Commercial/Alteration/NA") == "Commercial Alteration"
    assert query.record_type_display_label("Commercial Electrical") == "Commercial Electrical"
    assert query.record_type_display_label("Commercial Electrical extra") is None
    assert query.record_type_display_label("Building/Residential/New/SFR") == "New Single Family Residence"
    assert query.record_type_display_label("Building/Solar/NA/NA") == "Solar Permit"
    assert query.record_type_display_label("Building/Right of Way/NA/NA") == "Right of Way Use Permit"
    assert query.record_type_display_label("Building/Commercial/Demolition/NA") == "Commercial Demolition"
    assert query.record_type_display_label("Building/Residential/Mechanical/NA") == "Residential Mechanical"
    assert query.record_type_display_label("Building/Fire/Inspection/NA") is None
    assert query.record_type_display_label(
        "Building/Inspection/Blitzz Inspection/Remote Video Inspection") is None
    assert query._record_type_matches("Building/Commercial/Alteration/NA", "Commercial Alteration") is True
    assert query._record_type_matches("Commercial Electrical", "Commercial Electrical") is True
    assert query._record_type_matches("Building/Commercial/Alteration/NA", "Fire Inspection") is False
    assert query._record_type_matches("Building/Fire/Inspection/NA", "Fire Inspection") is None


def test_empty_owned_account_page_is_complete_only_with_explicit_empty_notice():
    parsed = query.parse_owned_page("My Records\nNo records found\nShowing 0-0 of 0", [])
    assert parsed.complete_page
    assert parsed.records == ()
    ambiguous = query.parse_owned_page("My Records\nShowing 0-0 of 0", [])
    assert query.parse_owned_page("My Records\nNo records found\nShowing 0-0 of 0", []).complete_page
    assert not ambiguous.complete_page
    assert ambiguous.error == "zero_record_range_without_explicit_empty_notice"


class GridFrame:
    def __init__(self, client):
        self.client = client

    async def content(self):
        return self.client.pages[self.client.page_index]


class OwnedRecordsClient:
    def __init__(self, pages, texts):
        self.pages = pages
        self.texts = texts
        self.page_index = 0
        self.page = SimpleNamespace(url="https://aca-test.accela.com/nullisland/Cap/MyRecordsCap.aspx",
                                    frames=[])
        self.page.frames = [GridFrame(self)]
        self.calls = []

    async def navigate(self, url):
        self.calls.append(("navigate", url))
        self.page.url = url
        self.page_index = 0
        return ToolResult(ok=True, url=url)

    async def click(self, target):
        self.calls.append(("click", target.describe()))
        if target.text == "Next":
            self.page_index += 1
        return ToolResult(ok=True, url=self.page.url)

    async def read_page(self, *, include=None, max_text=4000):
        self.calls.append(("read_page", tuple(include or ())))
        text = self.texts[self.page_index]
        return ToolResult(ok=True, url=self.page.url, data={"url": self.page.url, "text": text,
                                                           "fields": [], "loading": []})

    async def wait_for_text(self, **kwargs):
        return ToolResult(ok=True, url=self.page.url)

    async def screenshot(self, **kwargs):
        return ToolResult(ok=True, url=self.page.url)


def test_run_query_paginates_owned_records_then_uses_only_them(monkeypatch):
    pages = [page_html(record_row("BLD26-00469", "00014")),
             page_html(record_row("BLD26-00470", "00015", "Solar Permit"))]
    texts = ["My Records\nShowing 1-1 of 2 Next", "My Records\nShowing 2-2 of 2"]
    client = OwnedRecordsClient(pages, texts)
    scanned = []

    async def inspect(dispatcher, state, row, **kwargs):
        scanned.append(row["Record Number"])
        return {"permit_id": row["Record Number"], "status": "no_active_date_observed",
                "types_checked": 1, "calendar_windows": 1}

    monkeypatch.setattr(query, "_inspect_owned_record", inspect)
    summary = asyncio.run(query.run_capacity_query(
        ToolDispatcher(client), AgentState(goal="read-only test"), max_records=2,
        max_types=2, calendar_windows=1, today=lambda: query.date(2026, 9, 1),
    ))

    assert summary.status == "no_active_date_observed"
    assert summary.records_seen == 2
    assert scanned == ["BLD26-00469", "BLD26-00470"]
    assert [name for name, _ in client.calls].count("click") == 1
    assert all(name in {"navigate", "click", "read_page"} for name, _ in client.calls)


def test_partial_record_cap_can_return_positive_but_never_false_negative(monkeypatch):
    page = page_html(record_row("BLD26-00469", "00014"), record_row("BLD26-00470", "00015"))
    client = OwnedRecordsClient([page], ["My Records\nShowing 1-2 of 2"])
    async def positive(dispatcher, state, row, **kwargs):
        return {"permit_id": row["Record Number"], "status": "active_date_found",
                "types_checked": 1, "calendar_windows": 1,
                "reason": "verified active date", "match": {"permit_id": row["Record Number"]}}
    monkeypatch.setattr(query, "_inspect_owned_record", positive)
    summary = asyncio.run(query.run_capacity_query(
        ToolDispatcher(client), AgentState(goal="read-only test"), max_records=1,
        max_types=1, calendar_windows=1,
    ))
    assert summary.status == "active_date_found"
    assert summary.match == {"permit_id": "BLD26-00469"}
    assert summary.records_seen == 1

    async def negative(dispatcher, state, row, **kwargs):
        return {"permit_id": row["Record Number"], "status": "no_active_date_observed",
                "types_checked": 1, "calendar_windows": 1}
    monkeypatch.setattr(query, "_inspect_owned_record", negative)
    summary = asyncio.run(query.run_capacity_query(
        ToolDispatcher(client), AgentState(goal="read-only test"), max_records=1,
        max_types=1, calendar_windows=1,
    ))
    assert summary.status == "unknown"
    assert "partial" in summary.reason
    assert summary.records_seen == 1
    assert len(summary.records) == 1


def test_run_query_stops_unknown_if_owned_record_pages_are_incomplete(monkeypatch):
    client = OwnedRecordsClient(
        [page_html(record_row("BLD26-00469", "00014"))],
        ["My Records\nShowing 1-2 of 2 Next"],
    )
    called = False

    async def inspect(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not inspect records from an incomplete ownership list")

    monkeypatch.setattr(query, "_inspect_owned_record", inspect)
    summary = asyncio.run(query.run_capacity_query(
        ToolDispatcher(client), AgentState(goal="read-only test"), max_records=2,
        max_types=2, calendar_windows=1,
    ))
    assert summary.status == "unknown"
    assert not called


def test_inspect_owned_record_only_checks_complete_offered_catalog_and_returns_date(monkeypatch):
    ref = {"capID1": "REC26", "capID2": "00000", "capID3": "00014",
           "module": "Building", "agency_code": "NULLISLAND"}
    key = "NULLISLAND/Building/REC26/00000/00014"
    detail_text = "Record BLD26-00469:\nCommercial Electrical\nRecord Status: Submitted\nSchedule an Inspection"
    obs = PortalObservation.from_payload({
        "url": accela.detail_url("REC26", "00000", "00014"),
        "text": detail_text,
        "fields": [], "loading": [],
    })
    client = OwnedRecordsClient([], [])
    client.page.url = accela.detail_url("REC26", "00000", "00014")
    dispatcher = ToolDispatcher(client)
    monkeypatch.setattr(query, "_assert_test_sandbox", _async_noop)
    monkeypatch.setattr(query, "read_observation", lambda *args: _async_value(obs))
    checked_types = []

    class FakePortal:
        def __init__(self, dispatcher, *, record_ref, today):
            self.record_ref = record_ref
            self.last_catalog = {}
            self.last_availability = {}

        async def _open_wizard(self):
            return obs

        async def _read(self):
            return obs

        async def inspection_catalog(self, permit_id, record_key, *, wizard):
            self.last_catalog = {"declared_count": 2, "observed_count": 2,
                                 "pages_read": 1, "failure": None}
            return ({"name": "Rough", "required": True},
                    {"name": "Electrical Final", "required": False}), True

        async def available_dates(self, inspection_type, *, max_windows, constraints=None):
            checked_types.append((inspection_type, max_windows))
            self.last_availability = {
                "record_key": key, "permit_id": "BLD26-00469",
                "inspection_type": inspection_type,
                "calendar_read": True, "identity_verified": True,
                "availability_status": "available", "windows_read": 1,
                "available_dates": ["2026-10-02"],
                "calendar_months": [{"month": "Oct 2026", "active_day_count": 1}],
            }
            return ("2026-10-02",)

    monkeypatch.setattr(query, "LiveInspectionPortal", FakePortal)
    result = asyncio.run(query._inspect_owned_record(
        dispatcher, AgentState(goal="read-only"),
        {"Record Number": "BLD26-00469", "Record Type": "Building/Commercial/Electrical/NA",
         "hrefs": ["/NULLISLAND/Cap/CapDetail.aspx?Module=Building&capID1=REC26&capID2=00000&capID3=00014&agencyCode=NULLISLAND"]},
        type_budget=4, calendar_windows=2, today=lambda: query.date(2026, 9, 1),
    ))

    assert result["status"] == "active_date_found"
    assert result["match"] == {
        "permit_id": "BLD26-00469", "record_type": "Building/Commercial/Electrical/NA",
        "record_key": key, "inspection_type": "Rough", "active_date": "2026-10-02",
        "calendar_months": [{"month": "Oct 2026", "active_day_count": 1}],
    }
    assert checked_types == [("Rough", 2)]


def test_inspect_owned_record_type_offset_slices_the_verified_catalog(monkeypatch):
    """a full 13-type sweep is chunked by skipping the first n offered types; the offset must only slice the verified catalog, never widen it"""
    key = "NULLISLAND/Building/REC26/00000/00014"
    obs = PortalObservation.from_payload({
        "url": accela.detail_url("REC26", "00000", "00014"),
        "text": "Record BLD26-00469:\nCommercial Electrical\nRecord Status: Submitted\nSchedule an Inspection",
        "fields": [], "loading": [],
    })
    client = OwnedRecordsClient([], [])
    client.page.url = accela.detail_url("REC26", "00000", "00014")
    monkeypatch.setattr(query, "_assert_test_sandbox", _async_noop)
    monkeypatch.setattr(query, "read_observation", lambda *args: _async_value(obs))
    checked = []
    catalog = ({"name": "Set Backs", "required": False},
               {"name": "Temp Power", "required": False},
               {"name": "Footings & Forms", "required": True})

    class FakePortal:
        def __init__(self, dispatcher, *, record_ref, today):
            self.record_ref = record_ref
            self.last_catalog = {}
            self.last_availability = {}

        async def _open_wizard(self):
            return obs

        async def _read(self):
            return obs

        async def inspection_catalog(self, permit_id, record_key, *, wizard):
            self.last_catalog = {"declared_count": len(catalog), "observed_count": len(catalog),
                                 "pages_read": 1, "failure": None}
            return catalog, True

        async def available_dates(self, inspection_type, *, max_windows, constraints=None):
            checked.append(inspection_type)
            self.last_availability = {
                "record_key": key, "permit_id": "BLD26-00469",
                "inspection_type": inspection_type,
                "calendar_read": True, "identity_verified": True,
                "availability_status": "none_in_observed_calendar",
                "windows_read": max_windows, "available_dates": [],
                "calendar_months": [{"month": "Sep 2026", "active_day_count": 0}],
                "search_stop": "requested_window_exhausted", "failure": None,
            }
            return ()

    monkeypatch.setattr(query, "LiveInspectionPortal", FakePortal)
    row = {"Record Number": "BLD26-00469", "Record Type": "Building/Commercial/Electrical/NA",
           "hrefs": ["/NULLISLAND/Cap/CapDetail.aspx?Module=Building&capID1=REC26&capID2=00000&capID3=00014&agencyCode=NULLISLAND"]}

    result = asyncio.run(query._inspect_owned_record(
        ToolDispatcher(client), AgentState(goal="read-only"), row,
        type_budget=1, calendar_windows=2, today=lambda: query.date(2026, 9, 30),
        type_offset=2))
    assert checked == ["Footings & Forms"]
    assert result["type_offset"] == 2
    assert result["catalog_types_remaining"] == 1
    assert result["status"] == "no_active_date_observed"

    checked.clear()
    empty = asyncio.run(query._inspect_owned_record(
        ToolDispatcher(client), AgentState(goal="read-only"), row,
        type_budget=1, calendar_windows=2, today=lambda: query.date(2026, 9, 30),
        type_offset=5))
    assert not checked
    assert empty["status"] == "unknown"
    assert "at or past the 3 offered inspection types" in empty["reason"]


def test_horizon_month_parses_to_its_last_day_and_drives_the_window_count():
    assert query.parse_horizon_month("2028-12") == query.date(2028, 12, 31)
    assert query.parse_horizon_month("2027-02") == query.date(2027, 2, 28)
    for bad in ("2028", "2028-13", "", "December 2028"):
        with pytest.raises(ValueError):
            query.parse_horizon_month(bad)
    assert query.windows_for_horizon(query.date(2027, 12, 31),
                                     today=query.date(2026, 9, 30)) == 17


def test_budgets_derive_the_window_count_from_the_horizon_and_cap_the_reach():
    args = query._budgets(["--horizon", "2028-12"])
    assert args.horizon_end == query.date(2028, 12, 31)
    assert args.calendar_windows == query.windows_for_horizon(
        query.date(2028, 12, 31), today=query.date.today())
    # an explicit cap still wins; the old 12-window ceiling is not the limit now
    assert query._budgets(
        ["--horizon", "2027-12", "--calendar-windows", "20"]).calendar_windows == 20
    with pytest.raises(SystemExit):
        query._budgets(["--horizon", "2099-01"])


def test_inspect_owned_record_scans_toward_the_horizon_month(monkeypatch):
    """the scan must be handed an end month, so \"no active date\" means the horizon was actually read rather than the window budget running out"""
    key = "NULLISLAND/Building/REC26/00000/00014"
    obs = PortalObservation.from_payload({
        "url": accela.detail_url("REC26", "00000", "00014"),
        "text": "Record BLD26-00469:\nCommercial Electrical\nRecord Status: Submitted\nSchedule an Inspection",
        "fields": [], "loading": [],
    })
    client = OwnedRecordsClient([], [])
    client.page.url = accela.detail_url("REC26", "00000", "00014")
    monkeypatch.setattr(query, "_assert_test_sandbox", _async_noop)
    monkeypatch.setattr(query, "read_observation", lambda *args: _async_value(obs))
    seen = {}

    class FakePortal:
        def __init__(self, dispatcher, *, record_ref, today):
            self.record_ref = record_ref
            self.last_catalog = {}
            self.last_availability = {}

        async def _open_wizard(self):
            return obs

        async def _read(self):
            return obs

        async def inspection_catalog(self, permit_id, record_key, *, wizard):
            self.last_catalog = {"declared_count": 1, "observed_count": 1,
                                 "pages_read": 1, "failure": None}
            return ({"name": "Rough", "required": True},), True

        async def available_dates(self, inspection_type, *, max_windows, constraints=None):
            seen["constraints"] = constraints
            seen["max_windows"] = max_windows
            self.last_availability = {
                "record_key": key, "permit_id": "BLD26-00469",
                "inspection_type": inspection_type,
                "calendar_read": True, "identity_verified": True,
                "availability_status": "none_in_observed_calendar",
                "windows_read": max_windows, "available_dates": [],
                "calendar_months": [], "search_stop": "requested_window_exhausted",
                "failure": None,
            }
            return ()

    monkeypatch.setattr(query, "LiveInspectionPortal", FakePortal)
    result = asyncio.run(query._inspect_owned_record(
        ToolDispatcher(client), AgentState(goal="read-only"),
        {"Record Number": "BLD26-00469", "Record Type": "Building/Commercial/Electrical/NA",
         "hrefs": ["/NULLISLAND/Cap/CapDetail.aspx?Module=Building&capID1=REC26&capID2=00000&capID3=00014&agencyCode=NULLISLAND"]},
        type_budget=4, calendar_windows=29, today=lambda: query.date(2026, 9, 30),
        horizon=query.date(2028, 12, 31),
    ))

    assert seen["constraints"] == DateConstraints(end=query.date(2028, 12, 31))
    assert seen["max_windows"] == 29
    assert result["status"] == "no_active_date_observed"
    assert result["calendar_windows"] == 29


def test_date_hit_requires_date_to_be_in_reported_dates_and_month():
    availability = {
        "available_dates": ["2026-10-02"],
        "calendar_months": [{"month": "Oct 2026", "active_day_count": 1,
                              "inactive_day_count": 30, "any_available": True}],
    }
    today = lambda: query.date(2026, 9, 1)
    assert query._date_is_in_calendar_evidence("2026-10-02", availability, today=today)
    assert not query._date_is_in_calendar_evidence("2026-11-02", availability, today=today)
    availability["available_dates"] = []
    assert not query._date_is_in_calendar_evidence("2026-10-02", availability, today=today)


def test_record_type_regex_accepts_hierarchical_spaces_only_in_segments():
    assert query.record_type_display_label(
        "Building/Inspection/Blitzz Inspection/Remote Video Inspection") is None
    assert query._RECORD_TYPE_RE.fullmatch(
        "Building/Inspection/Blitzz Inspection/Remote Video Inspection")


async def _async_noop(*args, **kwargs):
    return None


async def _async_value(value):
    return value


@pytest.mark.parametrize("kwargs", [
    {"max_records": 21}, {"max_types": 201},
    {"calendar_windows": query.MAX_CALENDAR_WINDOWS + 1},
    {"max_record_pages": 6},
])
def test_query_rejects_budgets_above_hard_caps(kwargs):
    client = OwnedRecordsClient([], [])
    with pytest.raises(ValueError, match="hard safety limits"):
        asyncio.run(query.run_capacity_query(
            ToolDispatcher(client), AgentState(goal="read-only test"), **kwargs,
        ))
    assert not client.calls


def draft_row_html(number: str, record_type: str) -> str:
    """an unfinished application, shaped after the live 2026-09-30 grid"""
    return (f'<tr><td>09/29/2026</td><td><strong><span>{number}</span></strong></td>'
            f'<td>{record_type}</td><td></td><td>United States</td>'
            '<td></td><td>Resume Application</td><td></td></tr>')


def draft_page_html(*rows: str) -> str:
    """same grid as `page_html` plus the action column the live grid carries"""
    return ('<table><tr><th>Date</th><th>Record Number</th><th>Record Type</th>'
            '<th>Project Name</th><th>Address</th><th>Status</th><th>Action</th>'
            '<th>Expiration Date</th></tr>' + ''.join(rows) + '</table>')


def test_incomplete_application_row_requires_every_draft_signal():
    """live 2026-09-30: 26tmp-000071/072 reported as a missing record identity"""
    draft = {"Record Number": "26TMP-000072", "Record Type": "Residential Demolition",
             "Action": "Resume Application", "hrefs": []}
    assert query.is_incomplete_application_row(draft)
    assert not query.is_incomplete_application_row({**draft, "Record Number": "BLD26-00483"})
    assert not query.is_incomplete_application_row({**draft, "hrefs": ["/NULLISLAND/Cap/CapDetail.aspx"]})
    assert not query.is_incomplete_application_row({**draft, "Action": ""})
    assert not query.is_incomplete_application_row({**draft, "Action": "View Record"})


def test_parsed_draft_row_is_recognised_without_a_detail_link():
    source = draft_page_html(draft_row_html("26TMP-000072", "Residential Demolition"))
    parsed = query.parse_owned_page("Showing 1-1 of 1", [source])
    assert parsed.complete_page
    row = parsed.records[0]
    assert row["hrefs"] == []
    assert row["capids"] is None
    assert query.is_incomplete_application_row(row)


def test_inspect_owned_record_names_draft_unmapped_and_missing_link_separately(monkeypatch):
    draft = {"Record Number": "26TMP-000072", "Record Type": "Residential Demolition",
             "Action": "Resume Application", "hrefs": []}
    result = asyncio.run(query._inspect_owned_record(
        None, AgentState(goal="read-only"), draft,
        type_budget=4, calendar_windows=1, today=lambda: query.date(2026, 9, 30)))
    assert result["status"] == "not_a_record"
    assert "incomplete draft application" in result["reason"]

    monkeypatch.setattr(query, "_assert_test_sandbox", _async_noop)
    dispatcher = ToolDispatcher(OwnedRecordsClient([], []))
    linked = ("/NULLISLAND/Cap/CapDetail.aspx?Module=Building&capID1=REC26"
              "&capID2=00000&capID3=00014&agencyCode=NULLISLAND")
    unmapped = asyncio.run(query._inspect_owned_record(
        dispatcher, AgentState(goal="read-only"),
        {"Record Number": "BLD26-00499", "Record Type": "Fire Inspection", "hrefs": [linked]},
        type_budget=4, calendar_windows=1, today=lambda: query.date(2026, 9, 30)))
    assert unmapped["reason"] == "owned_row_record_type_path_is_unmapped"
    no_link = asyncio.run(query._inspect_owned_record(
        dispatcher, AgentState(goal="read-only"),
        {"Record Number": "BLD26-00499", "Record Type": "Commercial Electrical", "hrefs": []},
        type_budget=4, calendar_windows=1, today=lambda: query.date(2026, 9, 30)))
    assert no_link["reason"] == "owned_row_missing_safe_detail_link"


def test_run_query_never_claims_a_check_when_every_owned_row_is_a_draft():
    page = draft_page_html(draft_row_html("26TMP-000072", "Residential Demolition"))
    client = OwnedRecordsClient([page], ["My Records\nShowing 1-1 of 1"])
    summary = asyncio.run(query.run_capacity_query(
        ToolDispatcher(client), AgentState(goal="read-only test"), max_records=1,
        max_types=2, calendar_windows=1, today=lambda: query.date(2026, 9, 30)))
    assert summary.status == "unknown"
    assert summary.skipped_incomplete_applications == 1
    assert "no inspectable owned record" in summary.reason


def test_run_query_skips_drafts_without_poisoning_a_clean_negative(monkeypatch):
    page = page_html(record_row("BLD26-00469", "00014"),
                     record_row("26TMP-000072", "00015"))
    client = OwnedRecordsClient([page], ["My Records\nShowing 1-2 of 2"])
    seen = []

    async def inspect(dispatcher, state, row, **kwargs):
        number = row["Record Number"]
        seen.append(number)
        if number.startswith("26TMP"):
            return {"permit_id": number, "status": "not_a_record", "types_checked": 0,
                    "calendar_windows": 0,
                    "reason": "incomplete draft application: no detail page"}
        return {"permit_id": number, "status": "no_active_date_observed",
                "identity_verified": True, "types_checked": 1, "calendar_windows": 1}

    monkeypatch.setattr(query, "_inspect_owned_record", inspect)
    summary = asyncio.run(query.run_capacity_query(
        ToolDispatcher(client), AgentState(goal="read-only test"), max_records=2,
        max_types=2, calendar_windows=1, today=lambda: query.date(2026, 9, 30)))
    assert summary.status == "no_active_date_observed"
    assert summary.skipped_incomplete_applications == 1
    assert "skipped 1 incomplete draft application" in summary.reason
    assert seen == ["BLD26-00469", "26TMP-000072"]
