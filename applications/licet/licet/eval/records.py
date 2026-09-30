"""known test permit records and their expected states"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class KnownRecord:
    permit_id: str
    address: str
    expected_status: str
    expected_state: dict[str, Any]
    notes: str = ""


def record_for(permit_id: str) -> KnownRecord | None:
    """look up a record by the id a prompt would name"""
    return next(
        (record for record in (*KNOWN_RECORDS, *PUBLIC_PROBE_RECORDS) if record.permit_id == permit_id),
        None,
    )


# scheduling ground truth (measured live 2026 09 20, scripts/ni_schedule_probe.py +
# scripts/ni_availability_sweep.py)
SCHEDULING_GROUND_TRUTH: dict[str, dict[str, Any]] = {
    "BLD26-00467": {
        "declared_total": 3,
        "required_type": None,
        "page1_types": ["Sign Final", "Electrical Sign Final", "Progress Check"],
        "calendar_active_days": 0,
        "schedulable": False,
    },
    "BLD26-00466": {
        "declared_total": 3,
        "required_type": None,
        "page1_types": ["Sign Final", "Electrical Sign Final", "Progress Check"],
        "calendar_active_days": 0,
        "schedulable": False,
    },
    "000000014": {
        "declared_total": 18,
        "required_type": "Brycer Inspection History",
        "page1_types": [
            "Brycer Inspection History",
            "Set Backs",
            "Temp Power",
            "Footings & Forms",
            "Foundation",
            "Rough Frame",
            "Frame",
            "Floor Deck",
            "Roof Deck",
            "Partial/Temp Building Final",
        ],
        "calendar_active_days": 0,
        "schedulable": False,
    },
    "BLD26-00468": {
        "declared_total": 13,
        "required_type": "Floor Deck",
        "page1_types": [
            "Set Backs",
            "Temp Power",
            "Footings & Forms",
            "Foundation",
            "Rough Frame",
            "Frame",
            "Floor Deck",
            "Roof Deck",
            "Partial/Temp Building Final",
            "Building Final",
        ],
        "calendar_active_days": 0,
        "schedulable": False,
    },
    "BLD26-00469": {
        "declared_total": 6,
        "required_type": None,
        "page1_types": [
            "Rough",
            "Service",
            "Temp Service Pole",
            "Ground Work",
            "Electrical Final",
            "Progress Check",
        ],
        "calendar_active_days": 0,
        "schedulable": False,
    },
    "BLD26-00470": {
        "declared_total": 0,
        "required_type": None,
        "page1_types": [],
        "calendar_active_days": 0,
        "schedulable": False,
        "note": "no inspection types offered at all",
    },
    "BLD26-00471": {
        "declared_total": 3,
        "required_type": None,
        "page1_types": ["Rough", "Solar Final", "Progress Check"],
        "calendar_active_days": 0,
        "schedulable": False,
    },
    "BLD26-00472": {
        "declared_total": 0,
        "required_type": None,
        "page1_types": [],
        "calendar_active_days": 0,
        "schedulable": False,
        "note": "no inspection types offered at all",
    },
}


def scheduling_truth(permit_id: str) -> dict[str, Any] | None:
    return SCHEDULING_GROUND_TRUTH.get(permit_id)


# probe records reachable without login
PUBLIC_PROBE_RECORDS: list[KnownRecord] = [
    KnownRecord(
        permit_id="PLB-10-00951",
        address="",
        expected_status="unknown",
        expected_state={},
        notes=(
            "Omaha (agencyCode=OMAHA, module=Permits). Search-verified live "
            "via Solari 2026-09-18. Anchor record for search->open->read "
            "evals. Production portal: read-only use."
        ),
    ),
    KnownRecord(
        permit_id="22CAP-00000-006RZ",
        address="",
        expected_status="unknown",
        expected_state={},
        notes=(
            "Meridian ID (agencyCode=MERIDIAN, module=Dev-Services). "
            "Dynamically gated for anonymous browsers — deep-link smoke test "
            "only, do not use for correctness evals."
        ),
    ),
]


# null island sandbox records (agreed 2026 09 19; created via application on the citizen portal, tied to
# the public user test account in .env (accela_test_username; registered + login verified 2026 09 19)
KNOWN_RECORDS: list[KnownRecord] = [
    KnownRecord(
        permit_id="BLD26-00467",
        address="77 Licet Eval Way, Null Island 00001",
        expected_status="Submitted",
        expected_state={
            "cap_type": "Building/Sign/Temporary/NA",
            "module": "Building",
            "capids": {"capID1": "REC26", "capID2": "00000",
                       "capID3": "000QA"},
            "sections": ["Schedule an Inspection", "Record Info", "Payments",
                         "Attachments"],
            "expiration_date": "01/31/2026",
            "inspections": [],
            "created": "2026-09-19 via scripts/ni_apply_submit.py",
        },
        notes=("Sign - Temporary. First record from the full apply flow; the "
               "simplest target (no PROJECT DATES section, no payment gate)."),
    ),
    KnownRecord(
        permit_id="BLD26-00466",
        address="77 Licet Eval Way, Null Island 00001",
        expected_status="Submitted",
        expected_state={
            "cap_type": "Building/Sign/Temporary/NA",
            "module": "Building",
            "capids": {"capID1": "REC26", "capID2": "00000",
                       "capID3": "000Q9"},
            "sections": ["Schedule an Inspection", "Record Info", "Payments",
                         "Attachments"],
            "expiration_date": "01/31/2026",
            "inspections": [],
            "created": "2026-09-19 via scripts/ni_apply_submit.py",
        },
        notes=("Duplicate Sign - Temporary from an earlier submit run — spare "
               "slot for destructive evals (cancel/reschedule) so the primary "
               "record stays pristine."),
    ),
    KnownRecord(
        permit_id="000000014",
        address="81 Commerce Ave, Null Island 00001",
        expected_status="Submitted",
        expected_state={
            "cap_type": "Building/Commercial/Alteration/NA",
            "module": "Building",
            "capids": {"capID1": "REC26", "capID2": "00000",
                       "capID3": "000QB"},
            "sections": ["Schedule an Inspection", "Record Info", "Payments",
                         "Attachments"],
            "expiration_date": "01/31/2026",
            "inspections": [],
            "created": "2026-09-20 via scripts/ni_apply_batch.py (comm_alt)",
        },
        notes=("Commercial Alteration — flagship read target, but note the "
               "NON-BLD26 altID (000000014): this type renders a legacy "
               "numeric record number while others render BLD26-004xx. Also "
               "the only record whose grid Description cell is empty (its "
               "description was not captured on the first pass) — good "
               "negative case for 'description present'/parsing evals."),
    ),
    KnownRecord(
        permit_id="BLD26-00468",
        address="83 Commerce Ave, Null Island 00001",
        expected_status="Submitted",
        expected_state={
            "cap_type": "Building/Residential/Addition/NA",
            "module": "Building",
            "capids": {"capID1": "REC26", "capID2": "00000",
                       "capID3": "000QC"},
            "sections": ["Schedule an Inspection", "Record Info", "Payments",
                         "Attachments"],
            "expiration_date": "01/31/2026",
            "inspections": [],
            "created": "2026-09-20 via scripts/ni_apply_batch.py (res_add)",
        },
        notes="Residential Addition — homeowner/contractor permitting workflow.",
    ),
    KnownRecord(
        permit_id="BLD26-00469",
        address="85 Commerce Ave, Null Island 00001",
        expected_status="Submitted",
        expected_state={
            "cap_type": "Building/Commercial/Electrical/NA",
            "module": "Building",
            "capids": {"capID1": "REC26", "capID2": "00000",
                       "capID3": "000QD"},
            "sections": ["Schedule an Inspection", "Record Info", "Payments",
                         "Attachments"],
            "expiration_date": "01/31/2026",
            "inspections": [],
            "created": "2026-09-20 via scripts/ni_apply_batch.py (comm_elec)",
        },
        notes=("Commercial Electrical — trade permit; its AppSpec section "
               "added New Floor Area (sqft) / Estimated Cost ($) fields."),
    ),
    KnownRecord(
        permit_id="BLD26-00470",
        address="87 Commerce Ave, Null Island 00001",
        expected_status="Submitted",
        expected_state={
            "cap_type": "Building/Residential/New/SFR",
            "module": "Building",
            "capids": {"capID1": "REC26", "capID2": "00000",
                       "capID3": "000QE"},
            "sections": ["Schedule an Inspection", "Record Info", "Payments",
                         "Attachments"],
            "expiration_date": "01/31/2026",
            "inspections": [],
            "created": "2026-09-20 via scripts/ni_apply_batch.py (new_sfr)",
        },
        notes=("New Single Family Residence — multi-stage permit, expected "
               "richest inspection set once scheduled."),
    ),
    KnownRecord(
        permit_id="BLD26-00471",
        address="89 Commerce Ave, Null Island 00001",
        expected_status="Submitted",
        expected_state={
            "cap_type": "Building/Solar/NA/NA",
            "module": "Building",
            "capids": {"capID1": "REC26", "capID2": "00000",
                       "capID3": "000QF"},
            "sections": ["Schedule an Inspection", "Record Info", "Payments",
                         "Attachments"],
            "expiration_date": "01/31/2026",
            "inspections": [],
            "created": "2026-09-20 via scripts/ni_apply_batch.py (solar)",
        },
        notes=("Solar Permit — modern contractor use case; its AppSpec asked "
               "for modules / inverters / roof area / % covered."),
    ),
    KnownRecord(
        permit_id="BLD26-00472",
        address="91 Commerce Ave, Null Island 00001",
        expected_status="Submitted",
        expected_state={
            "cap_type": "Building/Right of Way/NA/NA",
            "module": "Building",
            "capids": {"capID1": "REC26", "capID2": "00000",
                       "capID3": "000QG"},
            "sections": ["Schedule an Inspection", "Record Info", "Payments",
                         "Attachments"],
            "expiration_date": "01/31/2026",
            "inspections": [],
            "created": "2026-09-20 via scripts/ni_apply_batch.py (row_use)",
        },
        notes=("Right of Way Use Permit — proves Licet generalizes beyond "
               "ordinary building permits. Its per-type 'PROJECT DATES' "
               "section (Schedule Start Date / Estimated Completion Date, "
               "both required masked dates) is what stalled the first "
               "attempt; now handled generically."),
    ),
]
