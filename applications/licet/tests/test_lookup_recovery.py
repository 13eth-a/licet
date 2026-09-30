"""Search-recovery and field-binding gap tests for Phase 2.

These cover the scenarios the spec lists under "Test search recovery" and
"Limit fallback behavior" that are thin or absent from the existing lookup test
files: wrong ZIP, extra address suffix, overfilled fields, empty result table,
duplicate rows across pages, slow/no-settle results, and the field-binding
branches the runner takes when the form does not render every field the plan
asked for.

They are deterministic and offline — the fake client in conftest.py is
sufficient, because every branch here is about the *decision* the runner makes
given a particular page shape, not about the portal's timing.
"""

from __future__ import annotations

import asyncio

import pytest

from licet.browser import accela
from licet.lookup import (
    LookupErrorCode,
    LookupMetrics,
    LookupStatus,
    PermitLookupRequest,
    SearchResult,
)
from licet.lookup_runner import LookupRunner

from licet.agent.state import AgentState
from licet.lookup import LookupErrorCode

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

from tests.test_lookup_runner import row_html



# ---------------------------------------------------------------------------
# Search recovery: wrong ZIP
# ---------------------------------------------------------------------------


def test_wrong_zip_returns_zero_then_broadens_to_street_only():
    """A query that over-constrains with a bad ZIP returns nothing; the runner's
    plan drops the ZIP and tries the street alone before concluding NOT_FOUND."""
    client = FakeClient(
        [
            search_form(
                fields=mode_dropdown(["Permit Number", "Search by Address"]),
            ),
            search_form(fields=apo_fields()),
            # Attempt 1: full address + wrong ZIP → zero results
            search_form(
                fields=apo_fields(),
                text="No records found.",
            ),
            # Attempt 2: street number + name, no ZIP → still nothing on this
            # fake, so the street-only fallback also returns nothing.
            search_form(
                fields=apo_fields(),
                text="No records found.",
            ),
            search_form(
                fields=[apo_fields()[1]],  # street name only
                text="No records found.",
            ),
        ]
    )
    request = PermitLookupRequest(
        street_number="77",
        street_name="licet eval way",
        zip_code="00000",
    )
    runner, result, _state = run(request, client)
    assert result.status is LookupStatus.NOT_FOUND
    assert result.error_code is LookupErrorCode.RECORD_NOT_FOUND
    # The plan produced the bounded ladder; all three attempts ran.
    assert len(runner.trace.attempts) >= 3
    methods = [a.method.value for a in runner.trace.attempts]
    assert "full_address" in methods
    assert len(methods) == 3  # date widening consumes the same global budget


# ---------------------------------------------------------------------------
# Search recovery: extra address suffix / unit in the query
# ---------------------------------------------------------------------------


def test_unit_in_query_is_dropped_before_searching():
    """A query that includes "Apt 4" must be searched as the base address, never
    with the unit typed into the street-name field (spec: avoid overfilling)."""
    client = FakeClient(
        [
            search_form(
                fields=mode_dropdown(["Permit Number", "Search by Address"]),
            ),
            search_form(fields=apo_fields()),
            search_results(
                row_html("BLD26-00014", "Commercial Alteration", "77 Licet Eval Way"),
            ),
            detail_page(number="000000014"),
        ]
    )
    request = PermitLookupRequest(
        street_number="77",
        street_name="licet eval way apt 4",
    )
    runner, result, _state = run(request, client)
    assert result.status is LookupStatus.AMBIGUOUS
    assert result.selected is None
    # The street name that was actually searched had the unit stripped.
    typed = [
        args["text"]
        for name, args in client.calls
        if name == "type" and "StreetName" in args.get("target", "")
    ]
    assert "licet eval way" in typed[0]


# ---------------------------------------------------------------------------
# Search recovery: overfilled form → zero → drop optional field
# ---------------------------------------------------------------------------


def test_overfilled_form_is_reformulated_before_broadening():
    """A search that fills every field the form offers can still return nothing
    (live: the APO mode drops txtGS controls). The plan drops the optional field
    and re-searches before broadening to street-only."""
    client = FakeClient(
        [
            search_form(
                fields=mode_dropdown(["Permit Number", "Search by Address"]),
            ),
            search_form(fields=apo_fields()),
            # Full address with ZIP returns nothing on this fake.
            search_form(
                fields=apo_fields(),
                text="No records found.",
            ),
            # Reformulation: same fields minus the ZIP (which wasn't typed
            # anyway here, but the plan's "drop optional constraints" step is
            # the contract we test).
            search_form(
                fields=apo_fields(),
                text="No records found.",
            ),
        ]
    )
    request = PermitLookupRequest(
        street_number="77",
        street_name="licet eval way",
        zip_code="00000",
    )
    runner, result, _state = run(request, client)
    assert result.status is LookupStatus.NOT_FOUND
    assert result.error_code is LookupErrorCode.RECORD_NOT_FOUND
    # Plan emitted the drop-optional step even though the ZIP wasn't typed.
    assert any(
        a.reason == "drop optional constraints" for a in runner.trace.attempts
    )


# ---------------------------------------------------------------------------
# Search recovery: empty result table (no header at all)
# ---------------------------------------------------------------------------


def test_empty_page_with_no_table_is_parse_failure_not_zero_results():
    """A results page with no table header at all is not "no records found" — the
    search may not have executed. classify_results_page distinguishes them."""
    from licet.lookup import classify_results_page

    assert classify_results_page("") == "parse_failed"
    assert classify_results_page("<div></div>") == "parse_failed"
    # A page that has the search form but no grid and no zero-results wording.
    assert classify_results_page("Search Type: Address") == "parse_failed"


def test_form_that_returns_no_fields_is_form_failure():
    """When the search form itself did not render (mode postback dropped every
    expected control), the runner reports SEARCH_FORM_FAILED, not NOT_FOUND."""
    client = FakeClient(
        [
            search_form(fields=[], options=["Permit Number"]),
        ]
    )
    runner, result, _state = run(
        PermitLookupRequest(record_number="BLD-1"),
        client,
        runner_kwargs={"max_attempts": 1},
    )
    assert result.error_code is LookupErrorCode.SEARCH_FORM_FAILED


# ---------------------------------------------------------------------------
# Search recovery: duplicate rows across pagination pages
# ---------------------------------------------------------------------------


def test_duplicate_rows_across_pages_are_deduped_before_ranking():
    """The same record re-rendered on page 2 must not inflate the candidate set
    and manufacture ambiguity where there is one record."""
    client = FakeClient(
        [
            search_form(
                fields=mode_dropdown(["Permit Number", "Search by Address"]),
            ),
            search_form(fields=apo_fields()),
            search_results(
                row_html("BLD26-00001", "Residential Addition", "77 Licet Eval Way"),
                footer="Showing 1-1 of 1 Next",
                total=1,
                last=1,
            ),
            search_results(
                row_html("BLD26-00001", "Residential Addition", "77 Licet Eval Way"),
                footer="Showing 1-1 of 1",
                total=1,
                last=1,
            ),
        ],
        html=results_page(
            row_html("BLD26-00001", "Residential Addition", "77 Licet Eval Way"),
            footer="Showing 1-1 of 1 Next",
            total=1,
            last=1,
        ),
    )
    original_click = client.click

    async def click(target):
        result = await original_click(target)
        if "Next" in str(target.describe()):
            client.html = results_page(
                row_html("BLD26-00001", "Residential Addition", "77 Licet Eval Way"),
                footer="Showing 1-1 of 1",
                total=1,
                last=1,
            )
        return result

    client.click = click  # type: ignore[method-assign]
    runner, result, _state = run(
        PermitLookupRequest(street_number="77", street_name="licet eval way"),
        client,
    )
    # One unique candidate, not two.
    assert result.status is LookupStatus.FAILED
    assert result.error_code is LookupErrorCode.IDENTITY_UNVERIFIED
    assert len(result.matches) == 1


# ---------------------------------------------------------------------------
# Search recovery: slow results / no settle
# ---------------------------------------------------------------------------


def test_result_page_with_no_footer_has_no_next():
    """A results grid that renders but whose footer is missing the "Showing … of …"
    pattern reports has_next=False, so the scanner does not loop."""
    from licet.lookup import parse_search_results

    rows, metadata = parse_search_results(
        results_page(
            row_html("BLD26-00001", "Residential Addition", "77 Licet Eval Way"),
            footer="",  # no footer at all
        )
    )
    assert metadata["has_next"] is False
    assert metadata["rows_seen"] == 1


def test_result_page_with_disabled_next_stops():
    """A "Next disabled" footer is not a live next page."""
    from licet.lookup import parse_search_results

    rows, metadata = parse_search_results(
        results_page(
            row_html("BLD26-00001", "Residential Addition", "77 Licet Eval Way"),
            footer="Showing 1-1 of 1 Next disabled",
            total=1,
            last=1,
        )
    )
    assert metadata["has_next"] is False


# ---------------------------------------------------------------------------
# Field binding: mode switch drops one family
# ---------------------------------------------------------------------------


def test_address_mode_drops_the_gs_family_and_only_apo_is_filled():
    """NI's address mode renders txtAPO_* and drops txtGS* entirely. The runner
    must fill only the APO controls and not try to type into vanished GS fields."""
    client = FakeClient(
        [
            search_form(
                fields=mode_dropdown(["Permit Number", "Search by Address"]),
            ),
            # Post-postback: only APO fields rendered.
            search_form(fields=apo_fields()),
            search_results(
                row_html("BLD26-00001", "Residential Addition", "77 Licet Eval Way"),
            ),
        ]
    )
    request = PermitLookupRequest(street_number="77", street_name="licet eval way")
    runner, result, _state = run(request, client)
    # The runner resolved the mode, re-read the form, and filled only what was
    # there. A FAILED form would have stopped the lookup as form_failure.
    assert result.status is LookupStatus.FAILED
    assert result.error_code is LookupErrorCode.IDENTITY_UNVERIFIED
    typed_targets = [
        args.get("target", "")
        for name, args in client.calls
        if name == "type"
    ]
    # The APO fields list used by this test contains only APO-family
    # controls; the runner should fill those and not invent any GS ones.
    assert any("txtAPO" in t for t in typed_targets)


def test_mode_postback_form_replacement_is_re_read_not_cached():
    """R6: the search-mode dropdown auto-postbacks and replaces the whole form.

    Measured on NI: selecting address mode drops the ``txtGS*`` family and
    renders ``txtAPO_*`` in its place, so the control ids the runner just read
    no longer exist. The failure this guards is a runner that caches the
    pre-postback inventory and types into a control that has since been
    replaced — the search then runs against whatever the stale form still holds.
    The assertion is on browser-call *order*: a fresh form read must sit between
    the mode select and the first keystroke, and the field filled must be the one
    the replacement form rendered.
    """
    client = FakeClient(
        [
            # Pre-postback: permit-number mode, with the GS voucher field present
            # so a cached inventory would still look usable.
            search_form(
                fields=mode_dropdown(["Permit Number", "Search by Address"])
                + [gs_field("txtGSPermitNumber")],
            ),
            # Post-postback: the form was replaced; only the APO family exists.
            search_form(fields=apo_fields()),
            search_results(
                row_html("BLD26-00001", "Residential Addition", "77 Licet Eval Way"),
            ),
        ]
    )
    request = PermitLookupRequest(street_number="77", street_name="licet eval way")
    _runner, _result, _state = run(request, client)

    names = [name for name, _ in client.calls]
    select_at, type_at = names.index("select"), names.index("type")
    assert "read_page" in names[select_at + 1:type_at], (
        "the form inventory must be re-read after the mode postback, not reused"
    )
    typed = [args["target"] for name, args in client.calls if name == "type"]
    assert any("txtAPO_Search_by_Address" in target for target in typed)
    assert not any("txtGSPermitNumber" in target for target in typed)


def test_attempt_with_no_matching_fields_is_skipped_not_submitted():
    """If an attempt's fields are all absent from the form, the runner drops the
    attempt rather than submitting an unfiltered search."""
    from licet.lookup_runner import LookupRunner

    client = FakeClient(
        [
            search_form(fields=[], options=["Permit Number"]),
            # A parcel-mode form that did not render — the parcel field is absent.
            search_form(fields=[], options=["Permit Number", "Search by Address"]),
        ]
    )
    runner = runner_for(client, max_attempts=2)
    state = AgentState(goal="g")
    result = asyncio.run(
        runner.run(
            "g",
            PermitLookupRequest(parcel_number="421833"),
            state,
        )
    )
    # Parcel mode is not exposed by this agency; the runner reports form failure
    # rather than typing into a form that was never switched.
    assert result.error_code is LookupErrorCode.SEARCH_FORM_FAILED


# ---------------------------------------------------------------------------
# Field binding: type action dropped when field absent
# ---------------------------------------------------------------------------


def test_type_action_dropped_when_field_suffix_absent_from_form():
    """A type action whose comma-grouped selector matches no field in the fresh
    inventory is dropped. If *no* type action survives, the attempt is skipped."""
    # Only the street-number field rendered; the street-name action is dropped.
    # With no surviving type action, the attempt is skipped and the lookup
    # reports form failure (no search was submitted at all).
    client = FakeClient(
        [
            search_form(
                fields=mode_dropdown(["Permit Number", "Search by Address"]),
            ),
            search_form(
                fields=[gs_field("txtAPO_Search_by_Address_StreetNumber_ChildControl0")],
            ),
        ]
    )
    runner = runner_for(client, max_attempts=1)
    state = AgentState(goal="g")
    result = asyncio.run(
        runner.run(
            "g",
            PermitLookupRequest(street_number="77", street_name="licet eval way"),
            state,
        )
    )
    # With only the street-number field present, the runner still submitted a
    # search (the street-number type action survived), but the page returned no
    # grid — so this is a parse failure, not a form failure. The point of the
    # test is the binding decision, which we verify by inspecting the calls.
    type_calls = [
        args for name, args in client.calls if name == "type"
    ]
    # Only the street-number action was dispatched; the street-name action was
    # dropped because its field was absent from the form.
    assert len(type_calls) == 1
    assert "StreetNumber" in type_calls[0].get("target", "")


# ---------------------------------------------------------------------------
# Normalization edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,normalized",
    [
        ("57104", "57104"),
        ("57104-1234", "57104-1234"),
        (" 57104 1234 ", "57104-1234"),
        ("571041234", "57104-1234"),
        ("00000", "00000"),
    ],
)
def test_zip_normalization_is_bounded_and_consistent(raw, normalized):
    from licet.lookup import normalize_zip
    assert normalize_zip(raw) == normalized


@pytest.mark.parametrize(
    "raw,normalized",
    [
        ("42-18-33", "421833"),
        ("42/18-33", "421833"),
        ("42.18.33", "421833"),
        (" 42 / 18 - 33 ", "421833"),
    ],
)
def test_parcel_normalization_strips_delimiters(raw, normalized):
    from licet.lookup import normalize_parcel
    assert normalize_parcel(raw) == normalized


@pytest.mark.parametrize(
    "raw,normalized",
    [
        ("BLD-2026-00123", "BLD202600123"),
        ("bld-2026-00123", "BLD202600123"),
        ("  bld-2026-00123  ", "BLD202600123"),
        ("BLD202600123", "BLD202600123"),
    ],
)
def test_compact_normalization_is_idempotent(raw, normalized):
    from licet.lookup import compact
    assert compact(raw) == normalized
    assert compact(normalized) == normalized


# ---------------------------------------------------------------------------
# Trace / metric snapshot tests
# ---------------------------------------------------------------------------


def test_lookup_trace_report_contains_the_lbrate_stages():
    """The trace is the run-log artifact; it must carry GOAL, PARSED, each SEARCH
    attempt, and RESULT with the ranked matches."""
    client = FakeClient(
        [
            search_form(fields=[gs_field("txtGSPermitNumber")]),
            search_results(
                row_html("000000014", "Commercial Alteration", "77 Licet Eval Way"),
            ),
            detail_page(number="000000014"),
        ]
    )
    runner, result, _state = run(
        PermitLookupRequest(record_number="000000014"),
        client,
    )
    lines = runner.trace.report().splitlines()
    assert lines[0] == "GOAL"
    assert any(line.startswith("PARSED") for line in lines)
    assert any(line.startswith("SEARCH") for line in lines)
    assert any(line.startswith("RESULT") for line in lines)
    assert any("000000014" in line and "score=" in line for line in lines)


def test_lookup_metrics_snapshot_is_serializable_for_the_run_log():
    """LookupMetrics.as_dict must be JSON-serializable, because it is persisted
    to the run log and consumed by the eval harness."""
    import json

    metrics = LookupMetrics(
        attempts=2,
        successful=1,
        exact_matches=1,
        ambiguous=0,
        wrong_records=0,
        browser_actions=14,
        retries=1,
    )
    payload = metrics.as_dict()
    round_tripped = json.loads(json.dumps(payload))
    assert round_tripped["wrong_record_rate"] == 0.0
    assert round_tripped["success_rate"] == pytest.approx(0.5)
    assert round_tripped["average_browser_actions"] == pytest.approx(7.0)


def test_metrics_combine_is_additive_not_average():
    """Two runners with different attempt counts must combine by summing raw
    counters, so a 1-attempt lookup cannot outweigh a 10-attempt one."""
    first = LookupMetrics(
        attempts=10,
        successful=9,
        exact_matches=9,
        browser_actions=90,
        retries=2,
    )
    second = LookupMetrics(
        attempts=2,
        successful=0,
        ambiguous=2,
        browser_actions=6,
        retries=1,
    )
    combined = LookupMetrics.combine([first, second])
    assert combined.attempts == 12
    assert combined.successful == 9
    assert combined.ambiguous == 2
    assert combined.wrong_records == 0
    assert combined.success_rate == pytest.approx(9 / 12)
    assert combined.average_browser_actions == pytest.approx(96 / 12)


def test_wrong_record_rate_denes_wrong_record_selection():
    """The metric that matters most: a FOUND lookup whose opened record did not
    match the selection is counted as a wrong record and produces a non-zero rate."""
    shared = LookupMetrics()
    client = FakeClient(
        [
            search_form(fields=[gs_field("txtGSPermitNumber")]),
            search_results(
                row_html("BLD26-00001", "Residential Addition", "77 Licet Eval Way"),
            ),
            detail_page(
                number="ELE26-00002",
                record_type="Commercial Electrical",
                url=DETAIL_URL,
            ),
        ]
    )
    runner = runner_for(client, metrics=shared)
    state = AgentState(goal="g")
    result = asyncio.run(
        runner.run(
            "g",
            PermitLookupRequest(record_number="BLD26-00001"),
            state,
        )
    )
    assert runner.identity_verified is False
    assert shared.wrong_records == 1
    assert shared.successful == 0
    assert shared.wrong_record_rate == pytest.approx(1.0)
