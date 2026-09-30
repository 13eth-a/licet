"""Golden-state fixtures and Phase 3 eval cases.

The interpretation oracles are Astra's 30 worked decisions
(``docs/phase3/reasoning_cases.md``): each case below binds one structured
permit state to the verdict the contract requires — including the *forbidden*
conclusions, because a hedged restatement of an unsupported claim is still an
unsupported claim. Extraction quality and reasoning quality are scored
separately (contract: separate extraction errors from reasoning errors), so
golden reasoning cases run on verified structured inputs while extraction
cases run page payloads through the adapter first.

Ground truth is data, not prose: ``GOLDEN_STATES`` records the manually
verified state per synthetic record, and the flagship case asserts the exact
shape the Phase 3 exit condition demands — facts stated, inference labeled by
strength, unsupported requirement abstained from, nothing executed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from licet.eval.phase3 import Phase3Case

from licet.phase3.extract import extract_partial_state, merge_partial_states
from licet.phase3.state import PermitState


@dataclass(frozen=True)
class GoldenRecord:
    """Manually recorded ground truth for one synthetic permit."""

    record_key: str
    record_number: str
    status: str
    failed_inspections: tuple[str, ...] = ()
    unpaid_balance: float | None = None
    next_likely_action: str | None = None


GOLDEN_STATES: dict[str, GoldenRecord] = {
    "flagship-001": GoldenRecord(
        record_key="NULLISLAND/Building/REC26/00000/9F001",
        record_number="BLD-GOLD-001",
        status="Issued",
        failed_inspections=("Rough Electrical",),
        unpaid_balance=74.50,
        next_likely_action="Reinspect Rough Electrical",
    ),
    "golden-002": GoldenRecord(
        record_key="NULLISLAND/Building/REC26/00000/9F002",
        record_number="BLD-GOLD-002",
        status="Submitted",
    ),
    "golden-003": GoldenRecord(
        record_key="NULLISLAND/Building/REC26/00000/9F003",
        record_number="BLD-GOLD-003",
        status="Issued",
        failed_inspections=("Rough Electrical",),
    ),
    "golden-004": GoldenRecord(
        record_key="NULLISLAND/Building/REC26/00000/9F004",
        record_number="BLD-GOLD-004",
        status="Expired",
    ),
    "golden-005": GoldenRecord(
        record_key="NULLISLAND/Building/REC26/00000/9F005",
        record_number="BLD-GOLD-005",
        status="Issued",
        failed_inspections=("Rough Electrical",),
        unpaid_balance=74.50,
    ),
}


# ---------------------------------------------------------------------------
# Per-municipality extraction fixtures.
#
# Reasoning cases above run on *verified structured inputs*. These run a real
# ``read_page`` payload through the ACA adapter first, so extraction errors stay
# separable from reasoning errors (reasoning contract: "separate extraction
# errors from reasoning errors by running reasoning both on verified golden
# structured inputs and extracted inputs"). Agencies configure their own section
# column labels; the fees grid was the named first gap (runtime_integration.md),
# so each municipal wording below is a header variant that must resolve to the
# same canonical row contract rather than degrading to a phantom "Unnamed fee".
# ---------------------------------------------------------------------------

PAGE_URL = (
    "https://aca-test.accela.com/NULLISLAND/Cap/CapDetail.aspx?Module=Building"
    "&capID1=REC26&capID2=00000&capID3=9F101&agencyCode=NULLISLAND"
)
PAGE_RECORD_KEY = "NULLISLAND/Building/REC26/00000/9F101"


@dataclass(frozen=True)
class ExtractionFixture:
    """One per-municipality page shape and what it must extract."""

    fixture_id: str
    municipality: str
    section: str
    text: str
    # canonical dataclass fields -> required value on the first extracted row
    first_row: dict[str, Any]
    count: int = 1
    question: str | None = None
    expected_blocker_types: tuple[str, ...] = ()
    forbidden_blocker_types: tuple[str, ...] = ()
    must_mention: tuple[str, ...] = ()
    must_not_claim: tuple[str, ...] = ()


ACA_PAGE_FIXTURES: tuple[ExtractionFixture, ...] = (
    ExtractionFixture(
        "X01-fees-grid-northgate",
        "Northgate",
        "fees",
        "Fees\nFee Type | Fee Amount | Balance Due | Payment Status\n"
        "Permit fee | $74.50 | $74.50 | Unpaid",
        {"description": "Permit fee", "amount": 74.5, "balance": 74.5, "paid": False, "due": True},
        question="Are there unpaid fees?",
        expected_blocker_types=("unpaid_fee",),
        must_mention=("$74.50",),
    ),
    ExtractionFixture(
        "X02-fees-grid-riverton",
        "Riverton",
        "fees",
        "Payments\nDescription | Amount Due | Status\nPlan review | $120.00 | Unpaid",
        {"description": "Plan review", "amount": 120.0, "paid": False, "due": True},
    ),
    ExtractionFixture(
        "X03-documents-grid-northgate",
        "Northgate",
        "documents",
        "Attachments\nFile Name | Category | Status | Date Uploaded\n"
        "plans.pdf | Plan | Approved | 09/01/2026",
        {"name": "plans.pdf", "type": "Plan", "status": "Approved", "date": "09/01/2026"},
    ),
    ExtractionFixture(
        "X04-documents-required-riverton",
        "Riverton",
        "documents",
        "Attachments\nDocument | Type | Status | Required\n"
        "Revised plans | Plan | Missing | Yes",
        {"name": "Revised plans", "status": "Missing", "required": True},
        question="What is blocking approval?",
        expected_blocker_types=("missing_required_document",),
    ),
    ExtractionFixture(
        "X05-inspections-grid-northgate",
        "Northgate",
        "inspections",
        "Inspections\nInspection Type | Status | Result | Completed Date\n"
        "Rough Electrical | Completed | Corrections Required | 09/18/2026",
        {
            "type": "Rough Electrical",
            "lifecycle_normalized": "COMPLETED",
            "result_normalized": "FAILED",
            # Portal MM/DD/YYYY is normalized to ISO at extraction (GLM Phase 5
            # review H02) so downstream attempt ordering sees comparable dates.
            "completed_date": "2026-09-18",
        },
        question="Why is this permit not moving forward?",
        expected_blocker_types=("failed_inspection",),
        must_mention=("Rough Electrical",),
    ),
    ExtractionFixture(
        "X06-inspections-action-column",
        "Northgate",
        "inspections",
        # a trailing link/control column must not defeat header recognition
        "Inspections\nInspection Type | Status | Result | Action\n"
        "Rough Electrical | Completed | Corrections Required | Edit",
        {"type": "Rough Electrical", "result_normalized": "FAILED"},
    ),
    ExtractionFixture(
        "X07-history-grid-riverton",
        "Riverton",
        "history",
        "Record History\nDate | Action | Status | Comments\n"
        "09/20/2026 | Permit expired | Complete | expired",
        {"event": "Permit expired", "date": "09/20/2026", "status": "Complete"},
    ),
    ExtractionFixture(
        "X08-conditions-grid-northgate",
        "Northgate",
        "conditions",
        "Conditions\nCondition | Status | Severity | Affects Stage\n"
        "Payment required before issuance | Active | High | issuance",
        {"description": "Payment required before issuance", "status": "Active", "affects_stage": "issuance"},
    ),
)


def _fixture_page(fixture: ExtractionFixture) -> dict[str, Any]:
    return {
        "record_key": PAGE_RECORD_KEY,
        "section": fixture.section,
        "coverage": "complete",
        "url": PAGE_URL,
        "text": fixture.text,
    }


def run_extraction_fixtures() -> list[dict[str, Any]]:
    """Page payload -> adapter -> partial state, checked against each fixture.

    Returns one row per fixture with ``passed`` and human-readable ``problems``,
    so the eval harness and CI score extraction the same way reasoning is scored.
    """
    from licet.phase3 import accela_extract
    from licet.phase3.extract import extract_partial_state
    from licet.phase3.reasoning import understand
    from licet.phase3.render import render_answer

    bucket_for = {
        "fees": lambda s: s.fees,
        "documents": lambda s: s.documents,
        "inspections": lambda s: s.inspections,
        "conditions": lambda s: s.conditions,
        "history": lambda s: s.history,
    }
    rows: list[dict[str, Any]] = []
    for fixture in ACA_PAGE_FIXTURES:
        observation = accela_extract.observation_for(fixture.section, _fixture_page(fixture))
        state = extract_partial_state(observation)
        items = bucket_for[fixture.section](state)
        problems: list[str] = []
        if len(items) != fixture.count:
            problems.append(f"expected {fixture.count} row(s), extracted {len(items)}")
        elif items:
            first = items[0]
            for field, wanted in fixture.first_row.items():
                actual = getattr(first, field, None)
                if actual != wanted:
                    problems.append(f"{field}: expected {wanted!r}, got {actual!r}")
        if observation["coverage"] not in {"complete", "explicitly_empty"}:
            problems.append(f"coverage degraded to {observation['coverage']!r}")
        if fixture.question is not None:
            result = understand(state, fixture.question)
            actual_types = {blocker.type for blocker in result.blockers}
            for wanted in fixture.expected_blocker_types:
                if wanted not in actual_types:
                    problems.append(f"missing blocker {wanted!r}")
            for forbidden in fixture.forbidden_blocker_types:
                if forbidden in actual_types:
                    problems.append(f"unsupported blocker {forbidden!r}")
            answer = render_answer(result)
            for part in fixture.must_mention:
                if part.lower() not in answer.lower():
                    problems.append(f"answer does not mention {part!r}")
            for claim in fixture.must_not_claim:
                if claim.lower() in answer.lower():
                    problems.append(f"answer asserts forbidden {claim!r}")
        rows.append(
            {
                "fixture_id": fixture.fixture_id,
                "municipality": fixture.municipality,
                "section": fixture.section,
                "passed": not problems,
                "problems": problems,
            }
        )
    return rows


def _page(record_key: str, section: str, **values: Any) -> dict[str, Any]:
    return {"record_key": record_key, "section": section, "coverage": "complete", **values}


def flagship_state() -> PermitState:
    """The Phase 3 flagship record, as verified golden structured input.

    Issued permit; latest relevant Rough Electrical attempt failed Sept 18 with
    a linked inspector comment; $74.50 unpaid balance with no gate evidence.
    """
    key = GOLDEN_STATES["flagship-001"].record_key
    overview = extract_partial_state(
        _page(key, "overview", fields={"record_number": "BLD-GOLD-001", "status": "Issued", "record_type": "Commercial Alteration", "address": "1 Flagship Way"})
    )
    inspections = extract_partial_state(
        _page(
            key,
            "inspections",
            rows=[
                {"id": "A", "type": "Rough Electrical", "status": "Completed", "result": "Corrections Required", "completed_date": "2026-09-18", "comments": "Enclose exposed junction box."},
            ],
        )
    )
    fees = extract_partial_state(
        _page(key, "fees", rows=[{"name": "Permit balance", "amount": "$74.50", "paid": False, "due": True}])
    )
    return merge_partial_states(merge_partial_states(overview, inspections), fees)


def _reinspection_scheduled_state() -> PermitState:
    key = GOLDEN_STATES["golden-005"].record_key
    overview = extract_partial_state(
        _page(key, "overview", fields={"record_number": "BLD-GOLD-005", "status": "Issued"})
    )
    inspections = extract_partial_state(
        _page(
            key,
            "inspections",
            rows=[
                {"id": "A", "type": "Rough Electrical", "status": "Completed", "result": "Corrections Required", "completed_date": "2026-09-18"},
                {"id": "B", "type": "Rough Electrical", "status": "Scheduled", "scheduled_date": "2026-09-25"},
            ],
        )
    )
    fees = extract_partial_state(
        _page(key, "fees", rows=[{"name": "Permit balance", "amount": "$74.50", "paid": False, "due": True}])
    )
    return merge_partial_states(merge_partial_states(overview, inspections), fees)


def _passed_after_failure_state() -> PermitState:
    """Same scope, dated later pass: the earlier failure is history, not current."""
    key = GOLDEN_STATES["golden-003"].record_key
    overview = extract_partial_state(
        _page(key, "overview", fields={"record_number": "BLD-GOLD-003", "status": "Issued"})
    )
    inspections = extract_partial_state(
        _page(
            key,
            "inspections",
            rows=[
                {"id": "A", "type": "Rough Electrical", "scope": "Unit A", "status": "Completed", "result": "Corrections Required", "completed_date": "2026-09-18"},
                {"id": "B", "type": "Rough Electrical", "scope": "Unit A", "status": "Completed", "result": "Passed", "completed_date": "2026-09-20"},
            ],
        )
    )
    return merge_partial_states(overview, inspections)


def build_cases() -> list["Phase3Case"]:
    """All golden reasoning cases (run on verified structured inputs)."""
    from licet.eval.phase3 import Phase3Case

    cases: list[Phase3Case] = []
    add = cases.append
    flagship = flagship_state()
    scheduled = _reinspection_scheduled_state()
    resolved = _passed_after_failure_state()

    # --- status extraction (oracle S01–S05) ---------------------------------
    add(Phase3Case("S01", "What is the current status?", flagship, (), (), "answered",
                   must_mention=("Issued",), must_not_claim=("ready",)))
    add(Phase3Case("S02-submitted", "What is the status of golden-002?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "overview",
                                               fields={"record_number": "BLD-GOLD-002", "status": "Submitted", "expiration_date": "2020-01-01"})),
                   (), (), "answered", must_mention=("Submitted",),
                   must_not_claim=("expired",)))
    add(Phase3Case("S04-completed-no-result", "Did the final inspection pass?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "inspections",
                                               rows=[{"type": "Final", "status": "Completed"}])),
                   (), (), "partial",
                   must_not_claim=("passed", "failed")))
    add(Phase3Case("S05-negative-wording", "What is blocking approval?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "inspections",
                                               rows=[
                                                   {"type": "Rough Electrical", "status": "Not Scheduled"},
                                                   {"type": "Frame", "result": "Not Approved"},
                                               ])),
                   ("failed_inspection",), ("expired_permit",), "answered",
                   must_not_claim=("scheduled",)))
    # Oracle S03: both facts are retained and the conflict must be flagged,
    # never silently resolved by scrape order. Conflicting is the required
    # verdict; the answer must still cite the portal's status and demand
    # renewal evidence rather than pick a winner.
    add(Phase3Case("S03-expired-event", "What is the status?",
                   merge_partial_states(
                       extract_partial_state(_page(GOLDEN_STATES["golden-004"].record_key, "overview",
                                                   fields={"record_number": "BLD-GOLD-004", "status": "Issued"})),
                       extract_partial_state(_page(GOLDEN_STATES["golden-004"].record_key, "history",
                                                   rows=[{"event": "Permit expired", "date": "2026-09-20", "details": "explicit expiration event"}])),
                   ),
                   (), (), "conflicting",
                   must_mention=("Issued",),
                   must_not_claim=("currently valid",)))

    # --- inspection history (oracle H01–H05) --------------------------------
    # H01: the later pass resolves the failure for that scope, and the resolved
    # failure must NOT remain a blocker — but a blocker question whose route
    # sections (conditions/history) are unread stays provisional, so the
    # runtime can never imply "nothing blocks" from covered evidence alone.
    add(Phase3Case("H01-later-pass-same-scope", "What is blocking approval?", resolved, (), ("failed_inspection",), "partial",
                   must_not_claim=("currently failed",)))
    add(Phase3Case("H02-scheduled-followup", "What should happen next?", scheduled, ("unpaid_fee",), (), "answered",
                   must_not_claim=("schedule another", "book another")))
    add(Phase3Case("H04-different-unit", "What is blocking approval?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "inspections",
                                               rows=[
                                                   {"type": "Rough Electrical", "scope": "Unit A", "result": "Failed"},
                                                   {"type": "Rough Electrical", "scope": "Unit B", "result": "Passed"},
                                               ])),
                   ("failed_inspection",), (), "answered"))
    add(Phase3Case("H05-unknown-ordering", "What is blocking approval?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "inspections",
                                               rows=[
                                                   {"type": "Rough Electrical", "result": "Failed"},
                                                   {"type": "Rough Electrical", "result": "Passed"},
                                               ])),
                   ("failed_inspection",), (), "partial"))
    add(Phase3Case("H03-cancelled-followup", "What should happen next?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "inspections",
                                               rows=[
                                                   {"type": "Rough Electrical", "status": "Completed", "result": "Corrections Required"},
                                                   {"type": "Rough Electrical", "status": "Cancelled"},
                                               ])),
                   ("failed_inspection",), (), "answered",
                   must_not_claim=("passed",)))

    # --- failure and comments (oracle F01–F05) ------------------------------
    add(Phase3Case("F01-flagship", "Why is this permit not moving forward, and what needs to happen next?",
                   flagship, ("failed_inspection", "unpaid_fee"), (), "answered",
                   must_mention=("Enclose exposed junction box", "$74.50"),
                   must_not_claim=("must be paid before", "reinspection is mandatory")))
    add(Phase3Case("F02-failure-no-comment", "What did the inspector say?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "inspections",
                                               rows=[{"type": "Rough Electrical", "status": "Completed", "result": "Failed"}])),
                   ("failed_inspection",), (), "answered",
                   must_not_claim=("because", "due to")))
    add(Phase3Case("F03-conditional-comment", "What did the inspector say?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "inspections",
                                               rows=[{"type": "Final", "status": "Completed", "comments": "Okay to proceed after correction."}])),
                   (), (), "answered",
                   must_not_claim=("enclose", "junction", "electrical")))
    add(Phase3Case("F04-passed-with-correction-comment", "Is this ready to move forward?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "inspections",
                                               rows=[{"type": "Final", "status": "Completed", "result": "Passed", "comments": "Correction Required"}])),
                   (), (), "conflicting",
                   must_not_claim=("ready",)))
    add(Phase3Case("F05-unlinked-comment", "What did the inspector say?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "inspections",
                                               rows=[
                                                   {"type": "Rough Electrical", "result": "Failed"},
                                                   {"type": "Plumbing", "result": "Failed"},
                                               ],
                                               comments=["Electrical defect: exposed junction box."])),
                   (), (), "answered"))

    # --- blockers and requirements (oracle B01–B05) -------------------------
    add(Phase3Case("B01-unpaid-not-gate", "What is blocking approval?", flagship, ("failed_inspection", "unpaid_fee"), (), "answered",
                   must_not_claim=("payment required before", "must pay before")))
    add(Phase3Case("B02-explicit-payment-gate", "What is blocking approval?",
                   merge_partial_states(
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "fees",
                                                   rows=[{"name": "Permit balance", "amount": "$74.50", "paid": False, "due": True, "gate_text": "Payment required before issuance"}])),
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "conditions", rows=[])),
                   ),
                   ("unpaid_fee",), (), "answered"))
    add(Phase3Case("B03-partial-documents", "Are the revised plans missing?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "documents", coverage="partial", rows=[])),
                   (), (), "needs_data",
                   must_not_claim=("plans missing", "plans required", "revised plans are missing")))
    add(Phase3Case("B04-review-pending", "What is blocking approval?",
                   merge_partial_states(
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "overview",
                                                   fields={"record_number": "BLD-GOLD-002", "status": "Submitted"})),
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "documents",
                                                   rows=[{"name": "Revised plans", "type": "Plan", "status": "Pending review", "required": False}])),
                   ),
                   (), (), "partial",
                   must_not_claim=("review is late", "plans prevent all work")))
    add(Phase3Case("B05-multi-blocker-ranking", "Why is approval blocked?",
                   merge_partial_states(
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "conditions",
                                                   rows=[{"description": "Administrative hold: all inspections suspended", "status": "Active hold", "affects_stage": "inspections"}])),
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "inspections",
                                                   rows=[{"type": "Foundation", "result": "Failed"}])),
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "fees",
                                                   rows=[{"name": "Permit balance", "amount": "$20.00", "paid": False}])),
                   ),
                   ("active_condition", "failed_inspection", "unpaid_fee"), (), "answered"))

    # --- next actions and readiness (oracle N01–N05) ------------------------
    add(Phase3Case("N01-catalog-not-required", "What inspection is next?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "inspections",
                                               rows=[], offered_types=[{"name": "Electrical Final", "required": False}])),
                   (), (), "answered",
                   must_not_claim=("required",)))
    add(Phase3Case("N02-explicit-prerequisite", "What inspection is next?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "inspections",
                                               rows=[{"type": "Rough Electrical", "result": "Passed"}],
                                               offered_types=[{"name": "Rough Electrical", "required": False}, {"name": "Electrical Final", "required": True}])),
                   (), (), "answered"))
    # Oracle N03: readiness without a named target or prerequisite evidence is
    # UNKNOWN — the observed failure and its conditional candidates are
    # reported, but the verdict stays withheld (must_not_claim "ready").
    add(Phase3Case("N03-fee-not-readiness", "Is this ready for its next inspection?",
                   merge_partial_states(
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "inspections",
                                                   rows=[{"type": "Rough Electrical", "result": "Failed"}])),
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "fees", rows=[])),
                   ),
                   ("failed_inspection",), (), "partial",
                   must_not_claim=("ready",)))
    add(Phase3Case("N04-tied-requirements", "What should happen next?",
                   merge_partial_states(
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "documents",
                                                   rows=[{"name": "Revised plans", "type": "Plan", "status": "Missing", "required": True}])),
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "fees",
                                                   rows=[{"name": "Permit balance", "amount": "$74.50", "paid": False, "due": True, "gate_text": "Payment required before issuance"}])),
                       # the readiness rule needs conditions/history covered for a
                       # full "what next" verdict; provide them explicitly here
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "conditions", rows=[])),
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "history", rows=[])),
                   ),
                   ("missing_required_document", "unpaid_fee"), (), "answered",
                   must_not_claim=("a unique next step", "the only next step")))
    add(Phase3Case("N05-expired-with-scheduled-inspection", "What should happen next?",
                   merge_partial_states(
                       extract_partial_state(_page(GOLDEN_STATES["golden-004"].record_key, "overview",
                                                   fields={"record_number": "BLD-GOLD-004", "status": "Expired"})),
                       extract_partial_state(_page(GOLDEN_STATES["golden-004"].record_key, "inspections",
                                                   rows=[{"type": "Final", "status": "Scheduled", "scheduled_date": "2026-09-30"}])),
                   ),
                   ("expired_permit",), (), "answered",
                   must_not_claim=("appointment proves", "renewal complete")))

    # --- missing data, conflict, isolation (oracle U01–U05) -----------------
    add(Phase3Case("U01-explicitly-empty", "Are there any inspections?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "inspections", rows=[])),
                   (), (), "answered",
                   must_not_claim=("no work required", "no requirements")))
    add(Phase3Case("U02-unavailable-tabs", "Why is this permit stalled?",
                   merge_partial_states(
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "fees", coverage="unavailable", rows=[])),
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "documents", coverage="parse_failed", rows=[])),
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "overview",
                                                   fields={"record_number": "BLD-GOLD-002", "status": "Submitted"})),
                   ),
                   (), (), "partial",
                   must_not_claim=("no fees", "no documents")))
    add(Phase3Case("U03-receipt-ambiguity", "Are there unpaid fees?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "fees",
                                               rows=[{"name": "Permit balance", "balance": "$74.50"}])),
                   (), (), "answered",
                   must_not_claim=("paid",)))
    add(Phase3Case("U05-covered-sections-clean", "Is this ready to move forward?",
                   merge_partial_states(
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "overview",
                                                   fields={"record_number": "BLD-GOLD-002", "status": "Submitted"})),
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "inspections", rows=[])),
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "fees", rows=[])),
                   ),
                   (), (), "partial",
                   must_not_claim=("ready", "no blockers")))
    add(Phase3Case("U04-foreign-record-rejected", "What is blocking approval?",
                   _foreign_record_state(),
                   (), (), "conflicting"))

    # --- adversarial regressions (DeepSeek review A1–A11) -------------------
    # Counterexamples reproduced against the inherited rules. Negative
    # assertions carry as much weight as expected ones here, and gate
    # classification is asserted directly rather than inferred from the type.
    add(Phase3Case("A01-negated-payment-gate", "What is blocking approval?",
                   merge_partial_states(
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "fees",
                                                   rows=[{"name": "Permit balance", "amount": "$74.50", "paid": False, "due": True}])),
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "conditions",
                                                   rows=[{"description": "No payment is required before issuance", "status": "Active"}])),
                   ),
                   ("unpaid_fee",), (), None,
                   must_not_claim=("Resolve outstanding fee", "required:"),
                   forbidden_classifications=(("unpaid_fee", "confirmed_gate"),)))
    # A real gate is kept, but the gated stage comes from the portal's wording
    # instead of defaulting to issuance.
    add(Phase3Case("A02-gate-stage-from-wording", "What is blocking approval?",
                   merge_partial_states(
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "fees",
                                                   rows=[{"name": "Permit balance", "amount": "$74.50", "paid": False, "due": True}])),
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "conditions",
                                                   rows=[{"description": "Payment required before final inspection", "status": "Active"}])),
                   ),
                   ("unpaid_fee",), (), None,
                   expected_classifications=(("unpaid_fee", "confirmed_gate"),),
                   must_not_claim=("affects: issuance",)))
    add(Phase3Case("A03-released-hold-is-not-a-gate", "What is blocking approval?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "conditions",
                                               rows=[{"description": "Administrative hold released", "status": "Hold released"}])),
                   (), ("active_condition",), None,
                   must_not_claim=("confirmed gate",)))
    # The latest attempt failed: an earlier pass must not resolve it away.
    add(Phase3Case("A04-later-failure-not-resolved", "Why is this permit not moving forward?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-003"].record_key, "inspections",
                                               rows=[
                                                   {"id": "A", "type": "Rough Electrical", "scope": "Unit A", "status": "Completed", "result": "Passed", "completed_date": "2026-09-18"},
                                                   {"id": "B", "type": "Rough Electrical", "scope": "Unit A", "status": "Completed", "result": "Failed", "completed_date": "2026-09-20"},
                                               ])),
                   ("failed_inspection",), (), "answered",
                   must_mention=("did not pass",)))
    add(Phase3Case("A05-clean-pass-with-negative-comment", "Is this ready to move forward?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "inspections",
                                               rows=[{"type": "Final", "status": "Completed", "result": "Passed", "comments": "No corrections required."}])),
                   (), (), None,
                   must_mention=("passed",),
                   expect_flags=(("contradictions", False),)))
    add(Phase3Case("A06-history-not-expired", "What is the status?",
                   merge_partial_states(
                       extract_partial_state(_page(GOLDEN_STATES["golden-004"].record_key, "overview",
                                                   fields={"record_number": "BLD-GOLD-004", "status": "Issued"})),
                       extract_partial_state(_page(GOLDEN_STATES["golden-004"].record_key, "history",
                                                   rows=[{"event": "Expiration date corrected; permit not expired", "date": "2026-09-20"}])),
                   ),
                   (), (), "answered",
                   expect_flags=(("contradictions", False),)))
    add(Phase3Case("A07-fee-payment-state-unknown", "Are there unpaid fees?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "fees",
                                               rows=[{"name": "Permit balance", "amount": "$74.50", "due": True}])),
                   (), ("unpaid_fee",), None,
                   must_not_claim=("remains unpaid",),
                   expect_uncertainty_on=("does not show whether it was paid",)))
    add(Phase3Case("A08-balance-not-amount", "Are there unpaid fees?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "fees",
                                               rows=[{"name": "Permit balance", "amount": "$74.50", "balance": "$100.00", "paid": False, "due": True}])),
                   ("unpaid_fee",), (), None,
                   must_mention=("$100.00",),
                   must_not_claim=("$74.50",)))
    # Two same-record reads disagree: the competition must surface, never be
    # silently decided by whichever read landed first.
    add(Phase3Case("A09-stale-fee-observation", "Are there unpaid fees?",
                   merge_partial_states(
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "fees",
                                                   rows=[{"name": "Permit balance", "amount": "$74.50", "paid": False, "due": True}])),
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "fees",
                                                   rows=[{"name": "Permit balance", "amount": "$74.50", "paid": True, "due": False}])),
                   ),
                   (), (), "conflicting",
                   expect_flags=(("contradictions", True),)))
    add(Phase3Case("A10-zero-balance-not-blocker", "Are there unpaid fees?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "fees",
                                               rows=[{"name": "Permit balance", "amount": "$0.00", "paid": False, "due": True}])),
                   (), ("unpaid_fee",), None))
    # "Pending" is not "missing": the requirement stays open as an uncertainty.
    add(Phase3Case("A11-required-document-pending", "What is blocking approval?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "documents",
                                               rows=[{"name": "Grading plan", "status": "Pending", "required": True}])),
                   (), ("missing_required_document",), None,
                   expect_uncertainty_on=("whether it satisfies the requirement",)))

    # --- requirement-strength correctness (contract's last open metric) -----
    # A failed inspection is an observed problem and supports a LIKELY
    # correction + a POSSIBLE reinspection, never a REQUIRED reinspection.
    add(Phase3Case(
        "RS01-failed-inspection-strength",
        "What should happen next?",
        flagship,
        ("failed_inspection", "unpaid_fee"),
        (),
        "answered",
        must_not_claim=("reinspection is mandatory", "must be paid before reinspection"),
        expected_strength=(("failed_inspection", "observed_problem",),),
        forbidden_strength=(("failed_inspection", "confirmed_gate"),),
    ))
    add(Phase3Case(
        "RS02-unpaid-without-gate-is-not-required",
        "What is blocking approval?",
        flagship,
        ("failed_inspection", "unpaid_fee"),
        (),
        "answered",
        must_not_claim=("payment required before", "must pay before"),
        expected_strength=(("unpaid_fee", "potential_impediment",),),
        forbidden_strength=(("unpaid_fee", "confirmed_gate"),),
    ))
    add(Phase3Case(
        "RS03-explicit-gate-is-required",
        "What is blocking approval?",
        merge_partial_states(
            extract_partial_state(
                _page(
                    GOLDEN_STATES["golden-002"].record_key,
                    "fees",
                    rows=[
                        {
                            "name": "Permit balance",
                            "amount": "$74.50",
                            "paid": False,
                            "due": True,
                            "gate_text": "Payment required before issuance",
                        }
                    ],
                )
            ),
            extract_partial_state(
                _page(GOLDEN_STATES["golden-002"].record_key, "conditions", rows=[])
            ),
        ),
        ("unpaid_fee",),
        (),
        "answered",
        expected_classifications=(("unpaid_fee", "confirmed_gate"),),
        expected_strength=(("unpaid_fee", "confirmed_gate",),),
        must_not_claim=("affects: final inspection",),
    ))

    # --- checklist: absent sections are coverage, not failure ----------------
    # "Test missing sections: no fees, no inspections, no comments, no documents,
    # no history. Make sure absence of data is not treated as failure."
    # (U01 already covers inspections; these add the other sections so the
    # observed-empty claim is exercised everywhere it can appear.)
    add(Phase3Case("U06-empty-fees", "Are there unpaid fees?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "fees", rows=[])),
                   (), (), "answered",
                   must_mention=("No fee entries are shown",),
                   must_not_claim=("unpaid", "outstanding")))
    add(Phase3Case("U07-empty-documents", "Are the revised plans missing?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "documents", rows=[])),
                   (), (), "answered",
                   must_mention=("No document entries are shown",),
                   must_not_claim=("plans are missing", "plans required")))
    add(Phase3Case("U08-empty-conditions-history", "What is blocking approval?",
                   merge_partial_states(
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "conditions", rows=[])),
                       extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "history", rows=[])),
                   ),
                   (), (), "answered",
                   must_mention=("No condition entries are shown", "No history entries are shown"),
                   must_not_claim=("hold", "expired")))
    add(Phase3Case("U09-inspections-no-comments", "What did the inspector say?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "inspections",
                                               rows=[{"type": "Rough Electrical", "status": "Completed", "result": "Failed"}])),
                   ("failed_inspection",), (), "answered",
                   must_not_claim=("because", "due to")))
    # "Test multi-blocker cases: missing document + pending review".
    add(Phase3Case("B06-missing-and-pending-document", "What is blocking approval?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "documents",
                                               rows=[
                                                   {"name": "Grading plan", "status": "Missing", "required": True},
                                                   {"name": "Revised plans", "status": "Pending review", "required": True},
                                               ])),
                   ("missing_required_document",), (), "answered",
                   expect_uncertainty_on=("whether it satisfies the requirement",),
                   must_not_claim=("review is late",)))
    # Agency label variants for conditions and documents (the handoff's
    # "next capture targets"): a real-world active hold label still gates, and
    # an explicit missing label still blocks; unknown labels stay uncertainties
    # (locked by tests/test_phase3_adversarial.py).
    add(Phase3Case("C01-hold-label-variant", "What is blocking approval?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "conditions",
                                               rows=[{"description": "Site work suspended", "status": "On Hold"}])),
                   ("active_condition",), (), "answered",
                   expected_classifications=(("active_condition", "confirmed_gate"),)))
    add(Phase3Case("D01-missing-document-label-variant", "What is blocking approval?",
                   extract_partial_state(_page(GOLDEN_STATES["golden-002"].record_key, "documents",
                                               rows=[{"name": "Grading plan", "status": "Not Uploaded", "required": True}])),
                   ("missing_required_document",), (), "answered"))

    # --- flagship acceptance cases ------------------------------------------
    add(Phase3Case("FLAGSHIP-STATUS", "What is the current status?", flagship, (), (), "answered",
                   must_mention=("Issued",)))
    add(Phase3Case("FLAGSHIP-NEXT", "What should happen next?", flagship, ("failed_inspection", "unpaid_fee"), (), "answered",
                   must_not_claim=("reinspection is mandatory", "must be paid before reinspection")))
    add(Phase3Case("FLAGSHIP-INSPECTOR", "What did the inspector say?", flagship, (), (), "answered",
                   must_mention=("Enclose exposed junction box",)))
    add(Phase3Case("FLAGSHIP-FEES", "Are there unpaid fees?", flagship, ("unpaid_fee",), (), "answered",
                   must_mention=("$74.50",)))
    add(Phase3Case("FLAGSHIP-EXECUTION-DISABLED", "Schedule the Rough Electrical reinspection now.", flagship,
                   (), (), "answered", must_not_claim=("scheduled", "booked", "payment submitted")))
    add(Phase3Case(
        "RS04-next-action-strength-multi-blocker",
        "What should happen next?",
        merge_partial_states(
            _reinspection_scheduled_state(),
        ),
        ("unpaid_fee",), (), "answered",
        must_not_claim=("schedule another", "book another"),
        expected_strength=(("unpaid_fee", "potential_impediment",),),
    ))

    return cases


def _foreign_record_state() -> PermitState:
    """Record A's state plus a rejected observation from record B (oracle U04)."""
    base = extract_partial_state(
        _page(GOLDEN_STATES["flagship-001"].record_key, "overview", fields={"record_number": "BLD-GOLD-001", "status": "Issued"})
    )
    foreign = extract_partial_state(
        _page("OTHER/Record/Key", "conditions", rows=[{"description": "Hold transferred from record B", "status": "Active hold"}])
    )
    merge_partial_states(base, foreign)
    return base
