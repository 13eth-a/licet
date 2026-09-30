"""regressions for the phase 2 the architecture review’s unsafe-success cases"""
import asyncio
import json

import pytest
from pydantic import ValidationError

from licet.agent.state import AgentState
from licet.lookup import (
    LookupResult, LookupStatus, LookupErrorCode, PermitLookupRequest,
    SearchResult, parse_search_results, parse_lookup_request,
)
from scripts.phase2_review_probe import main as review_probe
from tests.conftest import (
    FakeClient, runner_for, search_form, search_results, detail_page,
    gs_field, apo_fields, mode_dropdown, row, DETAIL_URL,
)


@pytest.fixture(scope="module")
def review_cases(tmp_path_factory):
    output = tmp_path_factory.mktemp("phase2") / "evidence.json"
    review_probe(output)
    return json.loads(output.read_text())["checks"]


@pytest.mark.parametrize("index", range(43))
def test_review_regression(review_cases, index):
    case = review_cases[index]
    assert case["passed"], f'{case["id"]}: expected {case["expected"]}; actual {case["actual"]}'


def test_found_requires_verified_permit():
    with pytest.raises(ValidationError):
        LookupResult(status=LookupStatus.FOUND, selected=SearchResult(record_number="BLD-1"))


def test_record_link_is_from_record_cell_and_tables_do_not_bleed():
    html = '''<table><tr><th>Action</th><th>Record #</th><th>Address</th></tr>
    <tr><td><a href="/delete">Delete</a></td><td><a href="/CapDetail.aspx?capID1=A">BLD-1</a></td><td></td></tr></table>
    <table><tr><td>Noise</td><td>BLD-2</td><td>123 Main</td></tr></table>Showing 1-1 of 1'''
    rows, meta = parse_search_results(html)
    assert [r.record_number for r in rows] == ["BLD-1"]
    assert rows[0].address is None
    assert rows[0].href_or_target.startswith("/CapDetail")
    assert meta["complete"]


def test_truncated_table_is_never_complete():
    html = '<table><tr><th>Record Number</th></tr><tr><td>BLD-1</td></tr><tr><td>BLD-2</td></tr></table>Showing 1-2 of 2'
    rows, meta = parse_search_results(html, max_results=1)
    assert len(rows) == 1
    assert meta["truncated"] and not meta["complete"]


def test_malformed_record_row_is_not_silently_skipped():
    html = '<table><tr><th>Record Number</th><th>Address</th></tr><tr><td><a href="/CapDetail.aspx">BLD-1</a></td></tr></table>Showing 1-1 of 1'
    rows, meta = parse_search_results(html)
    assert not rows and meta["parse_error"] and not meta["complete"]


@pytest.mark.parametrize("footer", ["", "Showing 1-1 of 2 Next disabled"])
def test_missing_or_incomplete_footer_cannot_select(footer):
    client = FakeClient([search_form(fields=[gs_field("txtGSPermitNumber")]),
                         search_results(row("BLD-1", "Commercial Alteration", "123 Main St"), footer=footer)])
    runner = runner_for(client)
    result = asyncio.run(runner.run("find", PermitLookupRequest(record_number="BLD-1"), AgentState(goal="find")))
    assert result.status is LookupStatus.INCOMPLETE
    assert result.selected is None
    assert not any(name == "click" and args.get("target") == "text=BLD-1" for name, args in client.calls)


def test_missing_fresh_address_never_uses_grid_address():
    client = FakeClient([search_form(fields=mode_dropdown(["General Search", "Search by Address"])),
                         search_form(fields=apo_fields()),
                         search_results(row("BLD-1", "Commercial Alteration", "123 Main St")),
                         detail_page(number="BLD-1")])
    runner = runner_for(client)
    state = AgentState(goal="find")
    result = asyncio.run(runner.run("find", PermitLookupRequest(street_number="123", street_name="Main St"), state))
    assert result.status is LookupStatus.FAILED
    assert result.error_code is LookupErrorCode.IDENTITY_UNVERIFIED
    assert not runner.identity_verified and state.active_permit is None


def test_earlier_page_record_opens_stable_url_and_verifies():
    first = search_results(row("BLD-1", "Commercial Alteration", "123 Main St"), footer="Showing 1-1 of 2 Next")
    second = search_results(row("BLD-2", "Commercial Alteration", "456 Main St"), footer="Showing 2-2 of 2")
    client = FakeClient([search_form(fields=[gs_field("txtGSPermitNumber")]), first, second,
                         detail_page(number="BLD-1")])
    runner = runner_for(client)
    result = asyncio.run(runner.run("find", PermitLookupRequest(record_number="BLD-1"), AgentState(goal="find")))
    assert result.status is LookupStatus.FOUND
    navigations = [args for name, args in client.calls if name == "navigate"]
    assert len(navigations) == 2
    assert "CapDetail.aspx" in navigations[-1]["url"]


def test_existing_uncertainty_prevents_any_new_action():
    client = FakeClient([])
    state = AgentState(goal="find")
    state.no_valid_action_reason = "action_outcome_unknown"
    result = asyncio.run(runner_for(client).run("find", PermitLookupRequest(record_number="BLD-1"), state))
    assert result.status is LookupStatus.FAILED
    assert not client.calls


def test_parser_span_offsets_refer_to_original_text():
    raw = "  Find   permit BLD26-00472 in ZIP 57104"
    request = parse_lookup_request(raw)
    assert request.raw_text == raw
    start, end = request.source_spans["record_number"][0]
    assert raw[start:end] == "BLD26-00472"
