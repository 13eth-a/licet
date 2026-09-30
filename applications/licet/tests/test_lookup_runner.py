"""lookuprunner tests: the browser execution bridge, driven by fakes"""

from __future__ import annotations

import asyncio

import pytest

from licet.agent.state import AgentState
from licet.browser import accela
from licet.lookup import (
    LookupErrorCode,
    LookupMetrics,
    LookupStatus,
    PermitLookupRequest,
    SearchResult,
)
from licet.lookup_runner import LookupRunner

from tests.conftest import (
    DETAIL_URL,
    FakeClient,
    SEARCH_URL,
    apo_fields,
    detail_page,
    gs_field,
    mode_dropdown,
    run,
    search_form,
    search_results,
    runner_for,
    results_page,
)


def row_html(number: str, record_type: str, address: str, parcel: str | None = None) -> str:
    """one result-grid row whose columns match the shared ``row_html`` template (date, record number, record type, project name, address, status, applicant, parcel)"""
    parcel_cell = f"<td>{parcel}</td>" if parcel else "<td></td>"
    return (
        f"<tr><td>09/20/2026</td><td><a href='/detail/{number}'>{number}</a></td>"
        f"<td>{record_type}</td><td></td><td>{address}</td><td>In Review</td>"
        f"<td>Eval User</td>{parcel_cell}</tr>"
    )


def row(number: str, record_type: str, address: str, **kwargs):
    return SearchResult(
        record_number=number,
        record_type=record_type,
        address=address,
        **kwargs,
    )


def detail_read(number: str = "000000014") -> dict:
    return detail_page(number=number, address="77 Licet Eval Way")


def row(number: str, record_type: str, address: str, **kwargs):
    return SearchResult(
        record_number=number,
        record_type=record_type,
        address=address,
        **kwargs,
    )


def detail_read(number: str = "000000014") -> dict:
    return detail_page(number=number, address="77 Licet Eval Way")


def test_record_number_lookup_finds_opens_and_verifies():
    client = FakeClient(
        [
            search_form(fields=[gs_field("txtGSPermitNumber")]),
            search_results(row_html("000000014", "Commercial Alteration", "77 Licet Eval Way")),
            detail_read("000000014"),
        ]
    )
    runner, result, state = run(PermitLookupRequest(record_number="000000014"), client)
    assert result.status is LookupStatus.FOUND
    assert result.selected.record_number == "000000014"
    assert result.confidence == 1.0
    assert runner.identity_verified is True
    assert runner.opened is not None and runner.opened.permit_type == "Commercial Alteration"
    assert state.current_permit == "000000014"
    assert any(name == "type" for name, _ in client.calls)
    assert ("click", {"target": "text=Search"}) in client.calls
    assert ("click", {"target": "text=000000014"}) in client.calls


def test_address_lookup_resolves_mode_label_and_uses_apo_fields():
    client = FakeClient(
        [
            search_form(
                fields=mode_dropdown(
                    ["Permit Number", "Search by Address", "Search by Parcel"]
                )
            ),
            search_form(fields=apo_fields()),
            search_results(row_html("000000014", "Commercial Alteration", "77 Licet Eval Way")),
            detail_read("000000014"),
        ]
    )
    request = PermitLookupRequest(
        street_number="77", street_name="licet eval way", permit_type="Commercial Alteration"
    )
    runner, result, _state = run(request, client)
    assert result.status is LookupStatus.FOUND
    assert runner.identity_verified is True
    selects = [args for name, args in client.calls if name == "select"]
    assert selects == [
        {"target": f"selector=#{accela.SEARCH_MODE_DROPDOWN}", "value": "Search by Address"}
    ]
    typed = [args for name, args in client.calls if name == "type"]
    targets = [call["target"] for call in typed]
    assert any("txtAPO_Search_by_Address_StreetNumber_ChildControl0" in target for target in targets)
    assert any("txtAPO_Search_by_Address_StreetName" in target for target in targets)
    assert len(selects) == 1


def test_exact_address_resolves_uniquely_despite_multiple_same_street_rows():
    """the phase-2 flagship shape: several rows on the street, one matches the requested address + type; ranking must separate them above threshold"""
    client = FakeClient(
        [
            search_form(
                fields=mode_dropdown(
                    ["Permit Number", "Search by Address", "Search by Parcel"]
                )
            ),
            search_form(fields=apo_fields()),
            search_results(
                row_html("BLD26-00001", "Residential Addition", "77 Licet Eval Way"),
                row_html("000000014", "Commercial Alteration", "77 Licet Eval Way"),
                row_html("ELE26-00002", "Commercial Electrical", "77 Licet Eval Way"),
            ),
            detail_read("000000014"),
        ]
    )
    request = PermitLookupRequest(
        street_number="77", street_name="licet eval way", permit_type="Commercial Alteration"
    )
    runner, result, _state = run(request, client)
    assert result.status is LookupStatus.FOUND
    assert result.selected.record_number == "000000014"
    assert "permit type" in result.selected.match_reasons
    assert runner.opened is not None


def test_ambiguous_rows_return_ambiguity_and_open_nothing():
    client = FakeClient(
        [
            search_form(fields=[], options=["Permit Number", "Search by Address"]),
            search_form(fields=apo_fields()),
            search_results(
                row_html("BLD26-00001", "Residential Addition", "77 Licet Eval Way"),
                row_html("000000014", "Commercial Alteration", "77 Licet Eval Way"),
                row_html("ELE26-00002", "Commercial Electrical", "77 Licet Eval Way"),
            ),
        ]
    )
    runner, result, state = run(PermitLookupRequest(street_number="77", street_name="licet eval way"), client)
    assert result.status is LookupStatus.AMBIGUOUS
    assert len(result.matches) == 3
    assert runner.opened is None and state.current_permit is None
    assert not any(
        name == "click"
        and args.get("target", "") in {"text=BLD26-00001", "text=000000014", "text=ELE26-00002"}
        for name, args in client.calls
    )


def test_pagination_scans_until_footer_stops_changing_and_dedups_by_union():
    page_one_html = results_page(
        row_html("BLD26-00001", "Residential Addition", "77 Licet Eval Way"),
        footer="Showing 1-1 of 2 Next",
        total=2,
        last=1,
    )
    page_two_html = results_page(
        row_html("ELE26-00002", "Commercial Electrical", "78 Licet Eval Way"),
        footer="Showing 2-2 of 2",
        total=2,
        last=2,
    )
    client = FakeClient(
        [
            search_form(fields=[], options=["Permit Number", "Search by Address"]),
            search_form(fields=apo_fields()),
            {
                "url": SEARCH_URL,
                "text": page_one_html,
                "fields": [],
                "flow": None,
            },
            {
                "url": SEARCH_URL,
                "text": page_two_html,
                "fields": [],
                "flow": None,
            },
        ],
        html=page_one_html,
    )
    original_click = client.click

    async def click(target):
        result = await original_click(target)
        if "Next" in str(target.describe()):
            client.html = page_two_html
        return result

    client.click = click  # type: ignore[method-assign]
    runner, result, _state = run(PermitLookupRequest(street_number="77", street_name="licet eval way"), client)
    assert result.status is LookupStatus.FAILED
    assert result.error_code is LookupErrorCode.RECORD_OPEN_FAILED
    assert runner.trace.pages_scanned == 2
    assert any("Next" in args.get("target", "") for name, args in client.calls if name == "click")


def test_stuck_pagination_click_stops_without_duplicate_rows():
    page_html = results_page(
        row_html("BLD26-00001", "Residential Addition", "77 Licet Eval Way"),
        footer="Showing 1-1 of 2 Next",
        total=2,
        last=1,
    )
    client = FakeClient(
        [
            search_form(fields=[], options=["Permit Number", "Search by Address"]),
            search_form(fields=apo_fields()),
            {
                "url": SEARCH_URL,
                "text": page_html,
                "fields": [],
                "flow": None,
            },
            {
                "url": SEARCH_URL,
                "text": page_html,
                "fields": [],
                "flow": None,
            },
        ],
        html=page_html,
    )
    runner, result, _state = run(PermitLookupRequest(street_number="77", street_name="licet eval way"), client)
    # the scan stops after one bounded click; no duplicate row inflates ranking
    assert [match.record_number for match in result.matches].count("BLD26-00001") == 1
    next_clicks = [args for name, args in client.calls if name == "click" and "Next" in args.get("target", "")]
    assert len(next_clicks) == 1


def test_zero_results_are_widened_once_then_reported_not_found():
    client = FakeClient(
        [
            search_form(fields=[gs_field("txtGSPermitNumber")]),
            search_form(
                fields=[gs_field("txtGSPermitNumber")],
                text="No records found.",
            ),
            search_form(
                fields=[gs_field("txtGSPermitNumber")],
                text="No records found.",
            ),
        ]
    )
    runner, result, _state = run(PermitLookupRequest(record_number="BLD-9999"), client)
    assert result.status is LookupStatus.NOT_FOUND
    assert result.error_code is LookupErrorCode.RECORD_NOT_FOUND
    searches = [args for name, args in client.calls if name == "click" and args.get("target") == "text=Search"]
    assert len(searches) == 2
    widened = any(
        accela.SEARCH_DATE_START_SUFFIX in args.get("target", "")
        for name, args in client.calls
        if name == "type"
    )
    assert widened is True
    assert runner.trace.attempts[0].widen_dates is False
    assert runner.trace.attempts[1].widen_dates is True


def test_parse_failure_is_never_reported_as_zero_results():
    client = FakeClient(
        [
            search_form(fields=[gs_field("txtGSPermitNumber")]),
            search_results(),
            search_results(),
        ]
    )
    client.reads[1]["text"] = "An unexpected error occurred while processing your request."
    client.reads[2]["text"] = "An unexpected error occurred while processing your request."
    runner, result, _state = run(PermitLookupRequest(record_number="BLD-9999"), client)
    assert result.error_code is LookupErrorCode.SEARCH_RESULTS_PARSE_FAILED
    assert "no records" not in (result.message or "").lower()


def test_form_failure_is_distinct_from_not_found():
    client = FakeClient(
        [search_form(fields=[], include_date=False)]
    )
    runner, result, _state = run(PermitLookupRequest(record_number="BLD-1"), client)
    assert result.error_code is LookupErrorCode.SEARCH_FORM_FAILED


def test_missing_search_mode_falls_back_without_guessing():
    """the agency exposes no parcel mode: the runner reports form failure rather than typing into a form that was never switched"""
    client = FakeClient(
        [search_form(fields=[], options=["Permit Number", "Search by Address"])]
    )
    runner, result, _state = run(PermitLookupRequest(parcel_number="421833"), client)
    assert result.error_code is LookupErrorCode.SEARCH_FORM_FAILED
    assert not any(name == "type" for name, _ in client.calls)


def test_wrong_record_opened_is_mismatch_not_success():
    client = FakeClient(
        [
            search_form(fields=[gs_field("txtGSPermitNumber")]),
            search_results(row_html("BLD26-00001", "Residential Addition", "77 Licet Eval Way")),
            detail_page(
                number="ELE26-00002",
                record_type="Commercial Electrical",
                url=DETAIL_URL,
            ),
        ]
    )
    runner, result, state = run(PermitLookupRequest(record_number="BLD26-00001"), client)
    assert result.status is LookupStatus.FAILED
    assert result.error_code is LookupErrorCode.RECORD_MISMATCH
    assert runner.identity_verified is False
    assert runner.opened is None
    assert "mismatch" in (runner.open_error or "")
    assert state.current_permit is None


def test_trace_tells_the_whole_story():
    client = FakeClient(
        [
            search_form(fields=[gs_field("txtGSPermitNumber")]),
            search_results(row_html("000000014", "Commercial Alteration", "77 Licet Eval Way")),
            detail_read("000000014"),
        ]
    )
    runner, result, _state = run(PermitLookupRequest(record_number="000000014"), client)
    lines = runner.trace.report().splitlines()
    assert lines[0] == "GOAL" and lines[1] == "test goal"
    assert any("000000014 score=1.00" in line for line in lines)
    assert "FOUND" in " ".join(lines)


def test_max_attempts_bounds_the_plan():
    from licet.lookup import build_search_plan

    request = PermitLookupRequest(street_number="77", street_name="licet eval way", zip_code="00001")
    assert len(build_search_plan(request, max_attempts=2)) == 2


def test_missing_record_page_leaves_opened_unset():
    client = FakeClient(
        [
            search_form(fields=[gs_field("txtGSPermitNumber")]),
            search_results(row_html("BLD26-00001", "Residential Addition", "77 Licet Eval Way")),
            # click succeeded, but the url never left the results page
            search_results(row_html("BLD26-00001", "Residential Addition", "77 Licet Eval Way")),
        ]
    )
    runner, result, _state = run(PermitLookupRequest(record_number="BLD26-00001"), client)
    assert runner.opened is None
    assert result.status is LookupStatus.FAILED
    assert result.error_code is LookupErrorCode.IDENTITY_UNVERIFIED


def test_metrics_count_retries_and_browser_actions_truthfully():
    shared = LookupMetrics()
    client = FakeClient(
        [
            search_form(fields=[gs_field("txtGSPermitNumber")]),
            search_form(fields=[gs_field("txtGSPermitNumber")], text="No records found."),
            search_form(fields=[gs_field("txtGSPermitNumber")], text="No records found."),
            search_form(fields=[gs_field("txtGSPermitNumber")], text="No records found."),
        ]
    )
    client.reads[1]["text"] = "No records found."
    client.reads[2]["text"] = "No records found."
    runner = runner_for(client, metrics=shared)
    state = AgentState(goal="test goal")
    result = asyncio.run(runner.run("test goal", PermitLookupRequest(record_number="BLD-9999"), state))
    assert result.error_code is LookupErrorCode.RECORD_NOT_FOUND
    assert shared.attempts == 1
    assert shared.retries == 1
    assert shared.browser_actions == runner.actions_taken
    assert shared.browser_actions > 0
    assert shared.successful == 0
    assert shared.wrong_record_rate == 0.0


def test_metrics_record_a_wrong_record_as_a_mismatch():
    shared = LookupMetrics()
    client = FakeClient(
        [
            search_form(fields=[gs_field("txtGSPermitNumber")]),
            search_results(row_html("BLD26-00001", "Residential Addition", "77 Licet Eval Way")),
            detail_page(number="ELE26-00002", record_type="Commercial Electrical", url=DETAIL_URL),
        ]
    )
    runner = runner_for(client, metrics=shared)
    state = AgentState(goal="test goal")
    result = asyncio.run(runner.run("test goal", PermitLookupRequest(record_number="BLD26-00001"), state))
    assert runner.identity_verified is False
    assert shared.wrong_records == 1
    assert shared.successful == 0
    assert shared.wrong_record_rate == 1.0


def test_same_query_ten_times_returns_the_same_record_each_run():
    shared = LookupMetrics()
    reads = [
        search_form(fields=[gs_field("txtGSPermitNumber")]),
        search_results(row_html("000000014", "Commercial Alteration", "77 Licet Eval Way")),
        detail_read("000000014"),
    ]
    client = FakeClient([])
    state = AgentState(goal="repeat")
    runner = runner_for(client, metrics=shared)
    request = PermitLookupRequest(record_number="000000014")
    selected: list[str] = []
    for _ in range(10):
        client.reads = list(reads)
        client.read_index = 0
        navigations_before = sum(1 for name, _ in client.calls if name == "navigate")
        result = asyncio.run(runner.run("repeat", request, state))
        assert result.status is LookupStatus.FOUND
        assert result.selected is not None
        selected.append(result.selected.record_number)
        assert runner.identity_verified is True
        navigations_after = sum(1 for name, _ in client.calls if name == "navigate")
        assert navigations_after == navigations_before + 1
    assert selected == ["000000014"] * 10
    assert state.current_permit == "000000014"
    assert shared.attempts == 10
    assert shared.successful == 10
    assert shared.exact_matches == 10
    assert shared.wrong_records == 0
    assert shared.wrong_record_rate == 0.0


def test_second_run_does_not_reuse_the_first_runs_search_results():
    """the portal re-renders search results inside one url, so a cached grid would let run 2 report run 1's record"""
    shared = LookupMetrics()
    client = FakeClient([])
    state = AgentState(goal="stale")
    runner = runner_for(client, metrics=shared)
    request = PermitLookupRequest(record_number="BLD26-00001")

    client.reads = [
        search_form(fields=[gs_field("txtGSPermitNumber")]),
        search_results(row_html("BLD26-00001", "Commercial Alteration", "77 Licet Eval Way")),
        detail_read("BLD26-00001"),
    ]
    client.read_index = 0
    first = asyncio.run(runner.run("stale", request, state))
    assert first.status is LookupStatus.FOUND

    client.reads = [
        search_form(fields=[gs_field("txtGSPermitNumber")]),
        search_results(row_html("BLD26-00002", "Residential Addition", "78 Licet Eval Way")),
    ]
    client.read_index = 0
    second = asyncio.run(runner.run("stale", request, state))
    assert second.status is LookupStatus.AMBIGUOUS
    assert runner.opened is None
    assert shared.ambiguous == 1
    assert shared.wrong_records == 0
