from licet.schema.permit import (
    Document,
    Fact,
    Fee,
    Inspection,
    InspectionStatus,
    Permit,
    PermitStatus,
    Provenance,
    RecordRef,
    normalize_inspection_status,
    normalize_permit_status,
)


def test_permit_roundtrip_minimal():
    permit = Permit(permit_id="ABC-123", address="123 Main St")
    assert permit.permit_id == "ABC-123"
    assert permit.inspections == []
    assert permit.fees == []


def test_permit_roundtrip_full():
    permit = Permit(
        permit_id="ABC-123",
        address="123 Main St",
        permit_type="Electrical",
        status="Open",
        applicant="Jane Doe",
        issued_date="2026-01-01",
        expiration_date="2026-12-31",
        inspections=[
            Inspection(type="Rough-In", status="Passed", date="2026-02-01", comments="ok"),
            Inspection(type="Final", status="Scheduled", date=None, comments=None),
        ],
        fees=[Fee(description="Permit fee", amount=150.0, paid=True)],
        documents=["plan.pdf"],
        outstanding_requirements=["Final inspection"],
        next_action="Schedule final inspection",
    )
    assert permit.inspections[0].status == "Passed"
    assert permit.inspections[0].status_normalized is InspectionStatus.PASSED
    assert permit.fees[0].paid is True
    assert permit.next_action.value == "Schedule final inspection"
    assert permit.next_action.provenance is Provenance.DERIVED


def _row_use_permit() -> Permit:
    """bld26 00472, as captured from my records + the detail deep link"""
    return Permit(
        permit_id="BLD26-00472",
        address="91 Commerce Ave, 00001 United States",
        ref=RecordRef(
            cap_id1="REC26", cap_id2="00000", cap_id3="000QG", display_id="BLD26-00472"
        ),
        permit_type="Right of Way Use Permit",
        status="Submitted",
        applicant="Eval User / Licet Eval Testing LLC",
        description="Licet eval application - right of way use permit for testing",
        submitted_date="2026-09-19",
        expiration_date="2026-01-31",
        sections=["Schedule an Inspection", "Record Info", "Payments", "Attachments"],
    )


def test_record_ref_builds_the_verified_deep_link():
    permit = _row_use_permit()
    assert permit.ref.detail_url() == (
        "https://aca-test.accela.com/NULLISLAND/Cap/CapDetail.aspx"
        "?Module=Building&TabName=Building"
        "&capID1=REC26&capID2=00000&capID3=000QG"
        "&agencyCode=NULLISLAND&IsToShowInspection="
    )
    assert permit.ref.as_key() == "NULLISLAND/Building/REC26/00000/000QG"


def test_display_id_is_not_identity():
    """commercial alteration renders 000000014 while others render bld26 004xx"""
    commercial = RecordRef(
        cap_id1="REC26", cap_id2="00000", cap_id3="000QB", display_id="000000014"
    )
    sign = RecordRef(cap_id1="REC26", cap_id2="00000", cap_id3="000QA", display_id="BLD26-00467")
    assert commercial.display_id != sign.display_id
    assert commercial.detail_url() != sign.detail_url()
    assert commercial.as_key() != sign.as_key()


def test_status_is_kept_raw_and_normalized():
    permit = _row_use_permit()
    assert permit.status == "Submitted"
    assert permit.status_normalized is PermitStatus.SUBMITTED
    assert normalize_permit_status("Insp Scheduled") is PermitStatus.UNKNOWN
    assert normalize_permit_status("") is PermitStatus.UNKNOWN


def test_submitted_is_not_issued_and_expiration_is_config_not_outcome():
    permit = _row_use_permit()
    assert permit.submitted_date is not None
    assert permit.issued_date is None
    assert permit.expiration_date is not None
    assert permit.status_normalized is not PermitStatus.EXPIRED


def test_inspection_keeps_id_dates_and_normalized_status():
    inspection = Inspection(
        type="Mechanical Final",
        status="Insp Scheduled",
        inspection_id="18482246",
        scheduled_date="2026-05-20",
        comments=None,
    )
    assert inspection.inspection_id == "18482246"
    assert inspection.status_normalized is InspectionStatus.SCHEDULED
    assert normalize_inspection_status("Failed") is InspectionStatus.FAILED
    assert normalize_inspection_status("Whatever") is InspectionStatus.UNKNOWN


def test_documents_and_requirements_accept_plain_strings_but_keep_provenance():
    permit = Permit(
        permit_id="BLD26-00467",
        address="77 Licet Eval Way",
        documents=["plan.pdf"],
        outstanding_requirements=["Final inspection"],
        next_action="Schedule final inspection",
    )
    assert permit.documents[0] == Document(name="plan.pdf")
    assert permit.outstanding_requirements[0].provenance is Provenance.UNATTRIBUTED
    assert permit.next_action.provenance is Provenance.DERIVED
    assert isinstance(permit.next_action, Fact)


def test_missing_inspections_is_the_next_inspection_answer():
    """only explicitly required types minus completing history are missing"""
    permit = Permit(
        permit_id="BLD26-00470",
        address="87 Commerce Ave",
        schedulable_inspection_types=["Mechanical Final", "Building Final", "Electrical Final"],
        required_inspection_types=["Mechanical Final", "Building Final"],
        inspections=[
            Inspection(type="Mechanical Final", status="Passed"),
        ],
    )
    assert permit.missing_inspections() == ["Building Final"]
    assert permit.offered_inspection_types() == [
        "Mechanical Final", "Building Final", "Electrical Final"
    ]
    # offered but unseen types are options, never obligations
    catalog_only = Permit(
        permit_id="BLD26-00470",
        address="87 Commerce Ave",
        schedulable_inspection_types=["Electrical Final"],
    )
    assert catalog_only.missing_inspections() == []


def test_fee_keeps_float_and_text():
    fee = Fee(description="Permit fee", amount=150.0, paid=True)
    assert fee.amount == 150.0
    assert fee.amount_text == "150.00"
    text_only = Fee(description="Permit fee", amount_text="$1,234.56")
    assert text_only.amount is None
