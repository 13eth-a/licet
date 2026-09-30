from licet.eval.phase3 import Phase3Case, score_cases
from licet.phase3.extract import extract_partial_state, merge_partial_states, normalize_lifecycle, normalize_permit_status, normalize_result
from licet.phase3.reasoning import understand
from licet.phase3.routing import needed_sections, route_question
from licet.phase3.state import CoverageStatus, PermitState, Section


def page(section, **values):
    return {"record_key": "A/Building/1/2/3", "section": section, "coverage": "complete", **values}


def test_status_normalization_does_not_promote_negative_or_configured_words():
    assert normalize_permit_status("Issued") == "ISSUED"
    assert normalize_permit_status("Not Issued") is None
    assert normalize_permit_status("Unexpired") is None
    assert normalize_permit_status("Inactive") is None


def test_inspection_dimensions_are_independent():
    assert normalize_lifecycle("Completed") == "COMPLETED"
    assert normalize_result("Completed") is None
    assert normalize_lifecycle("Not Scheduled") == "PENDING"
    assert normalize_result("Not Approved") == "FAILED"
    assert normalize_result("Corrections Required") == "FAILED"


def test_overview_extracts_raw_and_normalized_state_with_evidence():
    state = extract_partial_state(page("overview", fields={"record_number": "P-1", "record_type": "Electrical", "status": "Issued", "address": "1 Main"}))
    assert state.record_number == "P-1" and state.status_normalized == "ISSUED"
    assert state.facts[0].evidence_ids and state.evidence


def test_inspection_comment_stays_linked_to_its_attempt():
    state = extract_partial_state(page("inspections", rows=[{"id": "i1", "type": "Rough Electrical", "status": "Completed", "result": "Corrections Required", "comments": "Enclose box"}]))
    item = state.inspections[0]
    assert item.comment_evidence_ids == item.evidence_ids
    assert item.comments == "Enclose box" and item.failed


def test_empty_complete_section_is_not_an_extraction_error():
    state = extract_partial_state(page("inspections", rows=[]))
    assert state.coverage["inspections"].status == CoverageStatus.EXPLICITLY_EMPTY
    assert state.inspections == []


def test_loading_section_is_not_empty():
    state = extract_partial_state(page("inspections", coverage="loading", rows=[]))
    assert state.coverage["inspections"].status == CoverageStatus.LOADING


def test_fee_amount_and_payment_state_are_preserved():
    state = extract_partial_state(page("fees", rows=[{"name": "Permit fee", "amount": "$74.50", "paid": False, "due": True}]))
    assert state.fees[0].amount == 74.5 and state.fees[0].paid is False


def test_document_status_and_downloadability_are_preserved():
    state = extract_partial_state(page("documents", rows=[{"name": "plans.pdf", "type": "Plan", "status": "Approved", "downloadable": True}]))
    assert state.documents[0].downloadable and state.documents[0].status == "Approved"


def test_condition_stage_is_preserved():
    state = extract_partial_state(page("conditions", rows=[{"description": "Payment required before issuance", "status": "Active", "affects_stage": "issuance"}]))
    assert state.conditions[0].affects_stage == "issuance"


def test_history_events_keep_effective_dates():
    state = extract_partial_state(page("history", rows=[{"event": "Permit expired", "date": "2026-09-20", "details": "explicit"}]))
    assert state.history[0].date == "2026-09-20"


def test_merge_accumulates_sections_without_overwrite():
    base = extract_partial_state(page("overview", fields={"record_number": "P-1", "status": "Submitted"}))
    merged = merge_partial_states(base, extract_partial_state(page("fees", rows=[{"name": "Fee", "amount": 5, "paid": False}])))
    assert merged.status == "Submitted" and len(merged.fees) == 1 and "fees" in merged.coverage


def test_merge_rejects_foreign_record():
    base = extract_partial_state(page("overview", fields={"record_number": "P-1"}))
    other = extract_partial_state({**page("fees", rows=[{"name": "foreign"}]), "record_key": "OTHER"})
    merge_partial_states(base, other)
    assert base.fees == [] and base.rejected_observations
    # A rejected observation is a data-hygiene event, not a record-state
    # conflict: it must not flip answerability to "conflicting" (oracle U04).
    assert base.contradictions == []


def test_route_status_only_to_overview():
    assert route_question("What is the current status?").sections == ("overview",)


def test_route_inspector_question_to_inspections():
    assert route_question("What did the inspector say?").sections == ("inspections",)


def test_route_balance_only_to_fees():
    assert route_question("Are there unpaid fees?").sections == ("fees",)


def test_route_missing_plans_to_documents():
    assert route_question("Are the plans missing?").sections == ("documents",)


def test_route_stalled_permit_to_conditions_and_history():
    assert route_question("Why is approval blocked?").sections == ("conditions", "history")


def test_needed_sections_does_not_request_already_covered_section():
    state = extract_partial_state(page("overview", fields={"status": "Issued"}))
    assert needed_sections("What is the status?", state) == []


def test_failed_inspection_is_observed_problem_not_execution():
    state = extract_partial_state(page("inspections", rows=[{"type": "Rough", "result": "Failed", "status": "Completed"}]))
    result = understand(state, "Why did it fail?")
    assert any(x.type == "failed_inspection" for x in result.blockers)
    assert result.execution_allowed is False


def test_failed_inspection_comment_supports_conditional_next_action():
    state = extract_partial_state(page("inspections", rows=[{"type": "Rough", "result": "Corrections Required", "comments": "Enclose box"}]))
    result = understand(state, "What should happen next?")
    assert any("Enclose box" in x.reason for x in result.next_actions)


def test_unpaid_fee_without_gate_is_only_potential():
    state = extract_partial_state(page("fees", rows=[{"name": "Fee", "amount": 74.5, "paid": False, "due": True}]))
    result = understand(state, "What is blocking approval?")
    fee = next(x for x in result.blockers if x.type == "unpaid_fee")
    assert fee.classification == "potential_impediment"


def test_unpaid_fee_with_explicit_gate_is_confirmed():
    state = merge_partial_states(extract_partial_state(page("fees", rows=[{"name": "Fee", "amount": 74.5, "paid": False, "due": True, "gate_text": "Pay before issuance"}])), extract_partial_state(page("conditions", rows=[])))
    fee = understand(state, "What is blocking approval?").blockers[0]
    assert fee.classification == "confirmed_gate"


def test_offered_inspection_is_not_required():
    state = PermitState(record_key="A", record_number="P", status="Issued")
    state.coverage["inspections"] = __import__("licet.phase3.state", fromlist=["Coverage"]).Coverage(CoverageStatus.COMPLETE)
    state.facts.append(__import__("licet.phase3.state", fromlist=["Fact"]).Fact("offered_type", "Electrical Final"))
    result = understand(state, "What inspection is next?")
    assert not result.next_actions


def test_completed_without_result_is_not_passed():
    state = extract_partial_state(page("inspections", rows=[{"type": "Final", "status": "Completed"}]))
    assert state.inspections[0].lifecycle_normalized == "COMPLETED"
    assert not state.inspections[0].passed and not state.inspections[0].failed


def test_cancelled_inspection_is_not_failure():
    state = extract_partial_state(page("inspections", rows=[{"type": "Final", "status": "Cancelled", "result": ""}]))
    assert not understand(state, "What is blocking approval?").blockers


def test_pass_resolves_failure_only_when_scope_matches():
    state = extract_partial_state(page("inspections", rows=[{"type": "Rough", "scope": "Unit A", "result": "Failed"}, {"type": "Rough", "scope": "Unit B", "result": "Passed"}]))
    assert any(x.type == "failed_inspection" for x in understand(state, "What blocks it?").blockers)


def test_ambiguous_pass_without_scope_creates_uncertainty():
    state = extract_partial_state(page("inspections", rows=[{"type": "Rough", "result": "Failed"}, {"type": "Rough", "result": "Passed"}]))
    assert understand(state, "What blocks it?").uncertainties


def test_explicit_expired_status_is_blocker():
    state = extract_partial_state(page("overview", fields={"status": "Expired"}))
    assert any(x.type == "expired_permit" for x in understand(state, "What blocks it?").blockers)


def test_past_date_on_submitted_is_not_expired():
    state = extract_partial_state(page("overview", fields={"status": "Submitted", "expiration_date": "2020-01-01"}))
    assert not understand(state, "What blocks it?").blockers


def test_unavailable_section_requests_more_data_not_blocker():
    state = extract_partial_state(page("fees", coverage="unavailable", rows=[]))
    result = understand(state, "Are there unpaid fees?")
    assert result.answerability == "needs_data" and not result.blockers


def test_contradictory_overview_status_is_retained():
    state = extract_partial_state(page("overview", fields={"status": "Issued"}))
    merge_partial_states(state, extract_partial_state(page("overview", fields={"status": "Expired"})))
    result = understand(state, "What is the current status?")
    assert result.answerability == "conflicting" and result.contradictions


def test_raw_status_is_retained_alongside_normalization():
    state = extract_partial_state(page("overview", fields={"status": "Corrections Required"}))
    assert state.status == "Corrections Required" and state.status_normalized is None


def test_reasoning_claims_cite_existing_evidence():
    state = extract_partial_state(page("overview", fields={"status": "Issued"}))
    result = understand(state, "What is the status?")
    assert result.claims and all(eid in state.evidence for eid in result.claims[0].evidence_ids)


def test_reasoning_never_enables_execution():
    result = understand(PermitState(record_key="A"), "What next?")
    assert result.execution_allowed is False


def test_phase3_evaluator_reports_zero_unsupported_blockers():
    state = extract_partial_state(page("fees", rows=[{"name": "Fee", "amount": 5, "paid": False}]))
    report = score_cases([Phase3Case("B01", "What blocks it?", state, ("unpaid_fee",), (), None)])
    assert report["passed"] == 1 and report["unsupported_blocker_count"] == 0
