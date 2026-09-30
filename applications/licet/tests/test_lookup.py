from __future__ import annotations

import pytest

from licet.agent.state import AgentState
from licet.lookup import (
    CurrentPermitState,
    LookupErrorCode,
    LookupMethod,
    LookupMetrics,
    LookupStatus,
    PermitLookupRequest,
    SearchResult,
    build_search_plan,
    choose_search_strategy,
    search_actions,
    classify_results_page,
    empty_result_retry,
    pagination_actions,
    should_scan_next_page,
    street_name_search_form_value,
    compact,
    lookup_not_found,
    normalize_parcel,
    normalize_request,
    normalize_street_name,
    normalize_zip,
    parse_lookup_request,
    parse_search_results,
    rank_results,
    resolve_lookup,
    verify_record_identity,
)
from licet.browser import accela
from licet.schema.permit import Permit


def test_exact_record_request_is_parsed():
    request = parse_lookup_request("Find permit BLD-2026-00123")
    assert request.record_number == "BLD-2026-00123"
    assert choose_search_strategy(request) is LookupMethod.RECORD_NUMBER


def test_numeric_record_request_is_parsed_without_confusing_it_with_zip():
    request = parse_lookup_request("Look up permit 000000014")
    assert request.record_number == "000000014"
    assert request.zip_code is None


def test_exact_address_request_is_parsed():
    request = parse_lookup_request("Find the permit at 123 Main Street")
    assert request.street_number == "123"
    assert request.street_name == "main st"
    assert choose_search_strategy(request) is LookupMethod.FULL_ADDRESS


def test_address_with_zip_and_type_is_parsed():
    request = parse_lookup_request(
        "Look up commercial alteration permits at 800 State St 57104"
    )
    assert request.street_number == "800"
    assert request.street_name == "state st"
    assert request.zip_code == "57104"
    assert request.permit_type == "Commercial Alteration"


def test_partial_address_is_supported():
    request = parse_lookup_request("Find permits on Main Street")
    assert request.street_number is None
    assert request.street_name == "main st"
    assert choose_search_strategy(request) is LookupMethod.PARTIAL_ADDRESS


def test_parcel_request_is_parsed_and_normalized():
    request = parse_lookup_request("Find the record for parcel 42-18-33")
    assert request.parcel_number == "421833"
    assert choose_search_strategy(request) is LookupMethod.PARCEL


def test_applicant_request_is_supported():
    request = parse_lookup_request("Find the permit for applicant Jane Doe")
    assert request.applicant_name == "jane doe"
    assert choose_search_strategy(request) is LookupMethod.APPLICANT


def test_malformed_request_is_rejected_cleanly():
    with pytest.raises(ValueError, match="invalid_lookup_input"):
        parse_lookup_request("What inspections should happen next?")


def test_whitespace_and_case_normalization():
    request = normalize_request(
        PermitLookupRequest(
            record_number="  bld-2026-00123  ",
            street_name="  East   Main Street ",
            zip_code="57104-1234",
            parcel_number="42 / 18-33",
        )
    )
    assert request.record_number == "BLD-2026-00123"
    assert request.street_name == "e main st"
    assert request.zip_code == "57104-1234"
    assert request.parcel_number == "421833"


def test_street_abbreviation_normalization():
    assert normalize_street_name("1200 West Main Street") == "1200 w main st"
    assert normalize_street_name("1200 W Main St.") == "1200 w main st"


def test_zip_normalization_is_bounded():
    assert normalize_zip(" 57104 1234 ") == "57104-1234"
    with pytest.raises(ValueError):
        normalize_zip("57104-123456")


def test_compact_identifier_normalization():
    assert compact("BLD-2026-00123") == "BLD202600123"
    assert normalize_parcel("42-18-33") == "421833"


def test_record_search_plan_is_narrow_and_single_attempt():
    request = PermitLookupRequest(record_number="BLD-2026-00123")
    plan = build_search_plan(request)
    assert len(plan) == 1
    assert plan[0].method is LookupMethod.RECORD_NUMBER


def test_record_search_actions_use_runtime_search_controls():
    actions = search_actions(build_search_plan(PermitLookupRequest(record_number="BLD-1"))[0])
    assert actions[0]["name"] == "type"
    assert 'txtGSPermitNumber' in actions[0]["args"]["target"]
    assert actions[-1]["args"]["target"] == "Search"


def test_address_actions_select_mode_then_fill_both_id_families():
    """ni's address mode swaps in txtapo_* controls and drops txtgs* entirely (live verified), so each field targets both families by id suffix"""
    attempt = build_search_plan(PermitLookupRequest(street_number="123", street_name="main"))[0]
    actions = search_actions(attempt)
    assert actions[0]["name"] == "select"
    assert actions[0]["args"]["value"] == "address"
    targets = [action["args"].get("target") for action in actions]
    assert 'input[id$="txtAPO_Search_by_Address_StreetNumber_ChildControl0"], input[id$="txtGSNumber_ChildControl0"]' in targets
    assert 'input[id$="txtAPO_Search_by_Address_StreetNumber_ChildControl1"], input[id$="txtGSNumber_ChildControl1"]' in targets
    assert 'input[id$="txtAPO_Search_by_Address_StreetName"], input[id$="txtGSStreetName"]' in targets


def test_numbered_street_is_typed_as_digits_only():
    actions = search_actions(
        build_search_plan(PermitLookupRequest(street_number="5", street_name="72nd Avenue"))[0]
    )
    name_action = next(a for a in actions if a["name"] == "type" and "StreetName" in a["args"]["target"])
    assert name_action["args"]["text"] == "72 ave"


def test_empty_result_retry_widens_dates():
    attempt = build_search_plan(PermitLookupRequest(street_number="123", street_name="main"))[0]
    assert not attempt.widen_dates
    retry = empty_result_retry(attempt)
    assert retry.method is attempt.method and retry.fields == attempt.fields
    retry_actions = search_actions(retry)
    assert any(
        action["name"] == "type"
        and action["args"]["target"] == f'input[id$="{accela.SEARCH_DATE_START_SUFFIX}"]'
        and action["args"]["text"] == accela.SEARCH_DATE_START_WIDENED
        for action in retry_actions
    )


def test_parcel_search_plan_precedes_address():
    request = PermitLookupRequest(parcel_number="42-18-33", street_name="Main")
    assert choose_search_strategy(request) is LookupMethod.PARCEL
    assert build_search_plan(request)[0].method is LookupMethod.PARCEL


def test_address_fallback_plan_is_bounded():
    request = PermitLookupRequest(street_number="123", street_name="main st", zip_code="57104")
    plan = build_search_plan(request)
    assert [attempt.method for attempt in plan] == [
        LookupMethod.FULL_ADDRESS,
        LookupMethod.FULL_ADDRESS,
        LookupMethod.PARTIAL_ADDRESS,
    ]
    assert len(build_search_plan(request, max_attempts=2)) == 2


def test_exact_record_match_has_high_confidence():
    request = PermitLookupRequest(record_number="BLD-2026-00123")
    result = resolve_lookup(
        request,
        [SearchResult(record_number="BLD202600123", record_type="Residential")],
    )
    assert result.status is LookupStatus.CANDIDATE
    assert result.confidence == 1.0
    assert result.selected is not None
    assert "exact record number" in result.selected.match_reasons


def test_exact_address_and_type_can_resolve_one_candidate():
    request = PermitLookupRequest(
        street_number="800", street_name="state st", permit_type="Commercial Alteration"
    )
    result = resolve_lookup(
        request,
        [
            SearchResult(record_number="BLD-1", record_type="Commercial Alteration", address="800 State Street"),
            SearchResult(record_number="ELE-2", record_type="Electrical", address="800 State St"),
        ],
    )
    assert result.status is LookupStatus.CANDIDATE
    assert result.selected.record_number == "BLD-1"


def test_multiple_equal_address_matches_are_ambiguous():
    request = PermitLookupRequest(street_number="123", street_name="main st")
    result = resolve_lookup(
        request,
        [
            SearchResult(record_number="BLD-1", address="123 Main Street"),
            SearchResult(record_number="BLD-2", address="123 Main St"),
        ],
    )
    assert result.status is LookupStatus.AMBIGUOUS
    assert result.selected is None
    assert result.error_code is LookupErrorCode.AMBIGUOUS_RECORD


def test_low_confidence_single_partial_match_is_not_selected():
    result = resolve_lookup(
        PermitLookupRequest(street_name="main st"),
        [SearchResult(record_number="BLD-1", address="999 Main Street")],
    )
    assert result.status is LookupStatus.AMBIGUOUS
    # a single candidate below the floor is low confidence, not "too many"
    assert result.error_code is LookupErrorCode.AMBIGUOUS_RECORD


def test_empty_results_are_not_found():
    result = resolve_lookup(PermitLookupRequest(record_number="BLD-9999"), [])
    assert result.status is LookupStatus.NOT_FOUND
    assert lookup_not_found(result)
    assert result.error_code is LookupErrorCode.RECORD_NOT_FOUND


def test_ranking_orders_exact_record_first():
    ranked = rank_results(
        PermitLookupRequest(record_number="BLD-1", permit_type="Commercial Alteration"),
        [
            SearchResult(record_number="BLD-2", record_type="Commercial Alteration"),
            SearchResult(record_number="BLD-1", record_type="Residential"),
        ],
    )
    assert ranked[0].record_number == "BLD-1"
    assert ranked[0].score > ranked[1].score


def test_record_identity_mismatch_is_explicit():
    ok, code, message = verify_record_identity("BLD-1", "BLD-2")
    assert not ok
    assert code is LookupErrorCode.RECORD_MISMATCH
    assert "observed BLD-2" in message


def test_record_identity_checks_address_and_type():
    ok, code, _ = verify_record_identity(
        "BLD-1", "BLD-1", expected_address="123 Main St", observed_address="999 Main St"
    )
    assert not ok and code is LookupErrorCode.RECORD_MISMATCH
    ok, code, _ = verify_record_identity(
        "BLD-1", "BLD-1", expected_type="Commercial Alteration", observed_type="Electrical"
    )
    assert not ok and code is LookupErrorCode.RECORD_MISMATCH


def test_record_identity_accepts_formatting_differences():
    ok, code, message = verify_record_identity("BLD-2026-00123", "bld202600123")
    assert ok and code is None and "verified" in message


RESULT_TABLE = """
<table id="results"><tr>
  <th>Date</th><th>Record Number</th><th>Record Type</th><th>Project Name</th>
  <th>Address</th><th>Status</th><th>Applicant</th><th>Parcel</th>
</tr>
<tr id="row-1"><td>09/20/2026</td><td><a href="/detail/1">BLD-1</a></td>
  <td>Commercial Alteration</td><td></td><td>123 Main St</td><td>Issued</td>
  <td>Jane Doe</td><td>42-18-33</td></tr>
<tr id="row-2"><td>09/20/2026</td><td><a href="/detail/2">BLD-2</a></td>
  <td>Electrical</td><td></td><td>123 Main St</td><td>Submitted</td>
  <td>Jane Doe</td><td>42-18-33</td></tr>
</table>
Showing 1-2 of 4 Next
"""


def test_result_table_parser_preserves_empty_project_name_column():
    results, metadata = parse_search_results(RESULT_TABLE)
    assert len(results) == 2
    assert results[0].record_number == "BLD-1"
    assert results[0].record_type == "Commercial Alteration"
    assert results[0].address == "123 Main St"
    assert results[0].status == "Issued"
    assert results[0].applicant == "Jane Doe"
    assert results[0].parcel_number == "42-18-33"
    assert results[0].href_or_target == "/detail/1"
    assert results[0].row_id == "row-1"
    assert metadata["has_next"] is True
    assert metadata["total"] == 4


def test_result_table_parser_detects_no_table_as_parse_failure():
    results, metadata = parse_search_results("<div>No records found</div>")
    assert results == []
    assert metadata["parse_error"] is True


def test_result_table_parser_handles_disabled_next():
    _, metadata = parse_search_results(RESULT_TABLE.replace("Next", "Next disabled"))
    assert metadata["has_next"] is False


def test_results_page_classification_distinguishes_three_verdicts():
    assert classify_results_page("No records found for your search.") == "zero_results"
    assert classify_results_page("Record Number | Record Type | Address") == "results"
    # neither: the search may not have executed at all never "no records"
    assert classify_results_page("An unexpected error occurred.") == "parse_failed"


def test_pagination_actions_are_postback_clicks():
    actions = pagination_actions()
    assert actions == [
        {"name": "click", "args": {"target": accela.PAGINATION_NEXT_TEXT, "by": "text", "intent": "search_records"}}
    ]


def test_should_scan_next_page_is_bounded_and_requires_more_pages():
    assert should_scan_next_page({"has_next": True}, pages_scanned=1, max_pages=5)
    assert not should_scan_next_page({"has_next": False}, pages_scanned=1, max_pages=5)
    assert not should_scan_next_page({"has_next": True}, pages_scanned=5, max_pages=5)
    assert not should_scan_next_page({"has_next": True}, pages_scanned=6, max_pages=5)


def test_street_name_search_form_value_strips_ordinals_only():
    assert street_name_search_form_value("72nd Avenue") == "72 ave"
    assert street_name_search_form_value("Main Street") == "main st"
    assert street_name_search_form_value("1200 West Main Street") == "1200 w main st"


def test_lookup_trace_contains_structured_stages():
    from licet.lookup import LookupTrace

    trace = LookupTrace("Find permit BLD-1", parsed=PermitLookupRequest(record_number="BLD-1"))
    trace.attempts.extend(build_search_plan(trace.parsed))
    trace.result = resolve_lookup(trace.parsed, [SearchResult(record_number="BLD-1")])
    lines = trace.lines()
    assert lines[:2] == ["GOAL", "Find permit BLD-1"]
    assert "PARSED" in lines and "RESULT" in lines


def test_lookup_metrics_track_success_rate():
    metrics = LookupMetrics(attempts=10, successful=8, retries=2)
    assert metrics.success_rate == 0.8


def test_active_permit_state_is_persisted_without_researching():
    permit = Permit(permit_id="BLD-1", address="123 Main St")
    active = CurrentPermitState(
        permit=permit,
        lookup_method=LookupMethod.RECORD_NUMBER,
        lookup_confidence=1.0,
        source_query=PermitLookupRequest(record_number="BLD-1"),
        search_results_seen=1,
    )
    state = AgentState(goal="find BLD-1")
    state.set_active_permit(active)
    assert state.current_permit == "BLD-1"
    assert state.active_permit.lookup_confidence == 1.0
    state.clear_active_permit()
    assert state.current_permit is None


def test_lookup_result_rejects_found_without_selection():
    with pytest.raises(ValueError):
        from licet.lookup import LookupResult
        LookupResult(status=LookupStatus.CANDIDATE, confidence=1.0)
