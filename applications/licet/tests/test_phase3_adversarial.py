"""Adversarial review regressions (DeepSeek, Phase 3).

Each test is a counterexample reproduced against the inherited Phase 3 rules.
The theme is the one the phase cannot get wrong: never state a requirement,
gate, or outcome the portal did not state — and never silently drop one the
portal did.

Golden-case equivalents live in ``licet/eval/phase3_fixtures.py`` (``A01``-
``A11``); these are the precise unit-level locks on the same predicates.
"""
from __future__ import annotations

from licet.eval.phase3 import Phase3Case, score_cases
from licet.phase3 import accela_extract
from licet.phase3.extract import extract_partial_state, merge_partial_states
from licet.phase3.reasoning import understand
from licet.phase3.render import render_answer
from licet.phase3.rules import (
    _attempt_order,
    _condition_activity,
    comment_requests_correction,
    explicit_expiration_event,
    format_fee_amount,
)
from licet.phase3.state import Condition, Inspection

KEY = "NULLISLAND/Building/REC26/00000/9F002"


def page(section, **values):
    return {"record_key": KEY, "section": section, "coverage": "complete", **values}


def blockers(state, question="What is blocking approval?"):
    return {blocker.type: blocker for blocker in understand(state, question).blockers}


# --- A1/A3: condition activity is an agency label, not a substring -----------


def test_resolved_condition_labels_are_inactive():
    for status in ("Hold released", "Satisfied", "Resolved", "Cleared", "Waived", "Complete"):
        assert _condition_activity(Condition("Admin hold", status=status)) == "inactive"


def test_unrecognized_condition_label_is_unknown_never_active():
    assert _condition_activity(Condition("Site plan", status="Under agency review")) == "unknown"
    assert _condition_activity(Condition("Site plan", status=None)) == "unknown"


def test_released_hold_is_neither_blocker_nor_gate():
    state = extract_partial_state(
        page("conditions", rows=[{"description": "Administrative hold released",
                                  "status": "Hold released"}])
    )
    result = understand(state, "What is blocking approval?")
    assert not result.blockers
    assert "confirmed gate" not in render_answer(result)


def test_active_hold_is_a_confirmed_gate():
    state = extract_partial_state(
        page("conditions", rows=[{"description": "Administrative hold", "status": "Active hold"}])
    )
    blocker = blockers(state)["active_condition"]
    assert blocker.classification == "confirmed_gate"


def test_unmapped_condition_label_becomes_uncertainty_not_blocker():
    state = extract_partial_state(
        page("conditions", rows=[{"description": "Grading bond", "status": "Escrowed"}])
    )
    result = understand(state, "What is blocking approval?")
    assert not result.blockers
    assert any("not a recognized agency label" in u.description for u in result.uncertainties)


# --- A2/A3: a payment gate is a portal statement, with a stated stage --------


def _fee_plus_condition(description, status="Active"):
    return merge_partial_states(
        extract_partial_state(page("fees", rows=[{"name": "Permit balance", "amount": "$74.50",
                                                  "paid": False, "due": True}])),
        extract_partial_state(page("conditions", rows=[{"description": description, "status": status}])),
    )


def test_negated_payment_wording_is_not_a_gate():
    state = _fee_plus_condition("No payment is required before issuance")
    fee = blockers(state)["unpaid_fee"]
    assert fee.classification == "potential_impediment"
    assert fee.affects_stage is None
    assert "Required:" not in render_answer(understand(state, "What is blocking approval?"))


def test_payment_gate_stage_comes_from_the_portal_wording():
    inspections = blockers(_fee_plus_condition("Payment required before final inspection"))["unpaid_fee"]
    assert inspections.classification == "confirmed_gate"
    assert inspections.affects_stage == "final inspection"
    issuance = blockers(_fee_plus_condition("Balance must be paid before issuance"))["unpaid_fee"]
    assert issuance.affects_stage == "issuance"


def test_payment_gate_without_a_named_stage_stays_unknown():
    state = _fee_plus_condition("Payment required before releasing the record")
    fee = blockers(state)["unpaid_fee"]
    assert fee.classification == "confirmed_gate"
    assert fee.affects_stage is None
    assert any("does not state which stage" in u.description
               for u in understand(state, "What is blocking approval?").uncertainties)


def test_negated_fee_row_gate_text_is_not_a_gate():
    state = extract_partial_state(
        page("fees", rows=[{"name": "Permit balance", "amount": "$74.50", "paid": False,
                            "due": True, "gate_text": "No payment required"}])
    )
    assert blockers(state)["unpaid_fee"].classification == "potential_impediment"


# --- A4: only a *later* pass resolves a failure ------------------------------


def test_attempt_order_requires_both_iso_dates():
    passed = Inspection("Rough", result="Passed", scope="Unit A", completed_date="2026-09-20")
    failed = Inspection("Rough", result="Failed", scope="Unit A", completed_date="2026-09-18")
    assert _attempt_order(failed, passed) == "later"
    assert _attempt_order(passed, failed) == "earlier"
    assert _attempt_order(failed, Inspection("Rough", result="Passed", scope="Unit A")) == "unknown"
    assert _attempt_order(failed, Inspection("Rough", result="Passed", scope="Unit B",
                                             completed_date="2026-09-20")) == "incomparable"
    assert _attempt_order(failed, Inspection("Rough", result="Passed", scope="Unit A",
                                             completed_date="Sept 20")) == "unknown"


def test_earlier_pass_does_not_resolve_a_later_failure():
    state = extract_partial_state(
        page("inspections", rows=[
            {"id": "A", "type": "Rough Electrical", "scope": "Unit A", "status": "Completed",
             "result": "Passed", "completed_date": "2026-09-18"},
            {"id": "B", "type": "Rough Electrical", "scope": "Unit A", "status": "Completed",
             "result": "Failed", "completed_date": "2026-09-20"},
        ])
    )
    result = understand(state, "Why is this permit not moving forward?")
    assert any(b.type == "failed_inspection" for b in result.blockers)
    assert any("Rough Electrical" in a.action for a in result.next_actions)
    assert "did not pass" in render_answer(result)


def test_later_pass_still_resolves_same_scope_failure():
    state = extract_partial_state(
        page("inspections", rows=[
            {"id": "A", "type": "Rough Electrical", "scope": "Unit A", "status": "Completed",
             "result": "Corrections Required", "completed_date": "2026-09-18"},
            {"id": "B", "type": "Rough Electrical", "scope": "Unit A", "status": "Completed",
             "result": "Passed", "completed_date": "2026-09-20"},
        ])
    )
    result = understand(state, "What is blocking approval?")
    assert not any(b.type == "failed_inspection" for b in result.blockers)


# --- A5/A6: negation in comment and history wording --------------------------


def test_comment_correction_matcher_respects_negation():
    assert comment_requests_correction("Corrections Required")
    assert comment_requests_correction("Correction Required")
    assert not comment_requests_correction("No corrections required.")
    assert not comment_requests_correction("Corrections complete.")
    assert not comment_requests_correction("Okay to proceed after correction.")


def test_clean_pass_with_negative_comment_is_not_a_conflict():
    state = extract_partial_state(
        page("inspections", rows=[{"type": "Final", "status": "Completed", "result": "Passed",
                                   "comments": "No corrections required."}])
    )
    result = understand(state, "Is this ready to move forward?")
    assert not result.contradictions
    assert result.answerability != "conflicting"


def test_expiration_event_matcher_respects_negation():
    assert explicit_expiration_event("Permit expired")
    assert not explicit_expiration_event("Expiration date corrected; permit not expired")
    assert not explicit_expiration_event("Renewed before expiration")


def test_not_expired_history_does_not_flip_answerability():
    state = merge_partial_states(
        extract_partial_state(page("overview", fields={"record_number": "BLD-GOLD-004",
                                                       "status": "Issued"})),
        extract_partial_state(page("history", rows=[{"event": "Permit not expired",
                                                     "date": "2026-09-20"}])),
    )
    result = understand(state, "What is the status?")
    assert not result.contradictions
    assert result.answerability == "answered"


# --- A7/A8/A10: money wording -------------------------------------------------


def test_unknown_payment_state_is_not_reported_as_unpaid():
    state = extract_partial_state(
        page("fees", rows=[{"name": "Permit balance", "amount": "$74.50", "due": True}])
    )
    result = understand(state, "Are there unpaid fees?")
    assert not result.blockers
    assert any("does not show whether it was paid" in u.description for u in result.uncertainties)
    assert "remains unpaid" not in render_answer(result)


def test_parsed_balance_wins_over_raw_amount_text():
    state = extract_partial_state(
        page("fees", rows=[{"name": "Permit balance", "amount": "$74.50", "balance": "$100.00",
                            "paid": False, "due": True}])
    )
    fee = state.fees[0]
    assert fee.amount_text == "$74.50"  # raw capture is preserved, not rewritten
    assert format_fee_amount(fee) == "$100.00"
    assert "$74.50" not in render_answer(understand(state, "Are there unpaid fees?"))


def test_zero_balance_is_not_an_outstanding_amount():
    state = extract_partial_state(
        page("fees", rows=[{"name": "Permit balance", "amount": "$0.00", "paid": False, "due": True}])
    )
    assert not understand(state, "Are there unpaid fees?").blockers


# --- A9: competing same-record observations ----------------------------------


def test_disagreeing_fee_reads_are_recorded_as_a_conflict():
    merged = merge_partial_states(
        extract_partial_state(page("fees", rows=[{"name": "Permit balance", "amount": "$74.50",
                                                  "paid": False, "due": True}])),
        extract_partial_state(page("fees", rows=[{"name": "Permit balance", "amount": "$74.50",
                                                  "paid": True, "due": False}])),
    )
    assert any("fee paid" in contradiction for contradiction in merged.contradictions)
    assert understand(merged, "Are there unpaid fees?").answerability == "conflicting"


def test_agreeing_reads_produce_no_conflict():
    merged = merge_partial_states(
        extract_partial_state(page("fees", rows=[{"name": "Permit balance", "amount": "$74.50",
                                                  "paid": False}])),
        extract_partial_state(page("fees", rows=[{"name": "Permit balance", "amount": "$74.50",
                                                  "paid": False}])),
    )
    assert merged.contradictions == []


# --- A11: a required document is missing only when the portal says so --------


def test_pending_required_document_is_an_uncertainty_not_a_blocker():
    state = extract_partial_state(
        page("documents", rows=[{"name": "Grading plan", "status": "Pending", "required": True}])
    )
    result = understand(state, "What is blocking approval?")
    assert not result.blockers
    assert any("whether it satisfies the requirement" in u.description for u in result.uncertainties)
    assert "not satisfied" not in render_answer(result)


def test_explicitly_missing_required_document_is_still_a_blocker():
    state = extract_partial_state(
        page("documents", rows=[{"name": "Grading plan", "status": "Missing", "required": True}])
    )
    assert blockers(state)["missing_required_document"]


# --- A12/A14: scoring and rendering cannot hide an unsupported gate ----------


def test_evaluator_scores_gate_classification_separately_from_type():
    state = extract_partial_state(
        page("fees", rows=[{"name": "Permit balance", "amount": "$74.50", "paid": False,
                            "due": True, "gate_text": "Pay before issuance"}])
    )
    report = score_cases([Phase3Case(
        "GATE", "What is blocking approval?", state,
        expected_blocker_types=("unpaid_fee",),
        forbidden_classifications=(("unpaid_fee", "confirmed_gate"),),
    )])
    assert report["gate_false_positives"] == 1
    assert report["passed"] == 0


def test_renderer_marks_disputed_facts_as_disputed():
    state = merge_partial_states(
        extract_partial_state(page("overview", fields={"record_number": "P", "status": "Issued"})),
        extract_partial_state(page("overview", fields={"record_number": "P", "status": "Expired"})),
    )
    result = understand(state, "What is the status?")
    assert result.answerability == "conflicting"
    assert "some are disputed" in render_answer(result)


# --- H01-H07: portal state extraction regressions (GLM, Phase 5) -------------
# Scope per the Phase 5 assignment: planner failures caused by confusing Accela
# state, not by planner reasoning. These run real page payloads through the ACA
# adapter and assert what the planner's gates actually receive.


def inspections_page(text):
    return accela_extract.inspections_observation(page("inspections", text=text))


def test_portal_mmdd_dates_normalize_to_iso_for_attempt_ordering():
    """H02: ACA renders MM/DD/YYYY; ordering logic consumes ISO dates only."""
    state = extract_partial_state(inspections_page(
        "Inspection | Status | Result | Completed Date\n"
        "Rough Electrical | Completed | Failed | 09/18/2026\n"
        "Rough Electrical | Completed | Passed | 09/20/2026"
    ))
    assert [i.completed_date for i in state.inspections] == ["2026-09-18", "2026-09-20"]


def test_later_pass_with_known_dates_establishes_order_when_scope_rendered():
    """H07: real dates + rendered scope must resolve, not block the planner."""
    state = extract_partial_state(inspections_page(
        "Inspection | Status | Result | Completed Date | Scope\n"
        "Rough Electrical | Completed | Failed | 09/18/2026 | Unit A\n"
        "Rough Electrical | Completed | Passed | 09/20/2026 | Unit A"
    ))
    result = understand(state, "get ready for next inspection")
    assert result.blockers == []
    assert not any(u.blocks_answer for u in result.uncertainties)


def test_unrendered_scope_names_the_missing_premise_not_fake_order():
    """H07: with dates known and scope absent, the uncertainty names scope."""
    state = extract_partial_state(inspections_page(
        "Inspection | Status | Result | Completed Date\n"
        "Rough Electrical | Completed | Failed | 09/18/2026\n"
        "Rough Electrical | Completed | Passed | 09/20/2026"
    ))
    result = understand(state, "get ready for next inspection")
    assert result.blockers, "failure stays open until scope is established"
    (uncertainty,) = [u for u in result.uncertainties if u.blocks_answer]
    assert "scope" in uncertainty.description
    assert "order is not established" not in uncertainty.description


def test_text_path_inspection_row_keeps_its_date():
    """H02b: the citizen detail's text rendering previously dropped the date."""
    observation = accela_extract.inspections_observation(page(
        "inspections", text="Rough Electrical | Insp Scheduled | 05-20-2026"
    ))
    (row,) = observation["rows"]
    assert row["scheduled_date"] == "2026-05-20"
    state = extract_partial_state(observation)
    assert state.inspections[0].scheduled_date == "2026-05-20"


def test_failure_in_status_column_is_not_lost():
    """H06: some agencies render the outcome in the status column."""
    state = extract_partial_state(inspections_page(
        "Inspection | Status | Completed Date\nRough Electrical | Failed | 09/18/2026"
    ))
    inspection = state.inspections[0]
    assert inspection.result_normalized == "FAILED"
    assert inspection.failed
    result = understand(state, "What is blocking this permit?")
    assert "failed_inspection" in {b.type for b in result.blockers}


def test_legend_line_does_not_fabricate_an_inspection_row():
    """H01: 'Scheduled | Completed | Failed' is a legend, not an attempt."""
    observation = accela_extract.inspections_observation(page(
        "inspections",
        text="Rough Electrical | Insp Scheduled | 05-20-2026\nScheduled | Completed | Failed",
    ))
    assert [r.get("type") for r in observation["rows"]] == ["Rough Electrical"]


def test_declared_empty_beside_rows_degrades_to_partial():
    """H05: a self-disputing page must not claim complete coverage."""
    observation = accela_extract.inspections_observation(page(
        "inspections",
        text="Inspections\nYou have not added any inspections.\nRough Electrical | Completed | Failed",
    ))
    assert observation["rows"], "validated rows are kept"
    assert observation["coverage"] == "partial"


def test_fee_status_wording_in_collection_reads_unpaid():
    """H03: agency wording must not silently void the payment status."""
    observation = accela_extract.fees_observation(page(
        "fees", text="Fee | Amount | Balance | Status\nPlan Check Fee | $200.00 | $200.00 | In Collection"
    ))
    fee = extract_partial_state(observation).fees[0]
    assert fee.paid is False and fee.due is True


def test_due_date_header_does_not_defeat_the_fees_table():
    """H04: a 'Due Date' column is a date, not a payment status."""
    observation = accela_extract.fees_observation(page(
        "fees", text="Fee | Amount | Due Date\nPlan Check Fee | $74.50 | 09/30/2026"
    ))
    (row,) = observation["rows"]
    assert row["description"] == "Plan Check Fee"
    assert row["amount"] == "$74.50"
    state = extract_partial_state(observation)
    assert state.fees[0].paid is None, "a due date does not establish payment state"


def test_blocks_answer_uncertainty_is_producible_by_the_deterministic_stack():
    """DeepSeek's Phase 5 handoff: the class must not be model-only.

    An unavailable section, unknown ordering, and a completed attempt with no
    recorded result each produce ``blocks_answer=True`` from the shipped rule
    engine — the Phase 5 gate (``reasoning_is_sound``) is reachable from real
    portal data, not only from injected reasoning results.
    """
    from licet.phase3.state import Coverage, CoverageStatus

    state = extract_partial_state(page("overview", fields={"record_number": "P", "status": "Issued"}))
    state.coverage["inspections"] = Coverage(status=CoverageStatus.UNAVAILABLE)
    result = understand(state, "get ready for next inspection")
    assert any(u.blocks_answer for u in result.uncertainties)

    ordered = extract_partial_state(inspections_page(
        "Inspection | Status | Result | Completed Date\n"
        "Rough Electrical | Completed | Failed | 09/18/2026\n"
        "Rough Electrical | Completed | Passed | 09/20/2026"
    ))
    assert any(u.blocks_answer for u in understand(ordered, "get ready for next inspection").uncertainties)
