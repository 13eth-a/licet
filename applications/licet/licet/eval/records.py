"""Known test permit records and their expected states.

Two tiers:

1. PUBLIC PROBE RECORDS — discovered via anonymous live-portal recon
   (2026-09-18). These prove search/deep-link reachability and give the
   planner something real to hit before sandbox credentials exist. Ground
   truth below was read off the rendered portal pages, not an API.
2. SANDBOX RECORDS — 8 records on Null Island, all applied for on the
   citizen portal with the public-user test account (2026-09-19/20) and
   read back from My Records. These are the scheduling-eval targets.

For each record, capture the ground-truth state needed to score eval runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class KnownRecord:
    permit_id: str
    address: str
    expected_status: str
    expected_state: dict[str, Any]  # e.g. inspections, fees, outstanding_requirements
    notes: str = ""


def record_for(permit_id: str) -> KnownRecord | None:
    """Look up a record by the id a prompt would name."""
    return next(
        (record for record in (*KNOWN_RECORDS, *PUBLIC_PROBE_RECORDS) if record.permit_id == permit_id),
        None,
    )


# SCHEDULING GROUND TRUTH (measured live 2026-09-20, scripts/ni_schedule_probe.py
# + scripts/ni_availability_sweep.py). This is the single most important eval
# fact on this environment, so it is data rather than prose:
#
#   NO RECORD ON THE NULL ISLAND SANDBOX HAS A BOOKABLE APPOINTMENT DATE.
#
# Six records reach the calendar; every day cell in all three rendered months
# (Sep/Oct/Nov 2026) is `CalendarDayInactive` with
# title="Cannot schedule inspection on this date" — 91 cells, 0 selectable in
# total. `lblAvaliableTimes` is empty and the popup's Continue stays disabled
# (real postback parked in `href_disabled`). Two records (New SFR, Right of Way)
# offer no inspection types at all.
#
# Likely cause: every owned record is **Submitted, not Issued**. ACA normally
# only lets a citizen book inspections on issued permits. So "schedule the
# earliest available inspection" is expected to end in an accurate can't-finish
# report on this environment, and an agent that claims to have booked a slot is
# wrong by definition. Re-measure before assuming this still holds.
#
# inspection_types are the first page of the wizard grid (`gvInspectionType`
# paginates at 10 rows); `declared_total` from the `Available Inspection Types
# (N)` heading is authoritative for the count.
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


# Probe records reachable without login.
#
# PLB-10-00951 — Omaha's own documented example permit number; confirmed
# visible in live search results through Solari (2026-09-18,
# scripts/solari_verify.py step submit_search). Real production record:
# READ-PATH evals only — never schedule against it (not ours, production portal).
#
# 22CAP-00000-006RZ — Meridian ID record surfaced via a deep link. Deep
# links render via curl but Meridian gates anonymous record views behind an
# "approved Address/Parcel Verification" in a real browser (live-verified).
# Deep-link smoke test only.
PUBLIC_PROBE_RECORDS: list[KnownRecord] = [
    KnownRecord(
        permit_id="PLB-10-00951",
        address="",  # read live from the record page on first run
        expected_status="unknown",  # read live; correctness scoring needs detail capture
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

# Null Island (aca-test.accela.com/nullisland) record discovery, round 1
# (2026-09-18, scripts/ni_find_records.py): 6 modules x 5 loosening
# strategies (street=Main/Test/1, permit wildcard) all returned 0 public
# rows via the anonymous ACA portal. Follow-up (2026-09-19,
# scripts/ni_record_inventory.py + scripts/ni_record_details.py) went
# through the BACK OFFICE (nullisland-test-av.accela.com, developer/accela)
# and captured the full sandbox inventory: 123 records across all 12
# modules. Full data: logs/ni_backoffice/inventory/merged_inventory.json.
#
# NULL ISLAND INVENTORY (2026-09-19) — grouped by cap type:
#   Building/Residential/Mechanical/NA            31  (BLD26-…)
#   Building/Commercial/Mechanical/NA             27  (BLD25/26-…)
#   ServiceRequest/…/Fence Dispute/NA             20  (SR26-…)
#   AMS/Facilities/Building/{Install,Maintain,Repair} 20 (26FAC-…)
#   Enforcement/Incident/…                        9  (ENF2x-…)
#   Fire/{Inspection,Sprinkler System}/NA/NA      5  (FIR26-…)
#   EnvHealth/{Food/Mobile,Rec-Health/Pool}/…     5  (EHA26-…)
#   Planning/Application/Site Plan/Major          1  (PLN26-00001)
#   Cannabis/Licenses/Tobacco/License             2  (…)
#   Licenses/Contractor/General/Application       1  (TEST-CBL-APP-…)
#
# MATCH VS LICET'S TARGET CATEGORIES (Commercial Alteration, Residential
# Addition, Commercial Electrical, New SFR, Solar, ROW Use, Sign-Temp):
# ZERO matches among EXISTING records. RESOLVED 2026-09-19 — that only meant
# no records OF those types exist yet. The configured type catalog (portal
# search-page dropdown, scripts/ni_type_catalog.py) contains ALL 7 targets
# in module Building (exact values below), so the plan stands: create them
# by APPLYING on the citizen portal with the public-user account. No
# back-office seeding, no eval-type remapping.
#
# Configured portal types (CapHome.aspx ddlGSPermitType, no login needed):
#   Building 102, AMS 314, EnvHealth 65, Licenses 72, Planning 22,
#   Enforcement 18; PublicWorks/Fire/ServiceRequest/Cannabis/Treasury
#   expose 0 via CapHome. Data: logs/ni_backoffice/inventory/*_catalog.json.
# Licet's 7 eval types (exact cap type values, all module=Building):
#   Commercial Alteration     -> Building/Commercial/Alteration/NA
#   Residential Addition      -> Building/Residential/Addition/NA
#   Commercial Electrical     -> Building/Commercial/Electrical/NA
#   New Single Family Res.    -> Building/Residential/New/SFR
#   Solar Permit              -> Building/Solar/NA/NA
#   Right of Way Use Permit   -> Building/Right of Way/NA/NA
#   Sign - Temporary          -> Building/Sign/Temporary/NA
# Bonus types in the same catalog: Residential Alteration, Residential
# Electrical, Residential New, Commercial New, Fence Permit,
# Commercial Plumbing, Commercial Re-Roof — useful as extra eval slots.
# NOTE: CapWiz-style apply URLs are 404 on NI; the apply flow must be
# entered through the portal UI after login (no deep link).
# Back-office access notes (verified 2026-09-19):
#   - Record grid: module-scoped pagination GETs work
#     (capSearch.do?pageNo=N&column=altID&module=<M>&spaceName=
#     spaces.nullisland.record&isGeneralCAP=Y); mode=search URLs are
#     CSRF-denied ("capSearchForm 8035R").
#   - Record detail: capDetail.do with explicit ID1/ID2/ID3 params renders
#     the full type in readonly input value(capType) — no clicking needed.
#   - The anonymous ACA portal still returns 0 rows for everything
#     (verified across all 12 modules via back-office record IDs).

# Null Island sandbox records (agreed 2026-09-19; created via application on
# the citizen portal, tied to the public-user test account in .env
# (ACCELA_TEST_USERNAME; registered + login-verified 2026-09-19).
#
# APPLY-FLOW MAP (verified 2026-09-19, scripts/ni_apply_submit.py — the
# Sign - Temporary application below was submitted end-to-end by script):
#   entry    CapApplyDisclaimer.aspx?module=Building&TabName=Building
#            &FilterName=PMT_GENERAL  (agree checkbox + btnNextStep)
#   type     CapType.aspx — radio inputs, value IS the cap type
#            (e.g. Building/Sign/Temporary/NA); check() fires SelectNode
#   form     CapEdit.aspx — required: Street No, Street Name, Zip (masked,
#            digits only). Continue = actionBarBottom_btnContinue.
#   contact  CapEdit pageNumber=2 — required Applicant contact via
#            [id$='Applicant_269Edit_btnAddNew'] which opens an IFRAME
#            overlay (People/ContactAddNew.aspx, frame title "Contact
#            Information Dialog"). Save = #ctl00_phPopup_btnSave INSIDE
#            the dialog frame (parent btnSave = save-&-resume-later trap).
#            Phones: leave EMPTY (mask rejects typed digits, optional);
#            Type select -> 'Applicant'; Name of Business required.
#   detail   CapEdit stepNumber=3 — DetailInfoEdit_txtDescriptionDetail
#            required; some types add custom tabs (Sign: display dates,
#            number of signs — optional).
#   review   CapConfirm.aspx stepNumber=5 — no agree checkbox on NI; the
#            final Continue issues the record immediately (no payment
#            gate for Sign - Temporary).
#   result   CapConfirm 'Record Issuance' page prints 'Your Record Number
#            is <ALTIID>'. Fees/inspection prompts are templated text.
# ALL 7 TARGET TYPES APPLIED FOR (2026-09-20, scripts/ni_apply_batch.py) and
# verified against My Records (scripts/ni_my_records.py --details). Only
# Right of Way needed a second pass: NI adds a per-type "PROJECT DATES"
# AppSpec section whose required masked MM/DD/YYYY fields (Schedule Start
# Date, Estimated Completion Date) have no hardcoded control id, so the
# batch script now resolves required-field values GENERICALLY —
# aria-required/title='Required' + placeholder/fieldname rather than id —
# and fills masked dates with real keystrokes. With that, every remaining
# type applied end-to-end unmodified. An extra Sign - Temporary
# (BLD26-00466) exists from the first submit run; kept as a spare slot.
#
# altID FORMAT IS PER-TYPE, not per-agency: Sign - Temporary/Addition/
# Electrical/SFR/Solar/ROW came back as BLD26-004xx while Commercial
# Alteration came back as 000000014 (9-digit legacy sequence) — yet it sits
# in the same capID sequence (REC26/00000/000QB) and reads back fine. Parse
# the "Your Record Number is <ID>" sentence; never a fixed pattern.
# Lifecycle plan: one early (fees pending), one with a FAILED inspection
# (needs back office or inspector-side state — AV host 1015-bans deep
# hits; retry on a fresh day), one with an inspection scheduled.
# Verified live 2026-09-20 from My Records (8 rows: "Showing 1-8 of 8"),
# grid columns Date / Record Number / Record Type / Project Name / Address /
# Status / Action / Description / Expiration Date / Short Notes. Every row
# also carries a site-absolute detail link
# (/NULLISLAND/Cap/CapDetail.aspx?Module=<M>&TabName=<M>&capID1/2/3
#  &agencyCode=NULLISLAND&IsToShowInspection=) — that is the record read
# path, and it renders for all 8 with the same section set:
#   Schedule an Inspection | Record Info | Payments | Attachments
# (previously unverifiable: the account owned no records). Deep-link base is
# the SITE root, not the agency path — /nullisland/NULLISLAND/... 404s.
# Full capture: logs/ni_backoffice/inventory/20260920T044644Z_my_records.json.
#
# Scheduling ground truth (no bookable dates, per-type inspection lists) lives
# in SCHEDULING_GROUND_TRUTH below, measured by scripts/ni_availability_sweep.py.
#
# Shared ground truth: status "Submitted", Expiration Date 01/31/2026,
# Work Location "<street> Commerce Ave / 00001", Applicant "Eval User /
# Licet Eval Testing LLC / <ACCELA_TEST_USERNAME>", description
# "Licet eval application - <type> for testing", owner = public-user test
# account (so scheduling + cancel evals are safe). No inspections scheduled
# on any record yet.
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
