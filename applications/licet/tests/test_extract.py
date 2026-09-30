"""extraction: the page text we captured becomes a `permit`, with no invention"""

from __future__ import annotations

import datetime as dt

from licet.browser import accela
from licet.schema.extract import apply_next_action, parse_portal_date, permit_from_page
from licet.schema.permit import PermitStatus

DETAIL_URL = (
    "https://aca-test.accela.com/NULLISLAND/Cap/CapDetail.aspx?Module=Building"
    "&TabName=Building&capID1=REC26&capID2=00000&capID3=000QB"
    "&agencyCode=NULLISLAND&IsToShowInspection="
)

DETAIL_TEXT = (
    "Logout | Account Management | Logged in as: Kai Newland\n"
    "Record\u00a0000000014: \n Commercial Alteration\nRecord Status: Submitted\n"
    "Expiration Date: 01/31/2026\n"
    "Add to collection\nRecord Info\nPayments\nAttachments\n Inspections\n"
    "Upcoming\n Schedule or Request an Inspection\n"
    " You have not added any inspections.\n"
    "Click the link above to schedule or request one.\n"
    "Completed\n There are no completed inspections on this record.\n"
)

WIZARD_FIELDS = [
    {
        "kind": "radio",
        "id": "ctl00_phPopup_gvInspectionType_ctl02_rdInspectionType",
        "label": "Brycer Inspection History (required)",
        "value": "84043150",
    },
    {
        "kind": "radio",
        "id": "ctl00_phPopup_gvInspectionType_ctl03_rdInspectionType",
        "label": "Set Backs (optional)",
        "value": "97",
    },
]


def _page(**overrides):
    data = {
        "url": DETAIL_URL,
        "text": DETAIL_TEXT,
        "fields": [],
        "loading": [],
        "calendar": [],
        "inspection_type_total": None,
    }
    data.update(overrides)
    return data


def test_permit_from_page_reads_the_header_and_identity():
    permit = permit_from_page(_page())

    assert permit.permit_id == "000000014"  # not a bld26- id: per-type format
    assert permit.permit_type == "Commercial Alteration"
    assert permit.status == "Submitted"
    assert permit.status_normalized is PermitStatus.SUBMITTED
    assert permit.expiration_date == dt.date(2026, 1, 31)
    assert permit.ref is not None
    assert permit.ref.cap_id3 == "000QB"
    assert permit.ref.as_key() == "NULLISLAND/Building/REC26/00000/000QB"
    assert permit.ref.detail_url() == accela.detail_url("REC26", "00000", "000QB")


def test_permit_from_page_records_sections_and_the_empty_inspection_history():
    permit = permit_from_page(_page())

    assert permit.sections == [
        "Record Info",
        "Payments",
        "Attachments",
        "Inspections",
        "Schedule or Request an Inspection",
    ]
    assert permit.inspections == []
    assert any(
        "no inspection history" in fact.value for fact in permit.coverage_notes
    )
    assert permit.outstanding_requirements == []


def test_a_still_loading_section_is_not_reported_as_no_inspections():
    """live hazard: the inspections section loads over ajax and says 'none' first"""
    permit = permit_from_page(_page(loading=["loading..."]))

    assert not any(
        "no inspection history" in fact.value for fact in permit.coverage_notes
    )
    assert any(
        "still loading" in fact.value for fact in permit.coverage_notes
    )


def test_permit_from_page_keeps_the_portal_status_text_and_parses_the_date():
    permit = permit_from_page(_page())

    assert permit.status == "Submitted"
    assert permit.expiration_date == dt.date(2026, 1, 31)
    # expiration on a submitted record is agency config — the schema keeps both the raw status and the
    # date so "is it expired?" cannot be silently wrong
    assert permit.issued_date is None


def test_wizard_types_and_no_availability_become_next_action():
    permit = permit_from_page(
        _page(
            fields=WIZARD_FIELDS,
            inspection_type_total=18,
            calendar=[
                {"month": "Sep 2026", "active_days": [], "inactive_days": list(range(1, 31))},
                {"month": "Oct 2026", "active_days": [], "inactive_days": list(range(1, 32))},
            ],
        )
    )
    assert permit is not None
    apply_next_action(permit)

    assert permit.schedulable_inspection_types == ["Brycer Inspection History", "Set Backs"]
    # only the portal's own `(required)` marker is requirement evidence; the offered catalog never becomes
    # missing work (architecture review review p1 #1)
    assert permit.required_inspection_types == ["Brycer Inspection History"]
    assert permit.missing_inspections() == ["Brycer Inspection History"]
    assert permit.next_action is not None
    assert "required inspection not yet completed" in permit.next_action.value
    assert any(
        "no bookable appointment dates" in fact.value
        for fact in permit.coverage_notes
    )
    assert any(
        "2 observed month(s)" in fact.value
        for fact in permit.coverage_notes
    )
    assert any(
        "18 inspection types offered but only 2" in fact.value
        for fact in permit.coverage_notes
    )


def test_a_non_record_page_produces_nothing():
    assert permit_from_page({"url": accela.MY_RECORDS_URL, "text": "Showing 1-8 of 8"}) is None


def test_address_is_not_invented():
    permit = permit_from_page(_page(), address="81 Commerce Ave, Null Island 00001")

    assert permit.address == "81 Commerce Ave, Null Island 00001"
    assert permit_from_page(_page()).address == ""


def test_parse_portal_date_handles_real_and_junk_values():
    assert parse_portal_date("01/31/2026") == dt.date(2026, 1, 31)
    assert parse_portal_date(" 09/20/2026 ") == dt.date(2026, 9, 20)
    assert parse_portal_date("") is None
    assert parse_portal_date("not a date") is None
    assert parse_portal_date(None) is None


def test_scheduling_step_text_maps_to_the_flow_step():
    from licet.browser import accela as a

    assert a.schedule_step("Inspection type: Floor Deck | select an appointment date") == "select_date"
    assert a.schedule_step("Available Inspection Types (6)") == "select_type"
