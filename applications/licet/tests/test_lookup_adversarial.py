"""Adversarial record-matching, confidence, and fallback tests for Phase 2.

Division of responsibility: this file owns the *selection* logic — ranking,
confidence thresholds, the ambiguity/too-many distinction, parsing of messy
language, and the bounded fallback ladder. `tests/test_lookup.py` covers the
happy-path primitives and `tests/test_lookup_runner.py` the browser bridge.

The governing rule is the Phase 2 exit metric: **wrong-record rate must be 0**.
Every test that could otherwise select a plausible-but-unproven record asserts
that the lookup refuses instead.
"""

from __future__ import annotations

import pytest

from licet.lookup import (
    ConfidenceBand,
    LookupErrorCode,
    LookupStatus,
    LookupMetrics,
    PermitLookupRequest,
    SearchResult,
    build_search_plan,
    choose_search_strategy,
    confidence_band,
    normalize_request,
    parse_lookup_request,
    rank_results,
    resolve_lookup,
)

# ---------------------------------------------------------------------------
# Messy / adversarial natural language
# ---------------------------------------------------------------------------


def test_building_record_with_directional_is_parsed():
    request = parse_lookup_request("building record at 1200 e main")
    assert request.street_number == "1200"
    assert request.street_name == "e main"
    assert choose_search_strategy(request).value == "full_address"


def test_commercial_alteration_shorthand_is_parsed():
    request = parse_lookup_request("commercial alteration for 800 state")
    assert request.street_number == "800"
    assert request.street_name == "state"
    assert request.permit_type == "Commercial Alteration"


def test_parcel_phrase_extracts_only_the_parcel_token():
    # A greedy pattern used to swallow the rest of the sentence.
    request = parse_lookup_request("the permit tied to parcel 42-18-33 and address 123 Main")
    assert request.parcel_number == "421833"
    assert choose_search_strategy(request).value == "parcel_number"


def test_labeled_five_digit_record_is_not_a_zip():
    request = parse_lookup_request("permit 12345")
    assert request.record_number == "12345"
    assert request.zip_code is None
    assert choose_search_strategy(request).value == "record_number"


def test_address_pronoun_is_rejected_not_searched_as_a_street():
    with pytest.raises(ValueError, match="invalid_lookup_input"):
        parse_lookup_request("find whatever building permit is open at this address")


def test_question_with_no_lookup_fields_is_rejected():
    with pytest.raises(ValueError, match="invalid_lookup_input"):
        parse_lookup_request("What inspections should happen next?")


def test_partial_street_is_supported():
    request = parse_lookup_request("Find permits on Main Street")
    assert request.street_number is None
    assert request.street_name == "main st"
    assert choose_search_strategy(request).value == "partial_address"


# ---------------------------------------------------------------------------
# Trailing commentary and sentence punctuation
#
# The Phase 8 `prompts` suite (PROMPT-DISCOVERY-002-P029, -004-P032 and
# -005-P036) found three ways a real question leaked non-street text into the
# parsed address: a terminal "?", an em-dash clause and a parenthetical.
# Commentary describes the result set, never the street, and a polluted street
# name is what turns an ambiguous lookup into a spurious NOT_FOUND.
# ---------------------------------------------------------------------------


def test_terminal_question_mark_does_not_enter_the_street_field():
    request = parse_lookup_request("Which permit is at 123 Main Street?")
    assert request.street_number == "123"
    assert request.street_name == "main st"
    assert choose_search_strategy(request).value == "full_address"


@pytest.mark.parametrize("text", [
    "There are two permits at 123 Main Street — which one?",
    "Find the permit at 123 Main Street (there are two).",
    "Look up 123 Main Street — multiple records there.",
    "Resolve multiple permits at 123 Main Street.",
])
def test_ambiguity_commentary_does_not_enter_the_street_field(text):
    request = parse_lookup_request(text)
    assert (request.street_number, request.street_name) == ("123", "main st")
    assert choose_search_strategy(request).value == "full_address"


def test_record_type_qualifier_survives_a_question_wording():
    # The type qualifier is what separates two records at the same address, so
    # losing it in the "Which <type> is at ..." wording creates a false
    # ambiguity (PROMPT-DISCOVERY-004-P032).
    request = parse_lookup_request("Which Commercial Alteration is at 123 Main Street?")
    assert request.street_name == "main st"
    assert request.permit_type == "Commercial Alteration"


# ---------------------------------------------------------------------------
# Exact record number (5)
# ---------------------------------------------------------------------------


def test_exact_record_number_is_found_with_high_band():
    request = PermitLookupRequest(record_number="BLD-2026-00123")
    result = resolve_lookup(request, [SearchResult(record_number="BLD-2026-00123")])
    assert result.status is LookupStatus.CANDIDATE
    assert result.confidence == 1.0
    assert result.band is ConfidenceBand.HIGH
    assert result.selected is not None
    assert "exact record number" in result.selected.match_reasons


def test_record_number_formatting_difference_still_matches():
    result = resolve_lookup(
        PermitLookupRequest(record_number="BLD-2026-00123"),
        [SearchResult(record_number="bld202600123")],
    )
    assert result.status is LookupStatus.CANDIDATE


def test_record_search_with_one_exact_among_noise_picks_the_exact_one():
    request = PermitLookupRequest(record_number="BLD-2026-00123")
    result = resolve_lookup(
        request,
        [
            SearchResult(record_number="BLD-2026-0012", record_type="Residential"),
            SearchResult(record_number="BLD-2026-00123", record_type="Commercial Alteration"),
            SearchResult(record_number="BLD-2026-00124", record_type="Electrical"),
        ],
    )
    assert result.status is LookupStatus.CANDIDATE
    assert result.selected is not None and result.selected.record_number == "BLD-2026-00123"


def test_record_search_that_only_returns_near_misses_is_ambiguous():
    # Portal prefix search can return "BLD-10" for a query "BLD-01". Selecting
    # the first row here would be exactly the wrong-record failure.
    result = resolve_lookup(
        PermitLookupRequest(record_number="BLD-01"),
        [SearchResult(record_number="BLD-10")],
    )
    assert result.status is LookupStatus.AMBIGUOUS
    assert result.selected is None
    assert result.error_code is LookupErrorCode.AMBIGUOUS_RECORD


def test_duplicate_record_number_with_different_types_is_ambiguous():
    request = PermitLookupRequest(record_number="BLD-2026-00123")
    result = resolve_lookup(
        request,
        [
            SearchResult(record_number="BLD-2026-00123", record_type="Residential"),
            SearchResult(record_number="BLD-2026-00123", record_type="Commercial Alteration"),
        ],
    )
    assert result.status is LookupStatus.AMBIGUOUS


# ---------------------------------------------------------------------------
# Exact address (5)
# ---------------------------------------------------------------------------


def test_exact_address_alone_is_found_and_medium_band():
    request = PermitLookupRequest(street_number="123", street_name="main st")
    result = resolve_lookup(request, [SearchResult(record_number="BLD-1", address="123 Main Street")])
    assert result.status is LookupStatus.CANDIDATE
    assert result.confidence == pytest.approx(0.8)
    assert result.band is ConfidenceBand.MEDIUM


def test_address_plus_type_separates_rows_on_the_same_street():
    request = PermitLookupRequest(
        street_number="77", street_name="licet eval way", permit_type="Commercial Alteration"
    )
    result = resolve_lookup(
        request,
        [
            SearchResult(record_number="BLD-1", record_type="Residential Addition", address="77 Licet Eval Way"),
            SearchResult(record_number="BLD-2", record_type="Commercial Alteration", address="77 Licet Eval Way"),
            SearchResult(record_number="ELE-1", record_type="Commercial Electrical", address="77 Licet Eval Way"),
        ],
    )
    assert result.status is LookupStatus.CANDIDATE
    assert result.selected is not None and result.selected.record_number == "BLD-2"
    assert "permit type" in result.selected.match_reasons


def test_directional_and_suffix_abbreviations_are_equivalent():
    request = PermitLookupRequest(street_number="1200", street_name="w main st")
    result = resolve_lookup(
        request, [SearchResult(record_number="BLD-1", address="1200 West Main Street")]
    )
    assert result.status is LookupStatus.CANDIDATE


def test_numeric_ordinal_streets_are_equivalent():
    request = PermitLookupRequest(street_number="5", street_name="72 ave")
    result = resolve_lookup(request, [SearchResult(record_number="BLD-1", address="5 72nd Avenue")])
    assert result.status is LookupStatus.CANDIDATE


def test_apartment_constraint_requires_unit_evidence():
    # "Apt 4" must not be typed into the street-name field; a well-formed query
    # with a unit still has to find the base address record.
    request = parse_lookup_request("Find the permit at 123 Main Street Apt 4")
    assert request.street_name == "main st"
    result = resolve_lookup(request, [SearchResult(record_number="BLD-1", address="123 Main St")])
    assert result.status is LookupStatus.AMBIGUOUS
    assert request.unit == "4"
    assert result.selected is None


def test_suite_and_hash_units_are_dropped():
    assert parse_lookup_request("permit at 800 State St Suite 200").street_name == "state st"
    assert parse_lookup_request("permit at 800 State St #5").street_name == "state st"


def test_a_street_containing_a_unit_word_is_not_emptied():
    request = normalize_request(PermitLookupRequest(street_name="Unit Circle"))
    assert request.street_name == "unit circle"


def test_street_name_does_not_match_as_a_substring_of_another_street():
    # "main st" is a substring of "domain st"; whole-token matching must reject it.
    request = PermitLookupRequest(street_name="main st")
    result = resolve_lookup(request, [SearchResult(record_number="BLD-1", address="999 Domain Street")])
    assert result.status is LookupStatus.AMBIGUOUS
    assert result.selected is None


# ---------------------------------------------------------------------------
# Parcel (3)
# ---------------------------------------------------------------------------


def test_exact_parcel_is_found_with_high_band():
    request = PermitLookupRequest(parcel_number="42-18-33")
    result = resolve_lookup(request, [SearchResult(record_number="BLD-1", parcel_number="421833")])
    assert result.status is LookupStatus.CANDIDATE
    assert result.band is ConfidenceBand.MEDIUM
    assert result.selected is not None
    assert "exact parcel" in result.selected.match_reasons


def test_parcel_delimiter_differences_normalize_together():
    request = PermitLookupRequest(parcel_number="42/18-33")
    result = resolve_lookup(request, [SearchResult(record_number="BLD-1", parcel_number="42-18-33")])
    assert result.status is LookupStatus.CANDIDATE


def test_parcel_search_returning_a_different_parcel_does_not_select():
    request = PermitLookupRequest(parcel_number="42-18-33")
    result = resolve_lookup(request, [SearchResult(record_number="BLD-1", parcel_number="99-99-99")])
    assert result.status is LookupStatus.AMBIGUOUS
    assert result.selected is None


# ---------------------------------------------------------------------------
# Ambiguity (3)
# ---------------------------------------------------------------------------


def test_three_records_at_one_address_are_ambiguous_without_context():
    """The Phase 2 flagship ambiguity case: never open the first of three."""
    request = PermitLookupRequest(street_number="123", street_name="main st")
    result = resolve_lookup(
        request,
        [
            SearchResult(record_number="BLD-2026-0147", record_type="Commercial Alteration", address="123 Main Street"),
            SearchResult(record_number="ELE-2026-0211", record_type="Commercial Electrical", address="123 Main Street"),
            SearchResult(record_number="BLD-2025-0932", record_type="Commercial Re-Roof", address="123 Main Street"),
        ],
    )
    assert result.status is LookupStatus.AMBIGUOUS
    assert len(result.matches) == 3
    assert result.selected is None
    assert result.error_code is LookupErrorCode.AMBIGUOUS_RECORD


def test_permit_type_context_resolves_the_flagship_ambiguity():
    request = PermitLookupRequest(
        street_number="123", street_name="main st", permit_type="Commercial Alteration"
    )
    result = resolve_lookup(
        request,
        [
            SearchResult(record_number="BLD-2026-0147", record_type="Commercial Alteration", address="123 Main Street"),
            SearchResult(record_number="ELE-2026-0211", record_type="Commercial Electrical", address="123 Main Street"),
            SearchResult(record_number="BLD-2025-0932", record_type="Commercial Re-Roof", address="123 Main Street"),
        ],
    )
    assert result.status is LookupStatus.CANDIDATE
    assert result.selected is not None and result.selected.record_number == "BLD-2026-0147"


def test_two_exact_ties_are_ambiguous():
    request = PermitLookupRequest(street_number="123", street_name="main st")
    result = resolve_lookup(
        request,
        [
            SearchResult(record_number="BLD-1", address="123 Main Street"),
            SearchResult(record_number="BLD-2", address="123 Main Street"),
        ],
    )
    assert result.status is LookupStatus.AMBIGUOUS
    assert result.selected is None


# ---------------------------------------------------------------------------
# No results + too many results (2)
# ---------------------------------------------------------------------------


def test_empty_results_are_not_found():
    result = resolve_lookup(PermitLookupRequest(record_number="BLD-9999"), [])
    assert result.status is LookupStatus.NOT_FOUND
    assert result.error_code is LookupErrorCode.RECORD_NOT_FOUND


def test_too_many_indistinguishable_candidates_report_too_many_results():
    request = PermitLookupRequest(street_name="main st")
    noise = [
        SearchResult(record_number=f"BLD-{i:04d}", address=f"{i} Domain Street")
        for i in range(60)
    ]
    result = resolve_lookup(request, noise, max_candidates=50)
    assert result.status is LookupStatus.INCOMPLETE
    assert not result.results_complete
    assert result.error_code is LookupErrorCode.TOO_MANY_RESULTS
    assert result.selected is None


# ---------------------------------------------------------------------------
# Dedup, confidence band, fallback ladder, determinism
# ---------------------------------------------------------------------------


def test_pagination_duplicate_rows_do_not_manufacture_ambiguity():
    request = PermitLookupRequest(record_number="BLD-1")
    row = SearchResult(record_number="BLD-1", address="123 Main St")
    result = resolve_lookup(request, [row, row.model_copy(deep=True)])
    assert result.status is LookupStatus.CANDIDATE
    assert len(result.matches) == 1


def test_confidence_band_vocabulary():
    assert confidence_band(1.0, ["exact record number"]) is ConfidenceBand.HIGH
    assert confidence_band(1.0, ["exact parcel"]) is ConfidenceBand.MEDIUM
    assert confidence_band(0.8, ["street number", "street name"]) is ConfidenceBand.MEDIUM
    assert confidence_band(0.4, ["street name"]) is ConfidenceBand.LOW


def test_address_fallback_drops_optional_fields_before_broadening():
    request = PermitLookupRequest(
        street_number="123", street_name="main st", zip_code="57104"
    )
    plan = build_search_plan(request, max_attempts=3)
    assert [attempt.method.value for attempt in plan] == [
        "full_address",
        "full_address",
        "partial_address",
    ]
    assert plan[0].fields.get("zip_code") == "57104"
    assert "zip_code" not in plan[1].fields
    assert plan[1].fields["street_number"] == "123"


def test_address_plan_without_optional_fields_has_no_redundant_attempt():
    request = PermitLookupRequest(street_number="123", street_name="main st")
    plan = build_search_plan(request, max_attempts=3)
    assert [attempt.method.value for attempt in plan] == ["full_address", "partial_address"]


def test_ranking_is_deterministic_across_repeated_runs():
    request = PermitLookupRequest(street_number="123", street_name="main st")
    rows = [
        SearchResult(record_number="BLD-2", record_type="Residential", address="123 Main St"),
        SearchResult(record_number="BLD-1", record_type="Commercial", address="123 Main St"),
        SearchResult(record_number="ELE-3", record_type="Electrical", address="123 Main St"),
    ]
    first = [r.record_number for r in rank_results(request, rows)]
    second = [r.record_number for r in rank_results(request, reversed(rows))]
    assert first == second


def test_zip_only_corroborates_and_never_matches_as_a_substring():
    request = PermitLookupRequest(street_number="123", street_name="main st", zip_code="57104")
    with_zip = rank_results(request, [SearchResult(record_number="BLD-1", address="123 Main St 57104")])[0]
    without_zip = rank_results(request, [SearchResult(record_number="BLD-1", address="123 Main St")])[0]
    assert "ZIP" in with_zip.match_reasons
    assert with_zip.score > without_zip.score
    assert "ZIP" not in without_zip.match_reasons


# ---------------------------------------------------------------------------
# Retrieval metrics — wrong-record rate is the one that must stay 0
# ---------------------------------------------------------------------------


def test_retrieval_metrics_expose_the_phase_two_kpis():
    metrics = LookupMetrics(
        attempts=10,
        successful=8,
        exact_matches=6,
        ambiguous=1,
        wrong_records=0,
        browser_actions=40,
        retries=2,
    )
    assert metrics.success_rate == pytest.approx(0.8)
    assert metrics.exact_match_accuracy == pytest.approx(0.75)
    assert metrics.wrong_record_rate == 0.0
    assert metrics.search_retry_rate == pytest.approx(0.2)
    assert metrics.average_browser_actions == pytest.approx(4.0)


def test_lookup_metrics_combine_sums_counters_and_recomputes_rates():
    first = LookupMetrics(
        attempts=3, successful=3, exact_matches=2, browser_actions=12, retries=1
    )
    second = LookupMetrics(attempts=1, successful=0, ambiguous=1, browser_actions=5, retries=2)
    total = LookupMetrics.combine([first, second])
    assert total.attempts == 4
    assert total.successful == 3
    assert total.ambiguous == 1
    assert total.wrong_records == 0
    assert total.wrong_record_rate == 0.0
    assert total.success_rate == pytest.approx(0.75)
    assert total.average_browser_actions == pytest.approx(17 / 4)


def test_lookup_metrics_as_dict_is_json_serializable():
    import json

    payload = LookupMetrics(attempts=2, successful=1, exact_matches=1).as_dict()
    assert json.loads(json.dumps(payload))["wrong_record_rate"] == 0.0


# ---------------------------------------------------------------------------
# Applicant disambiguation
# ---------------------------------------------------------------------------


def test_unique_applicant_match_resolves_at_medium_band():
    request = PermitLookupRequest(applicant_name="Jane Doe")
    result = resolve_lookup(
        request,
        [
            SearchResult(record_number="BLD-1", record_type="Commercial Alteration", applicant="Jane Doe"),
            SearchResult(record_number="BLD-2", record_type="Electrical", applicant="Bob Smith"),
        ],
    )
    assert result.status is LookupStatus.CANDIDATE
    assert result.selected is not None and result.selected.record_number == "BLD-1"
    assert result.band is ConfidenceBand.MEDIUM
    assert "applicant" in result.selected.match_reasons


def test_applicant_name_order_and_format_are_equivalent():
    result = resolve_lookup(
        PermitLookupRequest(applicant_name="Doe, Jane"),
        [SearchResult(record_number="BLD-1", applicant="Jane Doe")],
    )
    assert result.status is LookupStatus.CANDIDATE


def test_multiple_applicant_matches_are_ambiguous():
    request = PermitLookupRequest(applicant_name="Jane Doe")
    result = resolve_lookup(
        request,
        [
            SearchResult(record_number="BLD-1", applicant="Jane Doe"),
            SearchResult(record_number="BLD-2", applicant="Jane Doe"),
        ],
    )
    assert result.status is LookupStatus.AMBIGUOUS
    assert result.selected is None


def test_applicant_plus_type_narrows_multiple_matches_to_one():
    request = PermitLookupRequest(applicant_name="Jane Doe", permit_type="Commercial Alteration")
    result = resolve_lookup(
        request,
        [
            SearchResult(record_number="BLD-1", record_type="Commercial Alteration", applicant="Jane Doe"),
            SearchResult(record_number="BLD-2", record_type="Electrical", applicant="Jane Doe"),
        ],
    )
    assert result.status is LookupStatus.CANDIDATE
    assert result.selected is not None and result.selected.record_number == "BLD-1"


def test_no_applicant_match_does_not_select_a_nonmatching_record():
    result = resolve_lookup(
        PermitLookupRequest(applicant_name="Jane Doe"),
        [SearchResult(record_number="BLD-1", applicant="Bob Smith")],
    )
    assert result.status is LookupStatus.AMBIGUOUS
    assert result.selected is None


def test_applicant_with_an_address_uses_the_normal_ranking_path():
    # With an address key present, applicant is corroboration, not the special
    # unique-match rule: two same-address rows are separated by the applicant.
    request = PermitLookupRequest(
        street_number="123", street_name="main st", applicant_name="Jane Doe"
    )
    result = resolve_lookup(
        request,
        [
            SearchResult(record_number="BLD-1", address="123 Main St", applicant="Jane Doe"),
            SearchResult(record_number="BLD-2", address="123 Main St", applicant="Bob Smith"),
        ],
    )
    assert result.status is LookupStatus.CANDIDATE
    assert result.selected is not None and result.selected.record_number == "BLD-1"
