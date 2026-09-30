"""phase 3 extraction fixtures: per municipality aca page shapes"""
from __future__ import annotations

from licet.eval import phase3_fixtures as fixtures
from licet.eval.phase3 import score_cases
from licet.phase3 import accela_extract
from licet.phase3.extract import extract_partial_state
from licet.phase3.reasoning import understand
from licet.phase3.render import render_answer


def _page(section: str, text: str) -> dict[str, object]:
    return {"record_key": fixtures.PAGE_RECORD_KEY, "section": section, "coverage": "complete",
            "url": fixtures.PAGE_URL, "text": text}


def test_every_per_municipality_extraction_fixture_passes():
    rows = fixtures.run_extraction_fixtures()
    assert len(rows) == len(fixtures.ACA_PAGE_FIXTURES)
    problems = {row["fixture_id"]: row["problems"] for row in rows if not row["passed"]}
    assert not problems, problems


def test_fixtures_cover_all_reasoning_sections_and_multiple_municipalities():
    sections = {fixture.section for fixture in fixtures.ACA_PAGE_FIXTURES}
    municipalities = {fixture.municipality for fixture in fixtures.ACA_PAGE_FIXTURES}
    assert {"fees", "documents", "inspections", "conditions", "history"} <= sections
    assert len(municipalities) >= 2


def test_fee_grid_header_variant_resolves_to_canonical_fields():
    """a \"fee type | fee amount | balance due | payment status\" grid now parses"""
    observation = accela_extract.fees_observation(_page("fees", fixtures.ACA_PAGE_FIXTURES[0].text))
    assert observation["rows"] == [
        {"description": "Permit fee", "amount": "$74.50", "balance": "$74.50",
         "paid": False, "due": True}
    ]
    state = extract_partial_state(observation)
    fee = state.fees[0]
    assert (fee.description, fee.amount, fee.paid, fee.due, fee.balance) == (
        "Permit fee", 74.5, False, True, 74.5,
    )


def test_inspection_grid_header_variant_keeps_type_and_completion_date():
    observation = accela_extract.inspections_observation(
        _page("inspections", "Inspection Type | Status | Result | Completed Date\n"
                             "Rough Electrical | Completed | Corrections Required | 09/18/2026")
    )
    inspection = extract_partial_state(observation).inspections[0]
    assert inspection.type == "Rough Electrical"
    assert inspection.lifecycle_normalized == "COMPLETED"
    assert inspection.result_normalized == "FAILED"
    assert inspection.completed_date == "2026-09-18"


def test_trailing_action_column_does_not_defeat_header_recognition():
    observation = accela_extract.inspections_observation(
        _page("inspections", "Inspection Type | Status | Result | Action\n"
                             "Rough Electrical | Completed | Corrections Required | Edit")
    )
    assert observation["coverage"] == "complete"
    assert extract_partial_state(observation).inspections[0].type == "Rough Electrical"


def test_unrecognized_header_degrades_to_partial_not_a_phantom_row():
    """wording licet cannot resolve stays honest partial coverage, never data"""
    observation = accela_extract.fees_observation(_page("fees", "Fees\nWidget | Gizmo\nThing | Other"))
    assert observation["rows"] == []
    assert observation["coverage"] == "partial"
    assert extract_partial_state(observation).fees == []


def test_absent_section_renders_as_coverage_not_an_unanswerable_void():
    state = extract_partial_state(
        {"record_key": "k", "section": "fees", "coverage": "complete", "rows": []}
    )
    answer = render_answer(understand(state, "Are there unpaid fees?"))
    assert "No fee entries are shown" in answer
    assert "cannot answer this" not in answer.lower()
    assert "unpaid" not in answer.lower()


def test_model_payload_carries_section_entities_and_coverage():
    """regression: the snapshot must include rows, not just overview scalars"""
    from licet.phase3.model_reasoning import _build_model_payload

    state = fixtures.flagship_state()
    payload = _build_model_payload(state, "What is blocking approval?", "snap-1")
    assert payload["sections"]["inspections"] and payload["sections"]["fees"]
    assert payload["coverage"]["inspections"]["status"]


def test_model_path_reproduces_the_deterministic_golden_set(monkeypatch):
    monkeypatch.setenv("LICET_PHASE3_MODEL_ENABLED", "1")
    from licet.phase3.model_reasoning import understand_one

    mismatches: list[str] = []
    for case in fixtures.build_cases():
        model = understand_one(case.state, case.question, snapshot_id=case.case_id, use_model=True)
        deterministic = understand(case.state, case.question, snapshot_id=case.case_id)
        if {b.type for b in model.blockers} != {b.type for b in deterministic.blockers}:
            mismatches.append(f"{case.case_id}: blockers")
        if model.answerability != deterministic.answerability:
            mismatches.append(f"{case.case_id}: answerability {model.answerability} != {deterministic.answerability}")
        if model.execution_allowed:
            mismatches.append(f"{case.case_id}: execution_allowed")
    assert not mismatches, mismatches


def test_checklist_gap_cases_are_present_and_the_whole_golden_set_passes():
    case_ids = {case.case_id for case in fixtures.build_cases()}
    assert {
        "U06-empty-fees",
        "U07-empty-documents",
        "U08-empty-conditions-history",
        "U09-inspections-no-comments",
        "B06-missing-and-pending-document",
        "C01-hold-label-variant",
        "D01-missing-document-label-variant",
    } <= case_ids
    report = score_cases(fixtures.build_cases())
    assert report["passed"] == report["total"]
    assert report["unsupported_blocker_count"] == 0
    assert report["gate_false_positives"] == 0
    assert report["false_ready_count"] == 0
